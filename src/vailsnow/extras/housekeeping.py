"""Keep the disk from filling up.

Most of what is on disk was downloaded once (model history for training). A few things grow
with every scheduled run; this trims the ones that have no further use and reports the rest.
It runs at the end of the daily forecast. Nothing here touches the forecast logs.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from ..resort import cache_dir, data_dir, web_data_dir

KEEP_RUN_DAYS = 14            # map forecasts kept for the run picker
KEEP_SNODAS_DAYS = 10         # small daily depth windows kept for the base-depth layer
KEEP_OFF_CYCLE_DAYS = 14      # National Blend cycles other than the two used for training
TRAINING_BLEND_HOURS = (12, 19)
LOG_BYTES = 400_000           # each job log is cut back to its newest 400 KB
KEEP_MARKER = ".keep"         # a forecast folder holding a file of this name is never removed


def size(path: Path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def old_runs(base: Path, keep_days: int = KEEP_RUN_DAYS) -> list[Path]:
    """Forecast folders older than `keep_days` behind the newest, unless marked to keep."""
    runs = []
    for d in base.iterdir() if base.exists() else []:
        meta = d / "meta.json"
        if d.is_dir() and meta.exists():
            runs.append((pd.Timestamp(json.loads(meta.read_text())["run"]), d))
    if not runs:
        return []
    newest = max(t for t, _ in runs)
    return [d for t, d in runs if newest - t > pd.Timedelta(days=keep_days) and not (d / KEEP_MARKER).exists()]


def off_cycle_blend(root: Path, now: pd.Timestamp, keep_days: int = KEEP_OFF_CYCLE_DAYS) -> list[Path]:
    """Blend files from cycles the training never uses, once they are too old for the live outlook."""
    out = []
    for p in root.glob("*/*.npz") if root.exists() else []:
        try:
            stamp, hour = p.stem.split("_")
            run = pd.Timestamp(stamp) + pd.Timedelta(hours=int(hour.rstrip("z")))
        except ValueError:
            continue
        if run.hour not in TRAINING_BLEND_HOURS and now - run > pd.Timedelta(days=keep_days):
            out.append(p)
    return out


def old_depth_windows(root: Path, keep: int = KEEP_SNODAS_DAYS) -> list[Path]:
    return sorted(root.glob("depth_*.npy"))[:-keep] if root.exists() else []


def trim_log(path: Path, limit: int = LOG_BYTES) -> int:
    """Cut a log back to its newest `limit` bytes, on a line boundary. Returns bytes removed."""
    n = path.stat().st_size
    if n <= limit:
        return 0
    data = path.read_bytes()[-limit:]
    data = data[data.find(b"\n") + 1:]
    path.write_bytes(data)
    return n - len(data)


def prune(dry_run: bool = False, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    web = web_data_dir()
    targets = {"old map forecasts": old_runs(web), "off-cycle Blend files": off_cycle_blend(cache_dir() / "nbm_240", now),
               "old SNODAS depth windows": old_depth_windows(cache_dir() / "snodas_live")}
    report = {"dry_run": dry_run, "removed": {}, "freed": 0}
    for name, paths in targets.items():
        freed = sum(size(p) for p in paths)
        report["removed"][name] = {"count": len(paths), "size": human(freed)}
        report["freed"] += freed
        if not dry_run:
            for p in paths:
                shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    logs = data_dir() / "logs"
    if not dry_run and logs.exists():
        trimmed = sum(trim_log(p) for p in logs.glob("*.log"))
        report["removed"]["job log lines"] = {"count": None, "size": human(trimmed)}
        report["freed"] += trimmed
    if not dry_run and targets["old map forecasts"]:
        from ..live.forecast import index_runs
        index_runs()
    report["freed"] = human(report["freed"])
    return report


def usage() -> list[dict]:
    """What is on disk, largest first, with whether it grows on the schedule."""
    c, d = cache_dir(), data_dir()
    rows = [
        ("ERA5 upper-air history (Phase 2 storm catalogue)", c / "era5_wb2", "one-off; only needed to rebuild the storm catalogue"),
        ("SNODAS archives for catalogued storms", c / "snodas", "one-off"),
        ("HRRR forecasts as issued", c / "hrrr_zarr", "grows about 0.4 GB a year; training data"),
        ("PRISM normals", c / "prism", "one-off"),
        ("HRRR gust fields", c / "hrrr_gust", "grows about 60 MB a year"),
        ("SNOTEL records", c / "snotel", "current season rewritten in place"),
        ("National Blend, long range", c / "nbm_240", "grows about 40 MB a year after pruning; training data"),
        ("National Blend and NWS, days 1 to 3", c / "nbm", "one-off"), ("NWS official grids", c / "ndfd", "one-off"),
        ("GEFS ensemble, long range", c / "gefs_240", "grows about 40 MB a year; training data"),
        ("Terrain, models and tables", d / "terrain", "one-off"), ("Fitted models and scores", d / "model", "rewritten on retrain"),
        ("Map forecasts", web_data_dir(), f"last {KEEP_RUN_DAYS} days kept"),
        ("SNODAS depth windows", c / "snodas_live", f"last {KEEP_SNODAS_DAYS} days kept"),
        ("Forecast logs (never pruned)", d / "log", "grows about 3 MB a year"), ("Job logs", d / "logs", "trimmed to 400 KB each"),
    ]
    out = [{"what": name, "bytes": size(p), "size": human(size(p)), "note": note} for name, p, note in rows]
    return sorted(out, key=lambda r: -r["bytes"])
