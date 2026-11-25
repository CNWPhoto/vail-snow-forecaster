"""Airport weather stations (ASOS/AWOS) through the Iowa Environmental Mesonet archive.

Used for one thing: wind and gusts at a ridge-level station, to check the wind the lift
wind-hold estimate is built on. Speeds come in knots and are returned in metres per second.
Times are UTC.
"""
from __future__ import annotations

import datetime as dt
import io
import time

import pandas as pd
import requests

from ..fetch import USER_AGENT
from ..resort import cache_dir

API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
KT = 0.514444


def _fetch_year(station: str, wy: int, today: dt.date) -> str:
    """Raw CSV for one water year (October to September), cached. Finished years are kept for good."""
    path = cache_dir() / "asos" / station / f"WY{wy}.csv"
    begin, end = dt.date(wy - 1, 10, 1), min(dt.date(wy, 10, 1), today + dt.timedelta(days=1))
    done = dt.date(wy, 10, 1) <= today
    if path.exists() and (done or time.time() - path.stat().st_mtime < 6 * 3600):
        return path.read_text()
    params = [("station", station), ("data", "sknt"), ("data", "gust"), ("data", "drct"), ("tz", "Etc/UTC"), ("format", "onlycomma"),
              ("latlon", "no"), ("missing", "M"), ("trace", "T"), ("direct", "no"), ("report_type", "3"), ("report_type", "4"),
              ("year1", begin.year), ("month1", begin.month), ("day1", begin.day), ("year2", end.year), ("month2", end.month), ("day2", end.day)]
    for attempt in range(4):                               # a shared public server: wait when it says it is busy
        r = requests.get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=300)
        if r.status_code != 429:
            break
        time.sleep(30 * (attempt + 1))
    r.raise_for_status()
    time.sleep(5.0)                                        # and never ask twice in quick succession
    if not r.text.startswith("station,valid"):
        raise RuntimeError(f"unexpected reply for {station} WY{wy}: {r.text[:120]!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text)
    return r.text


def load(station: str, first_wy: int = 2019, today: dt.date | None = None) -> pd.DataFrame:
    """Every report since `first_wy`: wind speed, gust and direction, UTC-indexed."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    frames = []
    for wy in range(first_wy, today.year + 2):
        if dt.date(wy - 1, 10, 1) > today:
            break
        frames.append(pd.read_csv(io.StringIO(_fetch_year(station, wy, today)), na_values="M"))
    df = pd.concat(frames, ignore_index=True)
    df["valid"] = pd.to_datetime(df["valid"])
    out = pd.DataFrame({"wind_ms": pd.to_numeric(df["sknt"], errors="coerce") * KT, "gust_ms": pd.to_numeric(df["gust"], errors="coerce") * KT,
                        "dir": pd.to_numeric(df["drct"], errors="coerce")}).set_index(df["valid"])
    out.index.name = "time_utc"
    return out[~out.index.duplicated()].sort_index()


def hourly_peak(obs: pd.DataFrame) -> pd.DataFrame:
    """Per UTC hour (labelled by its end): mean wind, strongest gust reported (or the strongest
    sustained wind when no gust was reported, which is how these stations report a steady wind),
    and how many reports the hour had."""
    peak = obs["gust_ms"].fillna(obs["wind_ms"])
    g = obs.assign(peak=peak).resample("h", label="right", closed="right")
    out = pd.DataFrame({"wind_ms": g["wind_ms"].mean(), "peak_ms": g["peak"].max(), "reports": g["wind_ms"].count()})
    return out[out["reports"] >= 2]
