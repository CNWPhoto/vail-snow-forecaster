"""PRISM 800 m precipitation normals (PRISM Climate Group, Oregon State University,
https://prism.oregonstate.edu). A statistical map of long-term average precipitation,
built from stations and terrain. It is a model of where precipitation falls on average,
not a measurement at any one cell, and it is used here as the climatological spatial prior.
"""
from __future__ import annotations

import json
import zipfile

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
from rasterio.windows import from_bounds

from ..fetch import cached_get
from ..resort import FT_PER_M, Resort, cache_dir, data_dir, load_resort

URL = "https://data.prism.oregonstate.edu/normals/us/800m/ppt/monthly/prism_ppt_us_30s_2020{month:02d}_avg_30y.zip"
WINTER = (11, 12, 1, 2, 3, 4)


def month_normal(resort: Resort, month: int) -> tuple[np.ndarray, rasterio.Affine, str]:
    """Normal precipitation (mm) for one month over the resort's regional box."""
    z = cached_get(URL.format(month=month), cache_dir() / "prism" / f"ppt_normal_{month:02d}.zip", timeout=600)
    tif = next(n for n in zipfile.ZipFile(z).namelist() if n.lower().endswith(".tif"))
    r = resort.region
    with rasterio.open(f"/vsizip/{z}/{tif}") as ds:
        win = from_bounds(r["west"], r["south"], r["east"], r["north"], ds.transform).round_offsets().round_lengths()
        return ds.read(1, window=win).astype(np.float32), ds.window_transform(win), ds.crs.to_string()


def winter_normal(resort: Resort) -> tuple[np.ndarray, rasterio.Affine, str]:
    """November to April normal precipitation (mm), saved as a GeoTIFF."""
    total, tr, crs = None, None, None
    for m in WINTER:
        a, tr, crs = month_normal(resort, m)
        total = a if total is None else total + a
    out = data_dir() / "prism"
    out.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out / f"{resort.id}_winter_ppt_normal_mm.tif", "w", driver="GTiff", height=total.shape[0], width=total.shape[1],
                       count=1, dtype="float32", crs=crs, transform=tr, compress="deflate") as ds:
        ds.write(total, 1)
    return total, tr, crs


def zone_table(resort: Resort | None = None) -> dict:
    """Winter normal precipitation by zone and elevation band, relative to the SNOTEL site's cell."""
    resort = resort or load_resort()
    total, tr, crs = winter_normal(resort)
    tdir = resort.out_dir()
    with rasterio.open(tdir / "zones_10m.tif") as zs, rasterio.open(tdir / "terrain_10m.tif") as ts:
        zone, band = zs.read(1), zs.read(2)
        elev = ts.read(1)
        on_grid = np.full(zone.shape, np.nan, dtype=np.float32)
        reproject(total, on_grid, src_transform=tr, src_crs=crs, dst_transform=zs.transform, dst_crs=zs.crs,
                  resampling=Resampling.bilinear, dst_nodata=np.nan)
        from pyproj import Transformer
        t = Transformer.from_crs(4326, zs.crs, always_xy=True)
        sn = next(s for s in resort.points if s["id"] == "snotel_842")
        r, c = zs.index(*t.transform(sn["lon"], sn["lat"]))
    ref = float(on_grid[r, c])
    rows = {}
    for z in resort.zones:
        sel = zone == z.index
        rows[z.id] = {"name": z.name, "group": z.group, "winter_ppt_in": round(float(np.nanmean(on_grid[sel])) / 25.4, 1),
                      "vs_snotel_cell": round(float(np.nanmean(on_grid[sel])) / ref, 2),
                      "elev_mean_ft": round(float(elev[sel].mean()) * FT_PER_M),
                      "bands": {b.id: round(float(np.nanmean(on_grid[sel & (band == b.index)])) / ref, 2)
                                for b in resort.bands if (sel & (band == b.index)).sum() > 50}}
    # how much of the zone-to-zone spread is just elevation
    inside = zone > 0
    e, p = elev[inside].astype(np.float64), on_grid[inside].astype(np.float64)
    e0 = e.mean()
    slope, intercept = np.polyfit(e - e0, p, 1)
    trend = lambda x: slope * (x.astype(np.float64) - e0) + intercept
    r2 = 1.0 - float(np.var(p - trend(e)) / np.var(p))
    for z in resort.zones:
        sel = zone == z.index
        rows[z.id]["vs_elevation_trend"] = round(float(np.nanmean(on_grid[sel] - trend(elev[sel]))) / ref, 3)
    out = {"snotel_cell_winter_ppt_in": round(ref / 25.4, 1), "elevation_r2": round(r2, 2),
           "gain_per_1000ft_pct_of_snotel": round(float(slope) * 304.8 / ref * 100, 1), "zones": rows,
           "source": "PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu, 1991-2020 normals, 800 m"}
    (data_dir() / "prism" / f"{resort.id}_zone_prior.json").write_text(json.dumps(out, indent=1))
    return out
