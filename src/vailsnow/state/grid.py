"""The grid the mountain state lives on, shared with the ski game, and everything about the
ground that never changes from hour to hour.

The game's Vail level is a 2 m height grid in NAD83 / UTM zone 13N. The state grid is that same
box cut into 16 m cells: one state cell is 8 x 8 game cells, and a game position (x east, z south,
in metres from the north-west corner) lands in the state texture at u = x / width, v = z / height.
Row 0 is the north edge and column 0 the west edge, as everywhere else in this project.

The forecast's own 10 m grid is not reused for this: it sits 1 m off the game's box and stops
short of its south edge. The static layers here are cut from the game's 2 m arrays instead, and
the forecast's fields (PRISM ratio, position in the HRRR grid, wind shelter) are resampled on.
"""
from __future__ import annotations

import json

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from scipy import ndimage as ndi
from shapely.geometry import LineString, Polygon, mapping

from ..resort import Resort, data_dir
from ..terrain import derive
from ..terrain.shelter import DIRECTIONS_16

SCHEMA = 1
SX_STEP = 0.25                # shelter angles are stored in quarter degrees
CLASSES = ("out_of_bounds", "groomed", "run", "bowl", "glade", "trees")
CLASS_LABELS = {"out_of_bounds": "Outside the ski-area boundary", "groomed": "Groomed run", "run": "Ungroomed run",
                "bowl": "Open bowl", "glade": "Glade", "trees": "Tight trees"}
TREE = 0.5                    # a 2 m game cell with at least this much canopy is under trees
OPEN_BELOW, TIGHT_FROM = 0.25, 0.6    # share of a state cell under trees: below the first it is open ground, from the second tight trees, between them a glade

# How each layer is stored: (dtype, scale). Stored value = round(real value * scale).
PACK = {"slope": ("uint8", 2.0), "aspect": ("uint8", 256.0 / 360.0), "canopy": ("uint8", 255.0), "tree_share": ("uint8", 255.0),
        "groomed": ("uint8", 255.0), "run": ("uint8", 255.0), "glade_run": ("uint8", 255.0), "height": ("uint8", 255.0),
        "d_run": ("uint16", 1.0), "d_top": ("uint16", 1.0), "ridge_distance": ("uint16", 1.0),
        "prism": ("float16", 1.0), "hrrr_row": ("float16", 1.0), "hrrr_col": ("float16", 1.0), "sx": ("int8", 1.0 / SX_STEP)}


def spec(resort: Resort) -> dict:
    """Bounds and size of the state grid. Read from the built static file when there is one (the
    scheduled jobs have no copy of the game); otherwise worked out from the game's own grid."""
    saved = static_path(resort).with_suffix(".json")
    if saved.exists():
        return json.loads(saved.read_text())
    fine, cell = resort.fine, float(resort.state["cell"])
    src = float(fine["source_cell"])
    nodes = np.load(resort.source_path("heights"), mmap_mode="r").shape
    f = int(round(cell / src))
    rows, cols = (nodes[0] - 1) // f, (nodes[1] - 1) // f
    if (nodes[0] - 1) % f or (nodes[1] - 1) % f:
        raise ValueError(f"the game's grid ({nodes[1] - 1} x {nodes[0] - 1} cells of {src} m) does not divide into {cell} m cells")
    west, north = float(fine["source_e0"]), float(fine["source_n_top"])
    return {"crs": f"EPSG:{resort.epsg}", "cell_m": cell, "width": cols, "height": rows,
            "west": west, "north": north, "east": west + cols * cell, "south": north - rows * cell, "game_cells_per_cell": f}


def static_path(resort: Resort):
    return data_dir() / "state" / f"{resort.id}_static.npz"


