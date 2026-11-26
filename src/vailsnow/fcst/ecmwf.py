"""The European ensemble (ECMWF IFS ENS): precipitation from each member, kept as a small window
around the mountain.

Two public sources, both free and under CC BY 4.0 with ECMWF named as the source:

* 2018 to 2022: the WeatherBench 2 copy on Google Cloud, which holds the ensemble MEAN on a
  0.25 degree grid (one member's worth: the average of all fifty).
* 18 January 2023 on: ECMWF's own open data on its public cloud mirrors, every member
  (0.4 degree until February 2024, 0.25 degree since). This is also the live feed; a run is
  complete there about 7 hours 45 minutes after it starts. The mirrors refuse requests when
  asked for too much at once (a few dozen connections is fine, a hundred and fifty is not).

One file per run: precipitation accumulated from the start of the run, in millimetres, at
each kept step, for each member. Only the 00z and 12z runs reach ten days.

This data is based on data and products of the European Centre for Medium-Range Weather
Forecasts (ECMWF).
"""
from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
from numcodecs import Blosc

from . import grib
from .archive import SEASON, _save, path

WB2 = "https://storage.googleapis.com/weatherbench2/datasets/ifs_ens/2018-2022-1440x721_mean.zarr"
OPEN = ("https://storage.googleapis.com/ecmwf-open-data",                 # the same files on two public mirrors: Google's
        "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com")          # (from late 2023) and Amazon's (from January 2023)
WB2_T0, WB2_END = pd.Timestamp("2018-01-01"), pd.Timestamp("2022-12-31 12:00")
OPEN_T0 = pd.Timestamp("2023-01-18")
LAST_STEP = 276
OUT_AFTER = pd.Timedelta(hours=6)       # no run is complete sooner than this after it starts (in practice about 7 h 45 min)
MM_PER_IN = 25.4
FEATURES = ("ec_mean", "ec_area", "ec_prev", "ec_next")       # from the mean of the members
MEMBER_FEATURES = ("ec_spread", "ec_p90")                     # from the members themselves (2023 on)
MEMBERS = 25                 # the first 25 of the 50 perturbed members: half the download, and the members are
                             # interchangeable, so their average is a fair sample of all of them. The control
                             # run is left out: it stopped being published in these files during 2026
LAYOUTS = ("ifs/0p25", "0p25", "ifs/0p4-beta", "0p4-beta")      # where a run's files sit; the finer grid first
MODEL = "ecens"


def steps_for(run: pd.Timestamp, last: int = LAST_STEP) -> list[int]:
    """Steps that end at 12z (5 am Mountain Standard Time), where every forecast day here starts and ends."""
    first = (12 - run.hour) % 24 or 24
    return list(range(first, last + 1, 24))


def _wb2_window() -> tuple[slice, slice, np.ndarray, np.ndarray]:
    lat, lon = np.arange(-90.0, 90.01, 0.25), np.arange(0.0, 360.0, 0.25)
    rows = np.nonzero((lat >= grib.LAT[0]) & (lat <= grib.LAT[1]))[0]
    cols = np.nonzero((lon - 360.0 >= grib.LON[0]) & (lon - 360.0 <= grib.LON[1]))[0]
    rs, cs = slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)
    lats, lons = np.meshgrid(lat[rs], lon[cs] - 360.0, indexing="ij")
    return rs, cs, lats.astype(np.float32), lons.astype(np.float32)


def fetch_wb2(run: pd.Timestamp, threads: int = 6) -> str:
    """The ensemble mean from the WeatherBench 2 copy (2018 to 2022)."""
    p = path(MODEL, run)
    if p.exists():
        return "have"
    if not WB2_T0 <= run <= WB2_END or run.hour not in (0, 12):
        return "absent"
    rs, cs, lats, lons = _wb2_window()
    t = int((run - WB2_T0) / pd.Timedelta(hours=12))
    steps = steps_for(run)

    def one(step: int) -> np.ndarray | None:
        raw = grib.get(f"{WB2}/total_precipitation/{t}.{step // 6}.0.0", optional=True)
        if raw is None:
            return None
        a = np.frombuffer(Blosc().decode(raw), dtype="<f4").reshape(721, 1440)
        return a[rs, cs] * 1000.0                             # metres to millimetres

    with ThreadPoolExecutor(threads) as pool:
        got = list(pool.map(one, steps))
    keep = [(s, a) for s, a in zip(steps, got) if a is not None and np.isfinite(a).all()]
    if len(keep) < 4:
        _save(p, {"absent": np.array(1)})
        return "absent"
    _save(p, {"steps": np.array([s for s, _ in keep]), "tp": np.stack([a for _, a in keep])[:, None].astype(np.float32),
              "lat": lats, "lon": lons, "members": np.array(0)})          # 0: the mean of all members, not members
    return "done"


