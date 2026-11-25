"""NRCS SNOTEL daily records through the AWDB REST API.

Daily values are start-of-day readings (midnight, Pacific Standard Time for
these sites), in the units the network stores: inches and degrees Fahrenheit.

The service's default is the opposite: without `periodRef=START` a value dated D is the
reading at the END of day D. Every request here asks for START, and a test checks the daily
record against the hourly one so the two conventions cannot be mixed up again.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import requests

from ..fetch import USER_AGENT
from ..resort import Resort, cache_dir

API = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data"
ELEMENTS = {"WTEQ": "swe", "PREC": "prec", "SNWD": "depth", "TOBS": "tobs", "TMAX": "tmax", "TMIN": "tmin", "TAVG": "tavg"}
RECORD_START = "1978-10-01"


def last_complete_water_year_end(today: dt.date) -> dt.date:
    """30 September of the most recent finished water year."""
    return dt.date(today.year if today >= dt.date(today.year, 10, 1) else today.year - 1, 9, 30)


def _fetch(triplet: str, begin: str, end: str, path: Path, max_age_h: float | None) -> list:
    """Raw API response, cached on disk. Finished water years never change, so `max_age_h=None`
    means cache forever; the current season is refreshed when older than `max_age_h` hours."""
    fresh = path.exists() and (max_age_h is None or time.time() - path.stat().st_mtime < max_age_h * 3600)
    if not fresh:
        for attempt in range(4):                # the service answers an occasional 403 or 500 and then recovers
            r = requests.get(API, params={"stationTriplets": triplet, "elements": ",".join(ELEMENTS), "duration": "DAILY",
                                          "beginDate": begin, "endDate": end, "periodRef": "START"}, headers={"User-Agent": USER_AGENT}, timeout=180)
            if r.status_code != 403 and r.status_code < 500:
                break
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(r.text)
        tmp.replace(path)
    return json.loads(path.read_text())


def parse(payload: list) -> pd.DataFrame:
    """API response for one station to a date-indexed table with one column per element."""
    cols = {}
    for block in payload[0]["data"] if payload else []:
        name = ELEMENTS.get(block["stationElement"]["elementCode"])
        if name:
            cols[name] = pd.Series({pd.Timestamp(v["date"]): v.get("value") for v in block["values"]}, dtype="float64")
    df = pd.DataFrame(cols)
    df.index.name = "date"
    return df.sort_index()


def load_site(triplet: str, today: dt.date | None = None, max_age_h: float = 12) -> pd.DataFrame:
    today = today or dt.date.today()
    done = last_complete_water_year_end(today)
    d = cache_dir() / "snotel" / triplet.replace(":", "_")
    hist = parse(_fetch(triplet, RECORD_START, done.isoformat(), d / f"daily_start_{RECORD_START}_{done}.json", None))
    cur = parse(_fetch(triplet, (done + dt.timedelta(days=1)).isoformat(), today.isoformat(), d / "daily_start_current.json", max_age_h))
    return pd.concat([hist, cur]).sort_index()


def load_all(resort: Resort) -> pd.DataFrame:
    """All configured SNOTEL sites, long format: site, date, then one column per element."""
    frames = []
    for s in resort.snotel:
        df = load_site(s["triplet"]).reset_index()
        df.insert(0, "site", s["id"])
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Hourly record (target site only). One cached file per water year.
# ---------------------------------------------------------------------------

HOURLY_FIRST_WY = 2007      # hourly snow depth and dense hourly reporting begin in mid-2006 at site 842


def load_hourly(triplet: str, first_wy: int = HOURLY_FIRST_WY, today: dt.date | None = None, max_age_h: float = 12) -> pd.DataFrame:
    """Hourly readings (Pacific Standard Time) for one site, from water year `first_wy` on."""
    today = today or dt.date.today()
    last_done = last_complete_water_year_end(today).year
    d = cache_dir() / "snotel" / triplet.replace(":", "_")
    frames = []
    for wy in range(first_wy, last_done + 2):
        begin, end = dt.date(wy - 1, 10, 1), min(dt.date(wy, 9, 30), today + dt.timedelta(days=1))   # tomorrow: today's hours so far are wanted too
        if begin > today:
            break
        path = d / f"hourly_WY{wy}.json"
        fresh = path.exists() and (wy <= last_done or time.time() - path.stat().st_mtime < max_age_h * 3600)
        if not fresh:
            for attempt in range(4):            # the service returns an occasional 500
                r = requests.get(API, params={"stationTriplets": triplet, "elements": "WTEQ,PREC,SNWD,TOBS", "duration": "HOURLY",
                                              "beginDate": begin.isoformat(), "endDate": end.isoformat()},
                                 headers={"User-Agent": USER_AGENT}, timeout=300)
                if r.status_code != 403 and r.status_code < 500:
                    break
                time.sleep(5 * (attempt + 1))
            r.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(r.text)
        frames.append(parse(json.loads(path.read_text())))
    df = pd.concat(frames).sort_index()
    df.index.name = "time"
    return df[~df.index.duplicated()]
