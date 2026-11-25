"""HRRR forecasts as issued, from the public chunked copy on AWS (`hrrrzarr`, University of Utah).

The copy stores each field as 150 x 150 cell chunks holding every forecast hour of a run, so one
small request per field covers the whole Vail region. Forecast runs are present from 2018-07-13.
Only a window of the chunk is kept on disk: about 125 x 155 km centred on the mountain.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from numcodecs import Blosc
from pyproj import Proj

from ..fetch import USER_AGENT
from ..resort import cache_dir

BASE = "https://hrrrzarr.s3.amazonaws.com"
FIRST_RUN = pd.Timestamp("2018-07-13")
CELL = 3000.0
X0, Y0 = -2697520.142521929, -1587306.152556665     # centre of grid cell (0, 0), metres
CHUNK = 150
CHUNK_ROW, CHUNK_COL = 3, 4                          # the chunk holding the Vail region
ROWS, COLS = slice(108, 150), slice(20, 72)          # window kept, inside that chunk
FILL = -9000.0
SEASON = (11, 12, 1, 2, 3, 4)

# (level, variable, short name, required)
FIELDS = (
    ("surface", "APCP_1hr_acc_fcst", "apcp", True),
    ("surface", "WEASD_1hr_acc_fcst", "weasd", False),
    ("surface", "ASNOW_1hr_acc_fcst", "asnow", False),
    ("2m_above_ground", "TMP", "t2", True),
    ("2m_above_ground", "DPT", "td2", True),
    ("700mb", "UGRD", "u700", True),
    ("700mb", "VGRD", "v700", True),
    ("700mb", "TMP", "t700", True),
    ("700mb", "DPT", "td700", True),
    ("500mb", "UGRD", "u500", True),
    ("500mb", "VGRD", "v500", True),
    ("500mb", "TMP", "t500", True),
    ("entire_atmosphere_single_layer", "PWAT", "pwat", True),
)

PROJ = Proj(proj="lcc", lat_0=38.5, lon_0=-97.5, lat_1=38.5, lat_2=38.5, R=6371229)
_local = __import__("threading").local()


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


def cell_of(lat: float, lon: float) -> tuple[int, int]:
    """(row, col) in the full HRRR grid of the cell containing a point."""
    x, y = PROJ(lon, lat)
    return int(round((y - Y0) / CELL)), int(round((x - X0) / CELL))


def window_cell(lat: float, lon: float) -> tuple[int, int]:
    """(row, col) inside the stored window. Raises if the point is outside it."""
    r, c = cell_of(lat, lon)
    r -= CHUNK_ROW * CHUNK + ROWS.start
    c -= CHUNK_COL * CHUNK + COLS.start
    if not (0 <= r < ROWS.stop - ROWS.start and 0 <= c < COLS.stop - COLS.start):
        raise ValueError(f"point {lat}, {lon} is outside the stored HRRR window")
    return r, c


def window_latlon() -> tuple[np.ndarray, np.ndarray]:
    """Latitude and longitude of every cell centre in the stored window."""
    rows = np.arange(CHUNK_ROW * CHUNK + ROWS.start, CHUNK_ROW * CHUNK + ROWS.stop)
    cols = np.arange(CHUNK_COL * CHUNK + COLS.start, CHUNK_COL * CHUNK + COLS.stop)
    xx, yy = np.meshgrid(X0 + cols * CELL, Y0 + rows * CELL)
    lon, lat = PROJ(xx, yy, inverse=True)
    return lat, lon


def run_path(run: pd.Timestamp) -> Path:
    return cache_dir() / "hrrr_zarr" / f"{run:%Y}" / f"{run:%Y%m%d_%H}z.npz"


def _get(key: str, optional: bool = False) -> bytes | None:
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = session().get(f"{BASE}/{key}", timeout=60)
            if r.status_code in (403, 404):
                if optional:
                    return None
                raise FileNotFoundError(key)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last = e
    raise RuntimeError(f"failed after retries: {key}: {last}")


def decode_chunk(raw: bytes, dtype: str, shape: tuple[int, int, int]) -> np.ndarray:
    """One compressed chunk -> (hours, 150, 150) array."""
    return np.frombuffer(Blosc().decode(raw), dtype=dtype).reshape(shape)


def fetch_field(run: pd.Timestamp, level: str, var: str) -> tuple[np.ndarray, np.ndarray] | None:
    """(forecast hours, window array) for one field of one run, or None when the copy lacks it."""
    root = f"sfc/{run:%Y%m%d}/{run:%Y%m%d}_{run:%H}z_fcst.zarr/{level}/{var}"
    meta = _get(f"{root}/.zmetadata", optional=True)
    if meta is None:
        return None
    meta = json.loads(meta)["metadata"]
    za = meta[f"{level}/{var}/.zarray"]
    period = next((k.split("/")[0] for k in meta if k.startswith("forecast_period") and k.endswith("/.zarray")), None)
    if period is None or len(za["shape"]) != 3:      # a field stored without a time axis is unusable
        return None
    hours = np.frombuffer(Blosc().decode(_get(f"{root}/{period}/0")), dtype=meta[f"{period}/.zarray"]["dtype"])
    raw = _get(f"{root}/{level}/{var}/0.{CHUNK_ROW}.{CHUNK_COL}", optional=True)
    if raw is None:
        return None
    n = za["chunks"][0]
    a = decode_chunk(raw, za["dtype"], (n, CHUNK, CHUNK))[: za["shape"][0], ROWS, COLS]
    if len(hours) != a.shape[0]:
        raise ValueError(f"{root}: {len(hours)} forecast hours for {a.shape[0]} time steps")
    return hours.astype(np.int16), np.ascontiguousarray(a)


def fetch_run(run: pd.Timestamp) -> str:
    """Download one run's fields to a single file. Returns 'have', 'done', 'absent' or 'partial'."""
    path = run_path(run)
    if path.exists():
        return "have"
    out: dict[str, np.ndarray] = {}
    missing = []
    for level, var, name, required in FIELDS:
        got = fetch_field(run, level, var)
        if got is None:
            if required and name == "apcp":
                path.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(path, absent=np.array(1))
                return "absent"
            missing.append(name)
            continue
        out[f"h_{name}"], out[name] = got
    out["missing"] = np.array(missing)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part.npz")
    np.savez_compressed(tmp, **out)
    tmp.replace(path)
    return "partial" if any(m for m in missing if m not in ("weasd", "asnow")) else "done"