def open_url(run: pd.Timestamp, step: int, layout: str, mirror: str = OPEN[0]) -> str:
    return f"{mirror}/{run:%Y%m%d}/{run:%H}z/{layout}/enfo/{run:%Y%m%d%H}0000-{step}h-enfo-ef"


def member_ranges(index_text: str, members: int = MEMBERS) -> list[tuple[int, int, int]]:
    """(member number, first byte, length) of the precipitation field of perturbed members 1 to
    `members`, from one of ECMWF's index files (a JSON object per line)."""
    out = []
    for line in index_text.splitlines():
        if '"tp"' not in line:
            continue
        r = json.loads(line)
        if r.get("param") != "tp":
            continue
        if r.get("type") == "pf" and 1 <= int(r.get("number", 0)) <= members:
            out.append((int(r["number"]), int(r["_offset"]), int(r["_length"])))
    return sorted(out)


def fetch_open(run: pd.Timestamp, last: int = LAST_STEP, threads: int = 16, members: int = MEMBERS, need_all: bool = False) -> str:
    """Member precipitation from ECMWF's open data. With `need_all` (the live outlook) nothing is
    saved unless every step is there, so a run still being published is tried again later; the
    history keeps a run that is short a step or two."""
    p = path(MODEL, run)
    if p.exists():
        return "have"
    if run < OPEN_T0 or run.hour not in (0, 12):
        return "absent"
    steps = steps_for(run, last)
    grid, index = None, {}
    for mirror, g in ((m, g) for m in OPEN for g in LAYOUTS):
        try:
            if need_all and grib.get(open_url(run, steps[-1], g, mirror) + ".index", (0, 0), optional=True) is None:
                continue                                      # the last step is not out yet
            raw = grib.get(open_url(run, steps[0], g, mirror) + ".index", optional=True)
        except RuntimeError:
            continue                                          # this mirror is refusing: try the other
        if raw:
            grid, index[steps[0]] = (g, mirror), raw.decode()
            break
    if grid is None:
        return "absent"

    def idx(step: int) -> str | None:
        raw = grib.get(open_url(run, step, *grid) + ".index", optional=True)
        return raw.decode() if raw else None

    with ThreadPoolExecutor(threads) as pool:
        for s, text in zip(steps[1:], pool.map(idx, steps[1:])):
            if text:
                index[s] = text
    jobs = [(s, n, a, ln) for s in steps if s in index for n, a, ln in member_ranges(index[s], members)]

    def one(job: tuple[int, int, int, int]) -> tuple[int, int, dict | None]:
        s, n, a, ln = job
        raw = grib.get(open_url(run, s, *grid) + ".grib2", (a, a + ln - 1), optional=True)
        return s, n, raw

    with ThreadPoolExecutor(threads) as pool:                 # downloads in parallel; decoding stays on this thread
        fetched = list(pool.map(one, jobs))
    by_step: dict[int, dict[int, np.ndarray]] = {}
    lats = lons = None
    for s, n, raw in fetched:
        if not raw:
            continue
        m = grib.decode_window(raw)[0]
        by_step.setdefault(s, {})[n] = m["values"] * 1000.0   # metres to millimetres
        lats, lons = m["lat"], m["lon"]
    full = [s for s in steps if len(by_step.get(s, {})) == members]
    if len(full) < (len(steps) if need_all else 4):
        return "absent"
    tp = np.stack([np.stack([by_step[s][n] for n in range(1, members + 1)]) for s in full]).astype(np.float32)
    _save(p, {"steps": np.array(full), "tp": tp, "lat": lats, "lon": lons, "members": np.array(members)})
    return "done"


def fetch(run: pd.Timestamp) -> str:
    """Get a run from whichever source holds it. A run from the last day and a half must be
    complete to be kept: it may still be coming out."""
    if run <= WB2_END:
        return fetch_wb2(run)
    fresh = pd.Timestamp.now(tz="UTC").tz_localize(None) - run < pd.Timedelta(hours=36)
    return fetch_open(run, need_all=fresh)


