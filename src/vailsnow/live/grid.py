"""The forecast grid: the Phase 1 terrain at 30 m, with everything that never changes from one
forecast to the next (elevation, zones, the PRISM ratio, where each cell sits in HRRR's grid)."""
from __future__ import annotations

import warnings

import numpy as np
import rasterio
from pyproj import Transformer

from ..fcst import hrrr
from ..grids import prism
from ..model import orographic as og
from ..model import zones
from ..resort import Resort, data_dir

BLOCK = 3                    # 10 m terrain cells per forecast cell


def block_mean(a: np.ndarray, f: int = BLOCK) -> np.ndarray:
    """Mean over f x f blocks, ignoring NaN. Edge rows that do not fill a block are dropped."""
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)             # blocks that are all NaN stay NaN, quietly
        return np.nanmean(a[:h, :w].reshape(h // f, f, w // f, f), axis=(1, 3))


def upsample(a: np.ndarray, shape: tuple[int, int], f: int = BLOCK) -> np.ndarray:
    """Repeat each cell f x f times and pad the dropped edge with NaN."""
    out = np.full(shape, np.nan, dtype=float)
    big = np.repeat(np.repeat(a, f, axis=0), f, axis=1)
    out[: big.shape[0], : big.shape[1]] = big
    return out


def static(resort: Resort) -> dict:
    """Arrays on the 30 m grid. Built once and cached."""
    path = data_dir() / "model" / "final" / "grid_static.npz"
    if path.exists():
        with np.load(path) as z:
            return {k: z[k] for k in z.files}
    d = resort.out_dir()
    with rasterio.open(d / "terrain_10m.tif") as ds:
        names = list(ds.descriptions)
        elev10 = ds.read(names.index("elevation") + 1).astype(float)
        tr, crs = ds.transform, ds.crs
    with rasterio.open(d / "zones_10m.tif") as ds:
        zone10, band10 = ds.read(1), ds.read(2)
    c = BLOCK // 2
    h, w = (elev10.shape[0] // BLOCK) * BLOCK, (elev10.shape[1] // BLOCK) * BLOCK
    zone, band = zone10[c:h:BLOCK, c:w:BLOCK], band10[c:h:BLOCK, c:w:BLOCK]
    elev = block_mean(elev10)
    rows, cols = np.mgrid[0: elev.shape[0], 0: elev.shape[1]]
    x = tr.c + (cols * BLOCK + BLOCK / 2) * tr.a
    y = tr.f + (rows * BLOCK + BLOCK / 2) * tr.e
    lon, lat = Transformer.from_crs(crs, 4326, always_xy=True).transform(x, y)
    # PRISM winter normal relative to its value at the gauge
    total, ptr, _ = prism.winter_normal(resort)
    pc, pr = ~ptr * (lon, lat)
    target = next(s for s in resort.snotel if s.get("target"))
    tc, trow = ~ptr * (target["lon"], target["lat"])
    at_gauge = og.sample(total.astype(float), np.array([trow - 0.5]), np.array([tc - 0.5]))[0]
    ratio = og.sample(total.astype(float), pr.ravel() - 0.5, pc.ravel() - 0.5).reshape(elev.shape) / at_gauge
    # fractional position of every cell in the 9 x 9 HRRR window around the gauge
    gr, gc = hrrr.window_cell(target["lat"], target["lon"])
    px, py = hrrr.PROJ(lon, lat)
    hrow = (py - hrrr.Y0) / hrrr.CELL - (hrrr.CHUNK_ROW * hrrr.CHUNK + hrrr.ROWS.start) - gr + zones.REACH
    hcol = (px - hrrr.X0) / hrrr.CELL - (hrrr.CHUNK_COL * hrrr.CHUNK + hrrr.COLS.start) - gc + zones.REACH
    out = {"elev": elev, "zone": zone, "band": band, "x": x, "y": y, "lon": lon, "lat": lat, "prism": ratio,
           "hrrr_row": hrow, "hrrr_col": hcol, "shape10": np.array(elev10.shape)}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, **out)
    return out


def lonlat_to_cell(g: dict, step: int = 1) -> list[float]:
    """Six numbers a, b, c, d, e, f with col = a*lon + b*lat + c and row = d*lon + e*lat + f on the
    grid thinned by `step`. The grid is in UTM, which is slightly rotated against longitude and
    latitude; over this small area a straight-line fit is good to a few metres."""
    lon, lat = g["lon"][::step, ::step], g["lat"][::step, ::step]
    rows, cols = np.mgrid[0: lon.shape[0], 0: lon.shape[1]]
    design = np.column_stack([lon.ravel(), lat.ravel(), np.ones(lon.size)])
    col_fit = np.linalg.lstsq(design, cols.ravel().astype(float), rcond=None)[0]
    row_fit = np.linalg.lstsq(design, rows.ravel().astype(float), rcond=None)[0]
    return [float(v) for v in (*col_fit, *row_fit)]