def _osm(resort: Resort) -> dict:
    """Runs (with the tags the survival model needs), lift top stations and the boundary, projected."""
    els = json.loads(resort.source_path("osm").read_text())["elements"]
    t = Transformer.from_crs(4326, resort.epsg, always_xy=True)

    def line(el):
        xs, ys = t.transform([p["lon"] for p in el["geometry"]], [p["lat"] for p in el["geometry"]])
        return LineString(list(zip(xs, ys)))

    runs, lifts, boundary = [], [], None
    for el in els:
        tags = el.get("tags", {})
        if el["type"] != "way" or len(el.get("geometry", [])) < 2:
            continue
        if tags.get("piste:type") == "downhill":
            name = tags.get("name") or ""
            runs.append({"name": name, "difficulty": tags.get("piste:difficulty"), "grooming": tags.get("piste:grooming"),
                         "glade": tags.get("gladed") == "yes" or "glade" in name.lower(), "geom": line(el)})
        elif tags.get("aerialway") in ("chair_lift", "gondola", "t-bar", "platter", "drag_lift", "mixed_lift"):
            lifts.append({"name": tags.get("name"), "geom": line(el)})
        elif tags.get("landuse") == "winter_sports" and tags.get("name") == resort.osm_boundary_name:
            boundary = Polygon(line(el).coords)
    if boundary is None:
        raise RuntimeError("ski-area boundary not found in the OpenStreetMap extract")
    return {"runs": runs, "lifts": lifts, "boundary": boundary}


def _block(a: np.ndarray, f: int) -> np.ndarray:
    h, w = a.shape[0] // f, a.shape[1] // f
    return a[: h * f, : w * f].reshape(h, f, w, f).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def run_kind(run: dict, groomed_difficulty: list[str]) -> str:
    """Which runs are groomed is not published anywhere this project may read, and only a handful
    of mapped runs carry a grooming tag. So: a run tagged as groomed, or any green or blue run, is
    taken as groomed every night; glades are glades; everything else is an ungroomed run."""
    if run["glade"]:
        return "glade"
    if run["grooming"] in ("mogul", "backcountry"):
        return "run"
    if run["grooming"] == "classic" or run["difficulty"] in groomed_difficulty:
        return "groomed"
    return "run"


