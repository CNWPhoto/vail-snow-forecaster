"""Zone polygons from mapped trails.

Trails named in the resort config seed their zone. Every other cell inside the
served footprint joins the zone whose seeds are nearest over the ground, where
stepping onto a ridge crest or a main creek is expensive, so zone edges settle
on ridgelines and valley floors when one is close.
"""
from __future__ import annotations

import heapq
import json
from collections import Counter

import numpy as np
from pyproj import Transformer
from rasterio import features
from shapely.geometry import LineString, Polygon, mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union

from ..resort import FT_PER_M, Resort
from .grid import Grid


def load_osm(resort: Resort) -> dict:
    """Trails, lifts and the ski-area boundary in projected coordinates."""
    els = json.loads(resort.source_path("osm").read_text())["elements"]
    t = Transformer.from_crs(4326, resort.epsg, always_xy=True)

    def line(el):
        xs, ys = t.transform([p["lon"] for p in el["geometry"]], [p["lat"] for p in el["geometry"]])
        return LineString(list(zip(xs, ys)))

    trails, lifts, boundary = [], [], None
    for el in els:
        tags = el.get("tags", {})
        if el["type"] != "way" or len(el.get("geometry", [])) < 2:
            continue
        if tags.get("piste:type") == "downhill":
            trails.append({"id": el["id"], "name": tags.get("name"), "difficulty": tags.get("piste:difficulty"),
                           "gladed": tags.get("gladed") == "yes", "geom": line(el)})
        elif "aerialway" in tags:
            lifts.append({"id": el["id"], "name": tags.get("name"), "kind": tags["aerialway"], "geom": line(el)})
        elif tags.get("landuse") == "winter_sports" and tags.get("name") == resort.osm_boundary_name:
            boundary = Polygon(line(el).coords)
    if boundary is None:
        raise RuntimeError("ski-area boundary not found in the OpenStreetMap extract")
    return {"trails": trails, "lifts": lifts, "boundary": boundary}


