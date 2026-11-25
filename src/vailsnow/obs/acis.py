"""Valley observers (NWS COOP and CoCoRaHS) through the RCC-ACIS web service.

These are once-a-day manual readings, usually around 7 am local, covering the
previous 24 hours. Inches throughout.
"""
from __future__ import annotations

import datetime as dt
import json
import time

import pandas as pd
import requests

from ..fetch import USER_AGENT
from ..resort import Resort, cache_dir

API = "https://data.rcc-acis.org/StnData"
ELEMS = ("pcpn", "snow", "snwd")


def to_number(v: str) -> float:
    """ACIS strings to numbers: 'M' is missing, 'T' is a trace, a trailing 'A' marks a multi-day total."""
    v = v.strip()
    if v in ("M", "", "S"):
        return float("nan")
    if v == "T":
        return 0.0
    return float(v.rstrip("A"))


def parse(payload: dict) -> pd.DataFrame:
    rows = payload.get("data", [])
    df = pd.DataFrame([[to_number(x) for x in r[1:]] for r in rows], columns=list(ELEMS),
                      index=pd.to_datetime([r[0] for r in rows]))
    df["multi_day"] = [any(x.strip().endswith("A") for x in r[1:]) for r in rows]
    df.index.name = "date"
    return df


def load_site(sid: str, today: dt.date | None = None) -> pd.DataFrame:
    today = today or dt.date.today()
    path = cache_dir() / "acis" / f"{sid}.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 12 * 3600:
        r = requests.post(API, json={"sid": sid, "sdate": "1978-10-01", "edate": today.isoformat(), "elems": ",".join(ELEMS)},
                          headers={"User-Agent": USER_AGENT}, timeout=180)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text)
    return parse(json.loads(path.read_text()))


def load_all(resort: Resort) -> pd.DataFrame:
    frames = []
    for s in resort.valley:
        df = load_site(s["sid"]).dropna(how="all", subset=list(ELEMS)).reset_index()
        df.insert(0, "site", s["id"])
        frames.append(df)
    return pd.concat(frames, ignore_index=True)
