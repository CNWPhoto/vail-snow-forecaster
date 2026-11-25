"""Phase 1 pipeline: terrain grid, derived layers, wind shelter, zones, review-map files."""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from shapely.geometry import mapping

from ..resort import FT_PER_M, ROOT, Resort, load_resort
from . import derive, render, shelter, zones
from .grid import build_grid, canopy_layers

SX_DMAX = 300.0
WEB = ROOT / "web" / "check"

ZONE_COLOURS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666",
                "#1f78b4", "#b15928", "#6a3d9a", "#ff7f00", "#33a02c", "#e31a1c", "#a6cee3", "#fb9a99", "#cab2d6"]


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)


def _write_tif(path: Path, bands: dict[str, np.ndarray], transform, epsg: int, dtype: str, nodata=None, scales=None) -> None:
    first = next(iter(bands.values()))
    with rasterio.open(path, "w", driver="GTiff", height=first.shape[0], width=first.shape[1], count=len(bands),
                       dtype=dtype, crs=f"EPSG:{epsg}", transform=transform, compress="deflate", tiled=True,
                       blockxsize=256, blockysize=256, nodata=nodata, predictor=2 if dtype != "float32" else 3) as ds:
        for i, (name, arr) in enumerate(bands.items(), start=1):
            ds.write(arr.astype(dtype), i)
            ds.set_band_description(i, name)
        if scales:
            ds.scales = scales