def history_runs(last: str | None = None) -> list[pd.Timestamp]:
    """The 00z run of every day the forecast tables use: November to April since late 2018 (when
    the HRRR record here starts), and the last days of October that the first November forecasts
    reach back to."""
    end = pd.Timestamp(last) if last else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=1)
    return [d for d in pd.date_range("2018-10-29", end, freq="D") if d.month in SEASON or (d.month == 10 and d.day >= 29)]


def _one(job: tuple[pd.Timestamp, int]) -> str:
    return fetch_wb2(job[0]) if job[0] <= WB2_END else fetch_open(job[0], threads=job[1])


def run_history(workers: int = 2, threads: int = 16) -> dict:
    """Download every past run not yet on disk. Kept to a few dozen connections: the mirrors refuse more."""
    jobs = [(r, threads) for r in history_runs() if not path(MODEL, r).exists()]
    print(f"{len(jobs)} runs to fetch", flush=True)
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(workers) as pool:
        futures = {pool.submit(_one, j): j[0] for j in jobs}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                k = f.result()
            except Exception as e:              # keep going; a rerun picks up what failed
                k = "failed"
                print(f"  {futures[f]:%Y-%m-%d}: {type(e).__name__}: {str(e)[-120:]}", flush=True)
            counts[k] = counts.get(k, 0) + 1
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} {counts}", flush=True)
    return counts


def load(run: pd.Timestamp) -> dict | None:
    p = path(MODEL, run)
    if not p.exists():
        return None
    with np.load(p) as z:
        return None if "absent" in z.files else {k: z[k] for k in z.files}


def weights(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> np.ndarray:
    """Bilinear weights over the window for a point, so grids of different spacing give comparable values."""
    la, lo = lats[:, 0].astype(float), lons[0].astype(float)
    w = np.zeros(lats.shape)

    def pair(axis: np.ndarray, v: float) -> list[tuple[int, float]]:
        order = np.argsort(axis)
        j = int(np.clip(np.searchsorted(axis[order], v) - 1, 0, len(axis) - 2))
        a, b = order[j], order[j + 1]
        f = float(np.clip((v - axis[a]) / (axis[b] - axis[a]), 0.0, 1.0))
        return [(int(a), 1 - f), (int(b), f)]

    for i, wi in pair(la, lat):
        for j, wj in pair(lo, lon):
            w[i, j] += wi * wj
    return w


def day_totals(d: dict, first_hour: int) -> np.ndarray | None:
    """Each member's precipitation (mm) over the 24 hours starting `first_hour` into the run, as
    (members, rows, cols). None when the run does not hold both ends of the day."""
    steps = list(d["steps"])
    if first_hour + 24 not in steps or (first_hour and first_hour not in steps) or first_hour < 0:
        return None
    end = d["tp"][steps.index(first_hour + 24)]
    start = d["tp"][steps.index(first_hour)] if first_hour else 0.0
    return np.clip(end - start, 0.0, None)


def features(d: dict | None, w: np.ndarray | None, first_hour: int) -> dict:
    """Model inputs (inches of water) for the 24 hours starting `first_hour` hours into a run: the
    ensemble mean at the gauge and over the window around it, the mean for the day before and
    after, and from the members their spread and 90th percentile at the gauge. `w` are the
    gauge's weights over the window (`weights`). NaN where the run does not reach, and for the
    member figures where only the mean was archived."""
    out = {k: np.nan for k in FEATURES + MEMBER_FEATURES}
    day = None if d is None else day_totals(d, first_hour)
    if day is None:
        return out
    at = (day * w).sum(axis=(1, 2)) / MM_PER_IN                  # one value per member, at the gauge
    out.update(ec_mean=float(at.mean()), ec_area=float(day.mean()) / MM_PER_IN)
    for name, start in (("ec_prev", first_hour - 24), ("ec_next", first_hour + 24)):
        other = day_totals(d, start)
        if other is not None:
            out[name] = float((other.mean(axis=0) * w).sum()) / MM_PER_IN
    if len(at) >= 10:
        out.update(ec_spread=float(at.std()), ec_p90=float(np.quantile(at, 0.9)))
    return out


def for_window(lat: float, lon: float, run: pd.Timestamp, first_hour: int) -> dict:
    """`features` for a point from a run already on disk."""
    d = load(run)
    return features(d, weights(d["lat"], d["lon"], lat, lon) if d is not None else None, first_hour)