SHOULDER = (10, 5)           # October and May: pulled for the rain/snow test, not used to train the water model


def season_runs(first: str = "2018-10-01", last: str | None = None, hours: tuple[int, ...] = (0, 12),
                months: tuple[int, ...] = SEASON + SHOULDER) -> list[pd.Timestamp]:
    """Every run at the given hours on snow-season days."""
    end = pd.Timestamp(last) if last else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=2)
    days = pd.date_range(max(pd.Timestamp(first), FIRST_RUN), end, freq="D")
    return [d + pd.Timedelta(hours=h) for d in days if d.month in months for h in hours]


def run(workers: int = 12, first: str = "2018-10-01", last: str | None = None, limit: int | None = None) -> dict:
    runs = [r for r in season_runs(first, last) if not run_path(r).exists()]
    if limit:
        runs = runs[:limit]
    print(f"{len(runs)} runs to fetch", flush=True)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(fetch_run, r): r for r in runs}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                k = f.result()
            except Exception as e:          # keep going; a rerun picks up what failed
                k = "failed"
                print(f"  {futures[f]}: {e}", flush=True)
            counts[k] = counts.get(k, 0) + 1
            if i % 200 == 0:
                print(f"  {i}/{len(runs)} {counts}", flush=True)
    return counts


def load_run(run: pd.Timestamp) -> dict[str, np.ndarray] | None:
    """A cached run as float32 arrays with fill values turned into NaN, or None if absent."""
    path = run_path(run)
    if not path.exists():
        return None
    with np.load(path) as z:
        if "absent" in z.files:
            return None
        out = {}
        for k in z.files:
            a = z[k]
            if k.startswith("h_") or k == "missing":
                out[k] = a
            else:
                a = a.astype(np.float32)
                a[a < FILL] = np.nan
                out[k] = a
    return out


def terrain() -> np.ndarray:
    """HRRR model terrain height (m) on the stored window. Fetched once."""
    path = cache_dir() / "hrrr_zarr" / "terrain.npy"
    if not path.exists():
        root = "sfc/20210115/20210115_00z_anl.zarr/surface/HGT/surface/HGT"
        za = json.loads(_get(f"{root}/.zarray"))
        raw = _get(f"{root}/{CHUNK_ROW}.{CHUNK_COL}")
        a = np.frombuffer(Blosc().decode(raw), dtype=za["dtype"]).reshape(CHUNK, CHUNK)[ROWS, COLS]
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, a.astype(np.float32))
    return np.load(path)