def build(resort: Resort) -> dict:
    """Cut the static layers and save them. Needs the game's data folder and the Phase 1 terrain build."""
    from ..live import grid as forecast_grid

    s = spec(resort)
    f, cell, rows, cols = s["game_cells_per_cell"], s["cell_m"], s["height"], s["width"]
    src = float(resort.fine["source_cell"])
    fine_tr = from_origin(s["west"], s["north"], src, src)
    tr = from_origin(s["west"], s["north"], cell, cell)
    shape, fine_shape = (rows, cols), (rows * f, cols * f)
    surv = resort.state["survival"]

    nodes = np.load(resort.source_path("heights"), mmap_mode="r")
    z2 = 0.25 * (nodes[:-1, :-1] + nodes[1:, :-1] + nodes[:-1, 1:] + nodes[1:, 1:])          # game cells sit between height nodes
    elev = _block(z2, f)
    del z2
    slope_here, aspect = derive.slope_aspect(elev, cell)

    masks = np.load(resort.source_path("heights").with_name("masks.npy"), mmap_mode="r")
    canopy2 = np.maximum(masks[0], masks[3] if masks.shape[0] > 3 else 0).astype(np.float32) / 255.0      # conifer or aspen: both are trees in the game
    tree2 = canopy2 >= TREE
    tree_share = _block(tree2.astype(np.float32), f)
    canopy = _block(canopy2, f)
    del canopy2

    osm = _osm(resort)
    kinds = [run_kind(r, surv["groomed_difficulty"]) for r in osm["runs"]]

    def burn(kind: str, width: float) -> np.ndarray:
        shapes = [(mapping(r["geom"].buffer(width / 2.0, cap_style="flat")), 1) for r, k in zip(osm["runs"], kinds) if k == kind]
        if not shapes:
            return np.zeros(fine_shape, dtype=bool)
        return features.rasterize(shapes, out_shape=fine_shape, transform=fine_tr, fill=0, dtype="uint8").astype(bool)

    open2 = ~tree2
    groomed2 = burn("groomed", surv["groomed_width_m"]) & open2
    run2 = burn("run", surv["run_width_m"]) & open2 & ~groomed2
    glade2 = burn("glade", surv["run_width_m"])
    open_share = np.maximum(1.0 - tree_share, 1e-6)
    groomed = np.clip(_block(groomed2.astype(np.float32), f) / open_share, 0, 1) * (tree_share < 0.999)
    run = np.clip(_block(run2.astype(np.float32), f) / open_share, 0, 1) * (tree_share < 0.999)
    glade_run = _block(glade2.astype(np.float32), f)
    del groomed2, run2, glade2, open2, tree2

    inside = features.rasterize([(mapping(osm["boundary"]), 1)], out_shape=shape, transform=tr, fill=0, dtype="uint8").astype(bool)
    lines = features.rasterize([(mapping(r["geom"]), 1) for r in osm["runs"]], out_shape=shape, transform=tr, fill=0, dtype="uint8", all_touched=True).astype(bool)
    d_run = (ndi.distance_transform_edt(~lines) * cell).astype(np.float32)
    tops = np.zeros(shape, dtype=bool)
    for lift in osm["lifts"]:
        ends = [lift["geom"].coords[0], lift["geom"].coords[-1]]
        rc = [(int((s["north"] - y) // cell), int((x - s["west"]) // cell)) for x, y in ends]
        rc = [(r, c) for r, c in rc if 0 <= r < rows and 0 <= c < cols]
        if rc:
            tops[max(rc, key=lambda p: elev[p])] = True
    d_top = (ndi.distance_transform_edt(~tops) * cell).astype(np.float32)

    # layers already built on the forecast's 10 m grid
    d = resort.out_dir()

    def warp(path, band: int, how=Resampling.average) -> np.ndarray:
        out = np.zeros(shape, dtype=np.float32)
        with rasterio.open(path) as ds:
            reproject(rasterio.band(ds, band), out, dst_transform=tr, dst_crs=ds.crs, resampling=how)
        return out

    with rasterio.open(d / "terrain_10m.tif") as ds:
        names = list(ds.descriptions)
    slope = warp(d / "terrain_10m.tif", names.index("slope") + 1)
    slope = np.where(slope > 0, slope, slope_here)                 # the last few metres at the south edge lie outside the 10 m grid
    ridge = warp(d / "terrain_10m.tif", names.index("ridge_distance") + 1)
    zone = warp(d / "zones_10m.tif", 1, Resampling.mode).astype(np.uint8)
    sx = np.stack([warp(d / "sx_300m.tif", k + 1, Resampling.bilinear) / 100.0 for k in range(len(DIRECTIONS_16))])
    in_zone = zone > 0
    zmin, zmax = float(elev[in_zone].min()), float(elev[in_zone].max())
    height = np.clip((elev - zmin) / (zmax - zmin), 0.0, 1.0)

    # the forecast grid's fields, read at each state cell centre
    g = forecast_grid.static(resort)
    x = s["west"] + (np.arange(cols) + 0.5) * cell
    y = s["north"] - (np.arange(rows) + 0.5) * cell
    gc = (x - g["x"][0, 0]) / (g["x"][0, 1] - g["x"][0, 0])
    gr = (y - g["y"][0, 0]) / (g["y"][1, 0] - g["y"][0, 0])
    coords = np.meshgrid(gr, gc, indexing="ij")

    def on(field: np.ndarray) -> np.ndarray:
        return ndi.map_coordinates(np.asarray(field, float), coords, order=1, mode="nearest").astype(np.float32)

    lon, lat = Transformer.from_crs(resort.epsg, 4326, always_xy=True).transform(*np.meshgrid(x, y))

    # terrain class, for labels and the review map (the survival rates use the shares directly)
    cls = np.full(shape, CLASSES.index("bowl"), dtype=np.uint8)
    is_open = tree_share < OPEN_BELOW
    cls[~is_open] = np.where((tree_share[~is_open] < TIGHT_FROM) | (glade_run[~is_open] > 0.3), CLASSES.index("glade"), CLASSES.index("trees"))
    cls[is_open & (run >= 0.5)] = CLASSES.index("run")
    cls[is_open & (groomed >= 0.5)] = CLASSES.index("groomed")
    cls[~inside] = CLASSES.index("out_of_bounds")

    out = {"elev": elev, "slope": slope, "aspect": aspect % 360.0, "canopy": canopy, "tree_share": tree_share, "groomed": groomed,
           "run": run, "glade_run": glade_run, "inside": inside, "d_run": np.minimum(d_run, 65535), "d_top": np.minimum(d_top, 65535),
           "ridge_distance": np.minimum(ridge, 65535), "height": height, "zone": zone, "class": cls,
           "sx": np.clip(sx, -127 * SX_STEP, 127 * SX_STEP),
           "prism": on(g["prism"]), "hrrr_row": on(g["hrrr_row"]), "hrrr_col": on(g["hrrr_col"]),
           "lat": np.float32(lat.mean()), "lon": np.float32(lon.mean())}
    for k, (dtype, scale) in PACK.items():
        v = np.asarray(out[k], np.float64) * scale
        out[k] = (v if dtype.startswith("float") else np.round(v) % 256 if k == "aspect" else np.round(v)).astype(dtype)
    path = static_path(resort)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    path.with_suffix(".json").write_text(json.dumps(s))
    share = {CLASSES[k]: round(float((cls[inside] == k).mean()), 3) for k in range(1, len(CLASSES))}
    return {"grid": s, "file": str(path), "mb": round(path.stat().st_size / 1e6, 1), "inside_share_by_class": share,
            "groomed_runs": sorted({r["name"] for r, k in zip(osm["runs"], kinds) if k == "groomed" and r["name"]}),
            "ungroomed_runs": sorted({r["name"] for r, k in zip(osm["runs"], kinds) if k == "run" and r["name"]}),
            "glades": sorted({r["name"] for r, k in zip(osm["runs"], kinds) if k == "glade" and r["name"]})}


def load(resort: Resort) -> dict:
    """The static layers as float arrays in real units (shelter angles in degrees, one layer per
    stored wind direction)."""
    with np.load(static_path(resort)) as z:
        out = {k: z[k] for k in z.files}
    for k, (_, scale) in PACK.items():
        out[k] = out[k].astype(np.float32) / np.float32(scale)
    return out


def gauge_cell(resort: Resort) -> tuple[int, int]:
    """(row, col) of the state cell holding the reference gauge."""
    target = next(g for g in resort.snotel if g.get("target"))
    s = spec(resort)
    x, y = Transformer.from_crs(4326, resort.epsg, always_xy=True).transform(target["lon"], target["lat"])
    return int((s["north"] - y) // s["cell_m"]), int((x - s["west"]) // s["cell_m"])


def shelter_gauge(layers: dict, rc: tuple[int, int], reach: int = 1) -> dict:
    """Switch the wind rules off in the few cells around the gauge.

    The wind-loading rule puts the gauge's own clearing in the lee of a ridge and would give it
    about 1.3 times what the gauge measures. But the gauge is the reference everything is scaled
    from, and the loading rule itself was tuned against "what the mountain reports" there. So the
    cells around it are treated as the sheltered clearing a snow gauge is sited in: no loading, no scouring."""
    r, c = rc
    sel = (slice(max(r - reach, 0), r + reach + 1), slice(max(c - reach, 0), c + reach + 1))
    layers["sx"] = layers["sx"].copy()
    layers["sx"][(slice(None), *sel)] = 0.0
    return layers


def grid_json(resort: Resort) -> dict:
    """What the game needs to line the textures up with its terrain."""
    s = json.loads(static_path(resort).with_suffix(".json").read_text())
    w, h = s["east"] - s["west"], s["north"] - s["south"]
    return {"schema": SCHEMA, "resort": resort.id, "crs": s["crs"],
            "bounds": {k: s[k] for k in ("west", "south", "east", "north")},
            "width": s["width"], "height": s["height"], "cell_m": s["cell_m"],
            "origin": "Row 0 is the north edge and column 0 the west edge. Texel (col, row) covers "
                      "west + col*cell to west + (col+1)*cell and north - row*cell down to north - (row+1)*cell.",
            "game": {"E0": s["west"], "N_TOP": s["north"], "width_m": w, "height_m": h, "game_cells_per_state_cell": s["game_cells_per_cell"],
                     "lookup": f"u = x / {w:g}, v = z / {h:g}, with x metres east of E0 and z metres south of N_TOP. "
                               "Upload without flipping (in three.js: flipY = false), with no colour-space conversion and no premultiplied alpha."},
            "zones": [{"index": z.index, "id": z.id, "name": z.name, "group": z.group} for z in resort.zones],
            "classes": [{"index": k, "id": c, "label": CLASS_LABELS[c]} for k, c in enumerate(CLASSES)],
            "credits": "Elevation and tree canopy: U.S. Geological Survey 3D Elevation Program. Runs, lifts and boundary: "
                       "(c) OpenStreetMap contributors (ODbL). A modelled estimate, not an official report; not affiliated with Vail Resorts."}