def line_cells(geom: LineString, grid: Grid, step: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Core-grid cells a line passes through (only those inside the grid)."""
    step = step or grid.cell / 2
    n = max(2, int(np.ceil(geom.length / step)) + 1)
    pts = [geom.interpolate(d) for d in np.linspace(0, geom.length, n)]
    r, c = grid.core_rc([p.x for p in pts], [p.y for p in pts])
    h, w = grid.core_shape
    ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
    return r[ok], c[ok]


def seed_raster(resort: Resort, osm: dict, grid: Grid) -> tuple[np.ndarray, list[str]]:
    """Zone index at seed-trail cells, 0 elsewhere. Also returns config trail names not found in the map data."""
    by_name = {t: z.index for z in resort.zones for t in z.trails}
    seeds = np.zeros(grid.core_shape, dtype=np.int16)
    found = set()
    for tr in osm["trails"]:
        idx = by_name.get(tr["name"])
        if idx:
            r, c = line_cells(tr["geom"], grid)
            seeds[r, c] = idx
            found.add(tr["name"])
    return seeds, sorted(set(by_name) - found)


def grow_zones(seeds: np.ndarray, mask: np.ndarray, barrier: np.ndarray, cell: float, barrier_cost: float = 25.0) -> np.ndarray:
    """Give every cell in `mask` the zone of its nearest seed by path length over the grid,
    where stepping onto a `barrier` cell (a ridge crest or a main creek) costs `barrier_cost`
    times as much, so zone edges settle on ridges and valley floors when one is close."""
    h, w = seeds.shape
    zone = np.where(mask, seeds, 0).astype(np.int16).ravel().tolist()
    okl = mask.ravel().tolist()
    costl = np.where(barrier, barrier_cost, 1.0).ravel().tolist()
    dist = [float("inf")] * (h * w)
    heap = []
    for i in np.flatnonzero((seeds > 0) & mask).tolist():
        dist[i] = 0.0
        heap.append((0.0, i))
    heapq.heapify(heap)
    diag = cell * 2 ** 0.5
    steps = [(-1, 0, cell), (1, 0, cell), (0, -1, cell), (0, 1, cell), (-1, -1, diag), (-1, 1, diag), (1, -1, diag), (1, 1, diag)]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        r, c = divmod(i, w)
        zi = zone[i]
        for dr, dc, length in steps:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w:
                j = rr * w + cc
                if okl[j]:
                    nd = d + length * costl[j]
                    if nd < dist[j]:
                        dist[j] = nd
                        zone[j] = zi
                        heapq.heappush(heap, (nd, j))
    return np.asarray(zone, dtype=np.int16).reshape(h, w)


def band_raster(resort: Resort, z_core: np.ndarray) -> np.ndarray:
    """Elevation band index (1-based) for every cell."""
    tops = np.array([b.top_m for b in resort.bands])
    return (np.searchsorted(tops, z_core, side="right") + 1).clip(1, len(tops)).astype(np.int16)


def boundary_mask(osm: dict, grid: Grid) -> np.ndarray:
    return features.rasterize([(mapping(osm["boundary"]), 1)], out_shape=grid.core_shape,
                              transform=grid.core_transform, fill=0, dtype="uint8").astype(bool)


def footprint_mask(osm: dict, grid: Grid, buffer_m: float) -> np.ndarray:
    """Ground the resort actually serves: inside the boundary and within `buffer_m` of a mapped
    trail or lift, with enclosed gaps filled. The mapped boundary alone is the permit area,
    which takes in large tracts no lift reaches."""
    from scipy import ndimage as ndi
    lines = np.zeros(grid.core_shape, dtype=bool)
    for item in osm["trails"] + osm["lifts"]:
        r, c = line_cells(item["geom"], grid)
        lines[r, c] = True
    near = ndi.distance_transform_edt(~lines) * grid.cell <= buffer_m
    return ndi.binary_fill_holes(near) & boundary_mask(osm, grid)


def polygons(raster: np.ndarray, grid: Grid, simplify_m: float = 10.0) -> dict[int, object]:
    """One (multi)polygon per non-zero value of an integer raster, in projected coordinates."""
    parts: dict[int, list] = {}
    for geom, val in features.shapes(raster.astype(np.int32), mask=raster > 0, transform=grid.core_transform):
        parts.setdefault(int(val), []).append(shape(geom))
    return {v: unary_union(g).simplify(simplify_m) for v, g in parts.items()}


def to_wgs84(geom, epsg: int):
    t = Transformer.from_crs(epsg, 4326, always_xy=True)
    return shp_transform(lambda x, y, z=None: t.transform(x, y), geom)


QUADRANTS = ("north", "east", "south", "west")


def cell_stats(sel: np.ndarray, layers: dict[str, np.ndarray], cell: float) -> dict:
    """Summary of the terrain layers over the cells selected by `sel`."""
    n = int(sel.sum())
    if n == 0:
        return {"cells": 0}
    z = layers["elevation"][sel]
    asp = layers["aspect"][sel]
    quad = ((asp + 45.0) % 360.0 // 90.0).astype(int)
    shares = np.bincount(quad, minlength=4) / n
    cover = layers["canopy_cover"][sel]
    out = {
        "cells": n,
        "acres": round(n * cell * cell / 4046.8564, 1),
        "elev_min_ft": round(float(z.min()) * FT_PER_M),
        "elev_mean_ft": round(float(z.mean()) * FT_PER_M),
        "elev_max_ft": round(float(z.max()) * FT_PER_M),
        "slope_mean_deg": round(float(layers["slope"][sel].mean()), 1),
        "aspect_share": {q: round(float(s), 3) for q, s in zip(QUADRANTS, shares)},
        "canopy_cover": round(float(np.nanmean(cover)), 3) if np.isfinite(cover).any() else None,
        "sky_view": round(float(layers["sky_view"][sel].mean()), 3),
    }
    for key in ("sx_nw", "sx_w"):
        if key in layers:
            out[key + "_mean_deg"] = round(float(layers[key][sel].mean()), 2)
            out[key + "_exposed_share"] = round(float((layers[key][sel] < 0).mean()), 3)
    return out


def trail_landings(osm: dict, zone: np.ndarray, grid: Grid, resort: Resort) -> list[dict]:
    """For every mapped trail: the zone most of its length lies in, and the zone it seeds (if any)."""
    by_name = {t: z.id for z in resort.zones for t in z.trails}
    ids = {z.index: z.id for z in resort.zones}
    out = []
    for tr in osm["trails"]:
        r, c = line_cells(tr["geom"], grid)
        vals = [v for v in zone[r, c].tolist() if v > 0]
        landed = ids[Counter(vals).most_common(1)[0][0]] if vals else None
        out.append({"osm_id": tr["id"], "name": tr["name"], "difficulty": tr["difficulty"], "gladed": tr["gladed"],
                    "seeds": by_name.get(tr["name"]), "zone": landed, "geom": tr["geom"]})
    return out
