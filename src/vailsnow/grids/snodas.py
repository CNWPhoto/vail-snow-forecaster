"""SNODAS daily snow water equivalent (NOAA NOHRSC, archived at NSIDC as data set G02158).

SNODAS is a 1 km snow model that takes in station and satellite observations. It is used
here to see how the model spreads a storm's snow across the area, as a spatial pattern
to compare with other evidence, never as a measurement.

Each daily file is the state at 06 UTC, about two hours before the SNOTEL (Pacific
Standard Time) day of the same date begins.
"""
from __future__ import annotations

import gzip
import tarfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from ..fetch import cached_get
from ..resort import Resort, cache_dir, data_dir

URL = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked/{y}/{m:02d}_{mon}/SNODAS_{y}{m:02d}{d:02d}.tar"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FIRST = pd.Timestamp("2003-09-30")
NROWS, NCOLS, CELL = 3351, 6935, 1.0 / 120.0
GRID_SHIFT = pd.Timestamp("2013-10-01")          # the grid origin moved by a fraction of a cell on this date
SWE_CODE = "1034"


def origin(date: pd.Timestamp) -> tuple[float, float]:
    """(west edge, north edge) of the masked grid in degrees."""
    return (-124.733333333333, 52.875) if date >= GRID_SHIFT else (-124.733749999999, 52.874583333332)


def window(resort: Resort, date: pd.Timestamp) -> tuple[slice, slice, float, float]:
    """Row and column slices covering the resort's regional box, and the window's west and north edges."""
    west, north = origin(date)
    r = resort.region
    c0, c1 = int(np.floor((r["west"] - west) / CELL)), int(np.ceil((r["east"] - west) / CELL))
    r0, r1 = int(np.floor((north - r["north"]) / CELL)), int(np.ceil((north - r["south"]) / CELL))
    return slice(r0, r1), slice(c0, c1), west + c0 * CELL, north - r0 * CELL


def tar_path(date: pd.Timestamp):
    return cache_dir() / "snodas" / f"SNODAS_{date:%Y%m%d}.tar"


def fetch(date: pd.Timestamp):
    return cached_get(URL.format(y=date.year, m=date.month, mon=MONTHS[date.month - 1], d=date.day), tar_path(date), timeout=600)


def read_swe(resort: Resort, date: pd.Timestamp) -> np.ndarray:
    """Snow water equivalent (inches) over the regional box on one date; NaN where there is no data."""
    rows, cols, _, _ = window(resort, date)
    with tarfile.open(fetch(date)) as tar:
        name = next(n for n in tar.getnames() if f"ssmv1{SWE_CODE}tS" in n and n.endswith(".dat.gz"))
        raw = gzip.decompress(tar.extractfile(name).read())
    grid = np.frombuffer(raw, dtype=">i2").reshape(NROWS, NCOLS)[rows, cols].astype(np.float32)
    grid[grid < 0] = np.nan
    return grid / 25.4                      # stored in millimetres


DEPTH_CODE = "1036"


def read_depth(resort: Resort, date: pd.Timestamp) -> np.ndarray:
    """Snow depth (inches) over the regional box on one date; NaN where there is no data."""
    rows, cols, _, _ = window(resort, date)
    with tarfile.open(fetch(date)) as tar:
        name = next(n for n in tar.getnames() if f"ssmv1{DEPTH_CODE}tS" in n and n.endswith(".dat.gz"))
        raw = gzip.decompress(tar.extractfile(name).read())
    grid = np.frombuffer(raw, dtype=">i2").reshape(NROWS, NCOLS)[rows, cols].astype(np.float32)
    grid[grid < 0] = np.nan
    return grid / 25.4                      # stored in millimetres


def live_path(date: pd.Timestamp):
    return cache_dir() / "snodas_live" / f"depth_{date:%Y%m%d}.npy"


def live_depth(resort: Resort, date: pd.Timestamp) -> np.ndarray:
    """Depth over the regional box for a recent date, keeping only that small window on disk.
    The daily file is a 15 to 25 MB archive of the whole country; kept every day it would add
    about 6 GB a year, so it is read once and deleted."""
    small = live_path(date)
    if small.exists():
        return np.load(small)
    had = tar_path(date).exists()               # a date already in the history cache keeps its archive
    grid = read_depth(resort, date)
    small.parent.mkdir(parents=True, exist_ok=True)
    np.save(small, grid)
    if not had:
        tar_path(date).unlink(missing_ok=True)
    return grid


def latest(resort: Resort, now: pd.Timestamp, back: int = 6) -> tuple[pd.Timestamp, np.ndarray]:
    """The newest daily depth grid published, looking back up to `back` days."""
    for k in range(back + 1):
        date = now.normalize() - pd.Timedelta(days=k)
        try:
            return date, live_depth(resort, date)
        except Exception:                   # not published yet (the archive runs a day or two behind)
            continue
    raise RuntimeError("no SNODAS file in the last week")


def sample(grid: np.ndarray, resort: Resort, date: pd.Timestamp, lat: float, lon: float) -> float:
    _, _, west, north = window(resort, date)
    r, c = int((north - lat) / CELL), int((lon - west) / CELL)
    return float(grid[r, c])


def event_dates(events: pd.DataFrame) -> list[pd.Timestamp]:
    """The start and day-after-end snapshot dates for every event the archive covers."""
    out = set()
    for s, e in zip(events["start"], events["end"]):
        if s >= FIRST:
            out.update([s, e + pd.Timedelta(days=1)])
    return sorted(out)


def fetch_all(events: pd.DataFrame, workers: int = 4) -> int:
    dates = [d for d in event_dates(events) if not tar_path(d).exists()]
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(fetch, dates))
    return len(dates)


def event_change(resort: Resort, start: pd.Timestamp, end: pd.Timestamp) -> tuple[np.ndarray, float, float]:
    """Modelled gain in snow water (inches) over an event: the snapshot the day after it ends minus the
    snapshot the day it starts. Returns the field and its west and north edges. Losses are kept as negative."""
    after = end + pd.Timedelta(days=1)
    _, _, west, north = window(resort, after)
    return read_swe(resort, after) - read_swe(resort, start), west, north


def on_zone_grid(field: np.ndarray, west: float, north: float, like) -> np.ndarray:
    """Resample a SNODAS window onto an open raster's grid (bilinear)."""
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    out = np.full((like.height, like.width), np.nan, dtype=np.float32)
    reproject(field.astype(np.float32), out, src_transform=from_origin(west, north, CELL, CELL), src_crs="EPSG:4326",
              dst_transform=like.transform, dst_crs=like.crs, resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
    return out
