"""As-issued forecasts from the National Blend (NBM), the NWS official grids (NDFD) and GFS.

One small file per model run is kept under the cache, holding a window around the mountain.
All three are pulled for the 12z run (5 am Mountain Standard Time); NBM and NDFD also at 00z
for the overnight forecast.
"""
from __future__ import annotations

import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from ..resort import cache_dir
from . import grib

NBM = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
NDFD = "https://noaa-ndfd-pds.s3.amazonaws.com"
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
GEFS = "https://noaa-gefs-pds.s3.amazonaws.com"
STARTS = {"nbm": pd.Timestamp("2020-11-01"), "ndfd": pd.Timestamp("2020-11-01"), "gfs": pd.Timestamp("2021-01-01"),
          "gefs": pd.Timestamp("2020-11-01")}       # ensemble mean and spread files begin with the September 2020 upgrade
SEASON = (11, 12, 1, 2, 3, 4)
GFS_LEVEL_FIELDS = {
    "u600": r"^UGRD:600 mb:", "v600": r"^VGRD:600 mb:", "t600": r"^TMP:600 mb:", "rh600": r"^RH:600 mb:",
    "t700": r"^TMP:700 mb:", "pwat": r"^PWAT:entire atmosphere",
}


def path(model: str, run: pd.Timestamp) -> Path:
    return cache_dir() / model / f"{run:%Y}" / f"{run:%Y%m%d_%H}z.npz"