def _fc(feats: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": feats}


def build(resort: Resort | None = None) -> dict:
    t0 = time.time()
    resort = resort or load_resort()
    out = resort.out_dir()

    grid = build_grid(resort)
    cell, core = grid.cell, grid.core
    _log(f"grid: core {grid.core_shape} at {cell:g} m, padded {grid.z.shape}", t0)

    slope, aspect = derive.slope_aspect(grid.z, cell)
    ridge = derive.ridge_mask(grid.z, cell)
    valley = derive.valley_mask(grid.z, cell)
    layers = {
        "elevation": grid.z[core],
        "slope": slope[core],
        "aspect": aspect[core],
        "curvature": derive.curvature(grid.z, cell)[core],
        "tpi_150m": derive.tpi(grid.z, cell, 150.0)[core],
        "tpi_500m": derive.tpi(grid.z, cell, 500.0)[core],
        "ridge_distance": derive.distance_to(ridge, cell)[core],
    }
    _log("slope, aspect, curvature, position index, ridges", t0)

    sx_all = {}
    for az in shelter.DIRECTIONS_16:
        sx_all[az] = shelter.sx(grid.z, cell, az, SX_DMAX)[core]
    layers["sx_nw"], layers["sx_w"] = sx_all[315.0], sx_all[270.0]
    _log("wind shelter (Sx) for 16 directions", t0)

    layers["sky_view"] = shelter.sky_view(grid.z, cell, reach=resort.fine["margin"])[core]
    _log("sky view", t0)

    layers["canopy_cover"], layers["canopy_height"] = canopy_layers(resort, grid)
    _log("canopy", t0)

    osm = zones.load_osm(resort)
    seeds, missing = zones.seed_raster(resort, osm, grid)
    inside = zones.footprint_mask(osm, grid, resort.fine["footprint_buffer"])
    zone = zones.grow_zones(seeds, inside, ridge[core] | valley[core], cell)
    band = zones.band_raster(resort, layers["elevation"])
    _log(f"zones grown ({int((zone > 0).sum())} cells in the served footprint)", t0)

    # ---- rasters -----------------------------------------------------------
    terrain_names = ["elevation", "slope", "aspect", "curvature", "tpi_150m", "tpi_500m", "ridge_distance",
                     "sky_view", "canopy_cover", "canopy_height"]
    _write_tif(out / "terrain_10m.tif", {k: layers[k] for k in terrain_names}, grid.core_transform, grid.epsg, "float32", nodata=np.nan)
    _write_tif(out / "sx_300m.tif", {f"sx_from_{az:05.1f}": np.round(sx_all[az] * 100) for az in shelter.DIRECTIONS_16},
               grid.core_transform, grid.epsg, "int16", scales=[0.01] * 16)
    _write_tif(out / "zones_10m.tif", {"zone": zone, "band": np.where(zone > 0, band, 0), "ridge": ridge[core].astype(np.int16)},
               grid.core_transform, grid.epsg, "int16", nodata=0)

    # ---- zone polygons and stats ------------------------------------------
    zpoly = zones.polygons(zone, grid)
    zstats, zfeats = {}, []
    for z, colour in zip(resort.zones, ZONE_COLOURS):
        sel = zone == z.index
        st = zones.cell_stats(sel, layers, cell)
        st["bands"] = {b.id: zones.cell_stats(sel & (band == b.index), layers, cell) for b in resort.bands}
        zstats[z.id] = {"name": z.name, "group": z.group, **st}
        if z.index in zpoly:
            props = {"id": z.id, "name": z.name, "group": z.group, "colour": colour, "lifts": list(z.lifts),
                     **{k: v for k, v in st.items() if k != "bands"}}
            zfeats.append({"type": "Feature", "properties": props, "geometry": mapping(zones.to_wgs84(zpoly[z.index], grid.epsg))})
    combo = np.where(zone > 0, zone * 10 + band, 0)
    bfeats = []
    names = {z.index: z for z in resort.zones}
    for val, geom in zones.polygons(combo, grid).items():
        z, b = names[val // 10], resort.bands[val % 10 - 1]
        st = zstats[z.id]["bands"][b.id]
        bfeats.append({"type": "Feature", "properties": {"zone": z.id, "zone_name": z.name, "band": b.id, "band_label": b.label, **st},
                       "geometry": mapping(zones.to_wgs84(geom, grid.epsg))})
    resort_sel = zone > 0
    summary = {
        "resort": resort.name, "cell_m": cell, "built": time.strftime("%Y-%m-%d %H:%M"),
        "whole_resort": zones.cell_stats(resort_sel, layers, cell),
        "groups": {g: zones.cell_stats(np.isin(zone, [z.index for z in resort.zones if z.group == g]), layers, cell)
                   for g in dict.fromkeys(z.group for z in resort.zones)},
        "zones": zstats, "seed_trails_not_in_map": missing,
    }

    landings = zones.trail_landings(osm, zone, grid, resort)
    tfeats = [{"type": "Feature", "properties": {k: v for k, v in t.items() if k != "geom"},
               "geometry": mapping(zones.to_wgs84(t["geom"], grid.epsg))} for t in landings]
    lfeats = [{"type": "Feature", "properties": {"name": l["name"], "kind": l["kind"]},
               "geometry": mapping(zones.to_wgs84(l["geom"], grid.epsg))} for l in osm["lifts"]]
    t84 = Transformer.from_crs(4326, grid.epsg, always_xy=True)
    pfeats = []
    for p in resort.points:
        x, y = t84.transform(p["lon"], p["lat"])
        r, c = grid.core_rc(x, y)
        h, w = grid.core_shape
        on = 0 <= r < h and 0 <= c < w
        props = {**p, "on_fine_grid": bool(on)}
        if on:
            props.update(grid_elev_ft=round(float(layers["elevation"][r, c]) * FT_PER_M), zone=(names[int(zone[r, c])].id if zone[r, c] else None),
                         slope_deg=round(float(layers["slope"][r, c]), 1), aspect_deg=round(float(layers["aspect"][r, c])),
                         canopy_cover=round(float(layers["canopy_cover"][r, c]), 2), sx_nw_deg=round(float(layers["sx_nw"][r, c]), 1))
        pfeats.append({"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}})
    summary["points"] = [f["properties"] for f in pfeats]

    boundary = {"type": "Feature", "properties": {"name": resort.name}, "geometry": mapping(zones.to_wgs84(osm["boundary"], grid.epsg))}
    files = {"zones": _fc(zfeats), "zone_bands": _fc(bfeats), "trails": _fc(tfeats), "lifts": _fc(lfeats),
             "points": _fc(pfeats), "boundary": _fc([boundary])}
    for name, fc in files.items():
        (out / f"{name}.geojson").write_text(json.dumps(fc))
    (out / "zone_stats.json").write_text(json.dumps(summary, indent=1))
    _log("rasters, polygons and stats written", t0)

    # ---- review-map layers -------------------------------------------------
    (WEB / "data").mkdir(parents=True, exist_ok=True)
    for name, fc in files.items():
        (WEB / "data" / f"{name}.geojson").write_text(json.dumps(fc))
    (WEB / "data" / "zone_stats.json").write_text(json.dumps(summary))
    shade = derive.hillshade(grid.z, cell)[core]
    L = WEB / "layers"
    render.save_png(render.ramp(shade, [(0.0, (30, 34, 42)), (0.55, (150, 152, 156)), (1.0, (252, 252, 250))]), L / "hillshade.png")
    render.save_png(render.ramp(layers["slope"], [(0, (255, 255, 229)), (15, (254, 227, 145)), (25, (254, 153, 41)), (35, (204, 76, 2)), (50, (102, 37, 6))], 200), L / "slope.png")
    render.save_png(render.ramp(layers["aspect"], [(0, (44, 123, 182)), (90, (171, 217, 233)), (180, (215, 25, 28)), (270, (253, 174, 97)), (360, (44, 123, 182))], 190,
                                transparent=layers["slope"] < 3), L / "aspect.png")
    render.save_png(render.ramp(layers["canopy_cover"], [(0.0, (247, 252, 245)), (0.3, (161, 217, 155)), (0.7, (35, 139, 69)), (1.0, (0, 68, 27))], 200,
                                transparent=np.nan_to_num(layers["canopy_cover"]) < 0.05), L / "canopy.png")
    sx_stops = [(-25, (140, 81, 10)), (-8, (223, 194, 125)), (0, (245, 245, 245)), (8, (128, 205, 193)), (25, (1, 102, 94))]
    for key in ("sx_nw", "sx_w"):
        render.save_png(render.ramp(layers[key], sx_stops, 205), L / f"{key}.png")
    render.save_png(render.ramp(layers["sky_view"], [(0.6, (37, 52, 148)), (0.85, (127, 205, 187)), (1.0, (255, 255, 217))], 200), L / "sky_view.png")
    band_cols = {1: (255, 255, 204), 2: (161, 218, 180), 3: (65, 182, 196), 4: (34, 94, 168)}
    render.save_png(render.classes(band, band_cols, 170), L / "bands.png")
    render.save_png(render.classes(ridge[core].astype(int), {1: (20, 20, 20)}, 255), L / "ridges.png")
    x0, y0, x1, y1 = grid.core_bounds()
    to84 = Transformer.from_crs(grid.epsg, 4326, always_xy=True)
    corners = [list(to84.transform(x, y)) for x, y in ((x0, y1), (x1, y1), (x1, y0), (x0, y0))]
    manifest = {"corners": corners, "cell_m": cell, "shape": list(grid.core_shape), "built": summary["built"],
                "bands": [{"label": b.label, "colour": "#%02x%02x%02x" % band_cols[b.index]} for b in resort.bands]}
    (WEB / "data" / "manifest.json").write_text(json.dumps(manifest))
    _log("review-map layers written", t0)
    return summary
