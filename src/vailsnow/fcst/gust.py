"""HRRR's surface wind-gust field, kept apart from the main cache because only the lift
wind-hold estimate uses it. Same public copy, same window, one small file per run."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from ..resort import cache_dir
from . import hrrr


def path(run: pd.Timestamp) -> Path:
    return cache_dir() / "hrrr_gust" / f"{run:%Y}" / f"{run:%Y%m%d_%H}z.npz"


def fetch(run: pd.Timestamp) -> str:
    p = path(run)
    if p.exists():
        return "have"
    got = hrrr.fetch_field(run, "surface", "GUST")
    p.parent.mkdir(parents=True, exist_ok=True)
    if got is None:
        np.savez_compressed(p, absent=np.array(1))
        return "absent"
    tmp = p.with_suffix(".part.npz")
    np.savez_compressed(tmp, hours=got[0], gust=got[1].astype(np.float32))
    tmp.replace(p)
    return "done"


def load(run: pd.Timestamp) -> tuple[np.ndarray, np.ndarray] | None:
    """(forecast hours, gust in m/s on the stored window), or None."""
    p = path(run)
    if not p.exists():
        return None
    with np.load(p) as z:
        if "absent" in z.files:
            return None
        g = z["gust"].astype(np.float32)
        g[g < hrrr.FILL] = np.nan
        return z["hours"], g


def run(workers: int = 12, first: str = "2018-10-01", last: str | None = None) -> dict:
    """Fetch the gust field for every winter 12z run already in the main cache."""
    runs = [r for r in hrrr.season_runs(first, last, hours=(12,), months=hrrr.SEASON) if hrrr.run_path(r).exists() and not path(r).exists()]
    print(f"{len(runs)} gust fields to fetch", flush=True)
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(fetch, r): r for r in runs}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                k = f.result()
            except Exception as e:
                k = "failed"
                print(f"  {futures[f]}: {e}", flush=True)
            counts[k] = counts.get(k, 0) + 1
            if i % 200 == 0:
                print(f"  {i}/{len(runs)} {counts}", flush=True)
    return counts