def _save(p: Path, arrays: dict[str, np.ndarray]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(p)


def _collect(url: str, patterns: dict[str, str], out: dict[str, np.ndarray]) -> None:
    for label, raw in grib.fetch_fields(url, patterns).items():
        m = grib.decode_window(raw)[0]
        out[label] = m["values"]
        out.setdefault("lat", m["lat"])
        out.setdefault("lon", m["lon"])


def fetch_nbm(run: pd.Timestamp) -> str:
    """Six-hour precipitation and snowfall out to 72 h (12z run) or 12 h (00z run)."""
    p = path("nbm", run)
    if p.exists():
        return "have"
    last = 72 if run.hour == 12 else 12
    out: dict[str, np.ndarray] = {}
    for f in range(6, last + 1, 6):
        url = f"{NBM}/blend.{run:%Y%m%d}/{run:%H}/core/blend.t{run:%H}z.core.f{f:03d}.co.grib2"
        pats = {f"apcp_{f:03d}": rf"^APCP:surface:{f - 6}-{f} hour acc fcst:$",
                f"asnow_{f:03d}": rf"^ASNOW:surface:{f - 6}-{f} hour acc fcst:$"}
        if f in (12, 36, 60):
            pats[f"snowlr_{f:03d}"] = rf"^SNOWLR:surface:{f} hour fcst:$"
            pats[f"snowlvl_{f:03d}"] = rf"^SNOWLVL:0 m above mean sea level:{f} hour fcst:$"
        _collect(url, pats, out)
    if not any(k.startswith("apcp") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


def ndfd_keys(day: pd.Timestamp, kind: str) -> list[str]:
    """Names of the CONUS 2.5 km files issued on one day."""
    prefix = {"qpf": "YIUZ98", "snow": "YSUZ98"}[kind]
    text = grib.get(f"{NDFD}/?list-type=2&prefix=wmo/{kind}/{day:%Y/%m/%d}/{prefix}").decode()
    return sorted(re.findall(r"<Key>([^<]+)</Key>", text))


def pick_issue(keys: list[str], run: pd.Timestamp) -> str | None:
    """The last file issued in the 45 minutes before the run hour (NDFD stamps it for that hour)."""
    best = None
    for k in keys:
        stamp = pd.to_datetime(k[-12:], format="%Y%m%d%H%M", errors="coerce")
        if pd.notna(stamp) and run - pd.Timedelta(minutes=45) <= stamp < run + pd.Timedelta(minutes=5):
            best = k
    return best


def fetch_ndfd(run: pd.Timestamp) -> str:
    p = path("ndfd", run)
    if p.exists():
        return "have"
    day = (run - pd.Timedelta(minutes=45)).normalize()
    out: dict[str, np.ndarray] = {}
    for kind in ("qpf", "snow"):
        key = pick_issue(ndfd_keys(day, kind), run)
        if key is None:
            continue
        raw = grib.get(f"{NDFD}/{key}", optional=True)
        if raw is None:
            continue
        for m in grib.decode_window(raw):
            base = pd.to_datetime(f"{m['dataDate']}{int(m['dataTime']):04d}", format="%Y%m%d%H%M")
            valid = pd.to_datetime(f"{m['validityDate']}{int(m['validityTime']):04d}", format="%Y%m%d%H%M")
            end = round((valid - run) / pd.Timedelta(hours=1))
            if abs((base - run) / pd.Timedelta(minutes=1)) > 45 or end % 6 or end <= 0:
                continue                       # off-cycle message: not aligned to the run hour
            out[f"{kind}_{end:03d}"] = m["values"]
            out.setdefault("lat", m["lat"])
            out.setdefault("lon", m["lon"])
    if not any(k.startswith("qpf") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


def fetch_gfs(run: pd.Timestamp) -> str:
    p = path("gfs", run)
    if p.exists():
        return "have"
    out: dict[str, np.ndarray] = {}
    for sub in ("atmos/", ""):
        base = f"{GFS}/gfs.{run:%Y%m%d}/{run:%H}/{sub}gfs.t{run:%H}z.pgrb2.0p25.f"
        for day in (1, 2, 3):
            _collect(f"{base}{day * 24:03d}", {f"apcp_{day * 24:03d}": rf"^APCP:surface:0-{day} day acc fcst:$"}, out)
        for f in (12, 36, 60):
            _collect(f"{base}{f:03d}", {f"{k}_{f:03d}": v for k, v in GFS_LEVEL_FIELDS.items()}, out)
        if out:
            break
    if not any(k.startswith("apcp") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


def fetch_gefs(run: pd.Timestamp, last: int = 72) -> str:
    """Ensemble mean and standard deviation of six-hour precipitation out to `last` hours (0.5 degree).
    The live forecast asks for 168 h for its outlook and keeps that in a file of its own."""
    p = path("gefs", run) if last == 72 else path("gefs_long", run)
    if p.exists():
        return "have"
    out: dict[str, np.ndarray] = {}
    base = f"{GEFS}/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5"
    for f in range(6, last + 1, 6):
        for kind, label in (("geavg", "mean"), ("gespr", "spread")):
            _collect(f"{base}/{kind}.t{run:%H}z.pgrb2a.0p50.f{f:03d}", {f"{label}_{f:03d}": rf"^APCP:surface:{f - 6}-{f} hour acc fcst:"}, out)
    if not any(k.startswith("mean") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


LONG_FIRST, LONG_LAST = 54, 264      # hours kept for the 10-day outlook's history: day 3's second half to day 11


def fetch_gefs_long(run: pd.Timestamp, first: int = LONG_FIRST, last: int = LONG_LAST, threads: int = 6) -> str:
    """Ensemble mean and spread of six-hour precipitation, and the mean 700 mb temperature, at
    every six-hour step from `first` to `last` hours. Any of the four daily runs. One file per run."""
    p = path("gefs_240", run)
    if p.exists():
        return "have"
    base = f"{GEFS}/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5"

    def one(job: tuple[int, str]) -> dict[str, bytes]:
        f, kind = job
        label = "mean" if kind == "geavg" else "spread"
        want = {f"{label}_{f:03d}": rf"^APCP:surface:{f - 6}-{f} hour acc fcst:"}
        if kind == "geavg":
            want[f"t700_{f:03d}"] = r"^TMP:700 mb:"
        return grib.fetch_fields(f"{base}/{kind}.t{run:%H}z.pgrb2a.0p50.f{f:03d}", want)

    out: dict[str, np.ndarray] = {}
    jobs = [(f, kind) for f in range(first, last + 1, 6) for kind in ("geavg", "gespr")]
    with ThreadPoolExecutor(threads) as pool:                 # downloads in parallel; decoding stays on this thread
        fetched = list(pool.map(one, jobs))
    for got in fetched:
        for label, raw in got.items():
            m = grib.decode_window(raw)[0]
            out[label] = m["values"]
            out.setdefault("lat", m["lat"])
            out.setdefault("lon", m["lon"])
    if not any(k.startswith("mean") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


def fetch_gefs_wind(run: pd.Timestamp, first: int = 6, last: int = LONG_LAST, threads: int = 8) -> str:
    """Ensemble-mean wind at 700 and 500 mb every six hours, for wind loading past day 3. Live runs only:
    one small file per run."""
    p = path("gefs_wind", run)
    if p.exists():
        return "have"
    base = f"{GEFS}/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5/geavg.t{run:%H}z.pgrb2a.0p50.f"

    def one(f: int) -> dict[str, bytes]:
        return grib.fetch_fields(f"{base}{f:03d}", {f"u700_{f:03d}": r"^UGRD:700 mb:", f"v700_{f:03d}": r"^VGRD:700 mb:",
                                                    f"u500_{f:03d}": r"^UGRD:500 mb:", f"v500_{f:03d}": r"^VGRD:500 mb:"})

    with ThreadPoolExecutor(threads) as pool:
        fetched = list(pool.map(one, range(first, last + 1, 6)))
    out: dict[str, np.ndarray] = {}
    for got in fetched:
        for label, raw in got.items():
            m = grib.decode_window(raw)[0]
            out[label] = m["values"]
            out.setdefault("lat", m["lat"])
            out.setdefault("lon", m["lon"])
    if not any(k.startswith("u700") for k in out):
        return "absent"                                       # nothing saved: a later refresh can try again
    _save(p, out)
    return "done"


def fetch_nbm_long(run: pd.Timestamp, first: int = LONG_FIRST, last: int = LONG_LAST, threads: int = 6, snow: bool = False) -> str:
    """The National Blend's six-hour precipitation at every six-hour step from `first` to `last`
    hours, for the days past the third. One file per run. The Blend's long-range fields are
    refreshed in the 01, 07, 13 and 19 UTC cycles; the 12 UTC cycle carries the 07 UTC ones."""
    p = path("nbm_240", run)
    if p.exists():
        return "have"
    base = f"{NBM}/blend.{run:%Y%m%d}/{run:%H}/core/blend.t{run:%H}z.core.f"

    def one(f: int) -> dict[str, bytes]:
        want = {f"apcp_{f:03d}": rf"^APCP:surface:{f - 6}-{f} hour acc fcst:$"}
        if snow:                                              # the Blend's own snowfall, shown beside this forecast for comparison
            want[f"asnow_{f:03d}"] = rf"^ASNOW:surface:{f - 6}-{f} hour acc fcst:$"
        return grib.fetch_fields(f"{base}{f:03d}.co.grib2", want)

    steps = [f for f in range(max(first, 6), last + 1) if (run.hour + f) % 6 == 0]      # six-hour totals end at 00, 06, 12 and 18 UTC
    with ThreadPoolExecutor(threads) as pool:                 # downloads in parallel; decoding stays on this thread
        fetched = list(pool.map(one, steps))
    out: dict[str, np.ndarray] = {}
    for got in fetched:
        for label, raw in got.items():
            m = grib.decode_window(raw)[0]
            out[label] = m["values"]
            out.setdefault("lat", m["lat"])
            out.setdefault("lon", m["lon"])
    if not any(k.startswith("apcp") for k in out):
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, out)
    return "done"


FETCH = {"nbm": fetch_nbm, "ndfd": fetch_ndfd, "gfs": fetch_gfs, "gefs": fetch_gefs, "gefs_240": fetch_gefs_long, "nbm_240": fetch_nbm_long}
STARTS["nbm_240"] = STARTS["nbm"]
STARTS["gefs_240"] = STARTS["gefs"]


def runs(model: str, last: str | None = None) -> list[pd.Timestamp]:
    end = pd.Timestamp(last) if last else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=4)
    hours = (12,) if model in ("gfs", "gefs", "gefs_240", "nbm_240") else (0, 12)
    days = pd.date_range(STARTS[model], end, freq="D")
    return [d + pd.Timedelta(hours=h) for d in days if d.month in SEASON for h in hours]


def _one(job: tuple[str, pd.Timestamp]) -> str:
    return FETCH[job[0]](job[1])


def run(models: tuple[str, ...] = ("gfs", "ndfd", "nbm"), workers: int = 8, limit: int | None = None) -> dict:
    jobs = [(m, r) for m in models for r in runs(m) if not path(m, r).exists()]
    if limit:
        jobs = jobs[:limit]
    print(f"{len(jobs)} runs to fetch", flush=True)
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(workers) as pool:
        futures = {pool.submit(_one, j): j for j in jobs}
        for i, f in enumerate(as_completed(futures), 1):
            m = futures[f][0]
            try:
                k = f"{m}:{f.result()}"
            except Exception as e:              # keep going; a rerun picks up what failed
                k = f"{m}:failed"
                print(f"  {futures[f]}: {type(e).__name__}: {e}", flush=True)
            counts[k] = counts.get(k, 0) + 1
            if i % 100 == 0:
                print(f"  {i}/{len(jobs)} {counts}", flush=True)
    return counts


def load(model: str, run: pd.Timestamp) -> dict[str, np.ndarray] | None:
    p = path(model, run)
    if not p.exists():
        return None
    with np.load(p) as z:
        if "absent" in z.files:
            return None
        return {k: z[k] for k in z.files}
