"""Is it still running? When each piece last updated, and whether the scheduled jobs are loaded."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pandas as pd

from ..resort import data_dir, web_data_dir

JOBS = {"forecast": "map, zones, lifts, first chair, scorecard: 10:45 am", "outlook": "10-day outlook: 4:55 and 10:55, am and pm",
        "evening": "evening alert: 9:45 pm", "morning": "5 am alert: 5:05 am"}
STALE_H = {"forecast": 30, "outlook": 9, "scorecard": 30}


def age(when: pd.Timestamp, now: pd.Timestamp) -> str:
    h = (now - when) / pd.Timedelta(hours=1)
    return f"{h * 60:.0f} min ago" if h < 1 else f"{h:.1f} h ago" if h < 48 else f"{h / 24:.0f} days ago"


def pieces(now: pd.Timestamp | None = None, base: Path | None = None) -> list[dict]:
    """Each output file's last update (UTC) and whether it is older than it should be on a working schedule."""
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    base = base or web_data_dir()
    rows = []

    def add(name: str, when: pd.Timestamp | None, limit: float | None, note: str = "") -> None:
        rows.append({"piece": name, "when": when, "age": None if when is None else age(when, now),
                     "stale": when is None or (limit is not None and (now - when) > pd.Timedelta(hours=limit)), "note": note})

    runs = json.loads((base / "runs.json").read_text()) if (base / "runs.json").exists() else []
    newest = max(runs, key=lambda r: r["run"], default=None)
    built = None
    if newest:
        meta = json.loads((base / newest["name"] / "meta.json").read_text())
        built = pd.Timestamp(meta["built"].replace("Z", ""))
    add("map forecast", built, STALE_H["forecast"], f"model run {newest['run']}Z" if newest else "none built")
    for name, file, key, limit in (("10-day outlook", "outlook.json", "updated", STALE_H["outlook"]), ("scorecard", "scorecard.json", "updated", STALE_H["scorecard"]),
                                   ("last alert built", "alert.json", "time", None)):
        p = base / file
        d = json.loads(p.read_text()) if p.exists() else {}
        when = pd.Timestamp(str(d[key]).replace("Z", "")) if d.get(key) and not str(d[key]).startswith("1970") else None
        add(name, when, limit, f"ensemble run {d['ensemble_run']}Z" if "ensemble_run" in d else (d.get("title", "") if file == "alert.json" else ""))
    return rows


def jobs() -> dict[str, bool]:
    """Which of the four launchd jobs are loaded for this user."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    return {j: f"com.vailsnow.{j}" in out for j in JOBS}


def report() -> str:
    lines = ["Last updates"]
    for r in pieces():
        local = None if r["when"] is None else r["when"].tz_localize("UTC").tz_convert(dt.datetime.now().astimezone().tzinfo)
        when = "never" if local is None else f"{local:%a %b %-d %-I:%M %p %Z} ({r['age']})"
        lines.append(f"  {'STALE ' if r['stale'] and r['piece'] != 'last alert built' else '      '}{r['piece']:<17} {when}" + (f"   {r['note']}" if r["note"] else ""))
    lines.append("\nScheduled jobs")
    for j, loaded in jobs().items():
        lines.append(f"  {'loaded    ' if loaded else 'NOT LOADED'}  {j:<9} {JOBS[j]}")
    log = data_dir() / "log"
    n = {f: sum(1 for _ in (log / f).open()) if (log / f).exists() else 0 for f in ("forecasts.jsonl", "outlook.jsonl")}
    lines.append(f"\nForecast log: {n['forecasts.jsonl']} three-day and {n['outlook.jsonl']} long-range forecasts logged. Job logs are in {os.path.relpath(data_dir() / 'logs')}/.")
    return "\n".join(lines)
