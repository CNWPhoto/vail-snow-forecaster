"""Fetch the ERA5 chunks the storm catalogue needs. Safe to stop and rerun: finished chunks are kept."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from ..resort import data_dir
from . import wb2

FINE_VARS = ("u_component_of_wind", "v_component_of_wind", "temperature", "specific_humidity", "geopotential")
FINE_SURFACE = ("mean_sea_level_pressure", "total_column_water_vapour")
COARSE_VARS = ("u_component_of_wind", "v_component_of_wind", "temperature")
SEASON_MONTHS = (10, 11, 12, 1, 2, 3, 4, 5)
RECORD_START = pd.Timestamp("1978-10-01")
PAD = pd.Timedelta(hours=18)        # either side of an event, and SNOTEL days are Pacific Standard Time


def event_chunks(store: wb2.Store, events: pd.DataFrame) -> list[int]:
    """Chunks touched by the events (those ending after the store does are skipped)."""
    out: set[int] = set()
    for start, end in zip(events["start"], events["end"]):
        a, b = start - PAD, end + pd.Timedelta(days=1) + PAD
        if b <= wb2.END:
            out.update(range(store.chunk_of(a), store.chunk_of(b) + 1))
    return sorted(out)


def season_chunks(store: wb2.Store) -> list[int]:
    """Every chunk that holds at least one snow-season day since the SNOTEL record began."""
    out = []
    for c in range(store.chunk_of(RECORD_START), store.chunk_of(wb2.END) + 1):
        if any(m in SEASON_MONTHS for m in store.chunk_times(c).month):
            out.append(c)
    return out


def plan(top_only: bool = True) -> list[tuple[wb2.Store, str, int, bool]]:
    events = pd.read_parquet(data_dir() / "catalog" / "events.parquet")
    if top_only:
        events = events[events["top"]]
    jobs = [(wb2.COARSE, v, c, True) for c in season_chunks(wb2.COARSE) for v in COARSE_VARS]
    fine = event_chunks(wb2.FINE, events)
    jobs += [(wb2.FINE, v, c, False) for c in fine for v in FINE_SURFACE]
    jobs += [(wb2.FINE, v, c, True) for c in fine for v in FINE_VARS]
    return jobs


def run(workers: int = 8) -> dict:
    jobs = plan()
    todo = [j for j in jobs if not (j[0].chunk_path(j[1], j[2], j[3]).exists())]
    print(f"{len(jobs)} chunks needed, {len(todo)} to fetch", flush=True)
    done = failed = 0
    with ThreadPoolExecutor(workers) as pool:
        futures = {pool.submit(s.fetch, v, c, lv): (s.name, v, c) for s, v, c, lv in todo}
        for f in as_completed(futures):
            try:
                f.result()
                done += 1
            except Exception as e:      # keep going; a rerun picks up what failed
                failed += 1
                print("failed", futures[f], repr(e)[:120], flush=True)
            if (done + failed) % 100 == 0:
                print(f"{done + failed}/{len(todo)} fetched", flush=True)
    return {"needed": len(jobs), "fetched": done, "failed": failed}
