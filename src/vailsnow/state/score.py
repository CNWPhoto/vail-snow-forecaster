"""The mountain state beside the mid-mountain depth sensor.

Once a day, at 5 am Mountain Standard Time, the live run writes down what the state held at the
gauge before it was eased toward the sensor (state/run.py). This turns those lines into the
numbers shown on the public scorecard.

What this is not: an independent test. The state is driven by this gauge's water and scaled by
this sensor's depth, so the comparison shows how well the state stays tied to the one instrument
it has, not how good it is elsewhere. Nothing else is public: the resort's own stakes may be read
privately but not republished, and no public sensor measures snow in Blue Sky Basin.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..resort import cache_dir, state_dir

SNOW_DAY_WATER_IN = 0.2


def kept_log():
    """The copy kept with the state's checkpoint."""
    return cache_dir() / "state_carry" / "scores.jsonl"


def published_log():
    """The copy published with the state files, where the forecast's scorecard reads it."""
    return state_dir() / "scores.jsonl"


def _lines(path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return {d["time"]: d for d in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}


def record(new: list[dict]) -> int:
    """Add the morning lines not already logged. Returns how many were new.

    The log is kept in two places and each run writes both from their union, so losing either one
    loses nothing: the checkpoint's cache can be evicted, and the published copy can be set back
    an hour when a forecast job publishes the site from an older copy."""
    have = {**_lines(published_log()), **_lines(kept_log())}
    added = [d for d in new if d and d["time"] not in have]
    have.update({d["time"]: d for d in added})
    text = "".join(json.dumps(have[t], separators=(",", ":")) + "\n" for t in sorted(have))
    if have:
        for path in (kept_log(), published_log()):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return len(added)


def read(path=None) -> pd.DataFrame:
    rows = list(({**_lines(published_log()), **_lines(kept_log())} if path is None else _lines(path)).values())
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d["time"] = pd.to_datetime(d["time"].str.replace("Z", ""))
    return d.drop_duplicates("time").sort_values("time").set_index("time")


def block(log: pd.DataFrame | None = None, now: pd.Timestamp | None = None) -> dict:
    """Season-to-date numbers for the scorecard."""
    d = read() if log is None else log
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    season = now.year + (now.month >= 10)
    out = {"n": 0}
    if d.empty:
        return out
    d = d[d.index >= pd.Timestamp(f"{season - 1}-10-01")]
    d = d[d["sensor_depth_in"].notna() & d["model_depth_in"].notna()]
    if d.empty:
        return out
    miss = d["model_depth_in"] - d["sensor_depth_in"]
    out = {"n": int(len(d)), "first": d.index[0].date().isoformat(), "last": d.index[-1].date().isoformat(),
           "depth_mae_in": round(float(miss.abs().mean()), 1), "depth_bias_in": round(float(miss.mean()), 1),
           "latest": {"model_depth_in": float(d["model_depth_in"].iloc[-1]), "sensor_depth_in": float(d["sensor_depth_in"].iloc[-1])}}
    # the 24-hour change in depth on days it snowed: model change against the sensor's
    prev = d["model_depth_in"].reindex(d.index - pd.Timedelta(hours=24)).to_numpy()
    change = d["model_depth_in"].to_numpy() - prev
    snow = d["gauge_water_24h_in"].fillna(0).to_numpy() >= SNOW_DAY_WATER_IN
    ok = snow & np.isfinite(change) & d["sensor_change_24h_in"].notna().to_numpy()
    if ok.any():
        err = change[ok] - d["sensor_change_24h_in"].to_numpy()[ok]
        out["snow_days"] = {"n": int(ok.sum()), "change_mae_in": round(float(np.abs(err).mean()), 1), "change_bias_in": round(float(err.mean()), 1)}
    return out
