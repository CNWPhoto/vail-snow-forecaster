"""Powder alerts: one in the evening, one at 5 am.

Evening. Uses the 00z HRRR run (issued 5 pm Mountain Standard Time, published about 9:30 pm):
tonight's snow from 5 pm to 5 am and tomorrow's from 5 am to 5 am, each from a model fitted to
that lead time. What the gauge has already measured since 5 am is added, so the number tested
against the thresholds is new snow in the 24 hours ending 5 am. Before the 00z run is out, the
morning's 12z forecast is used instead and the message says so.

5 am. Reports what the mid-mountain SNOTEL gauge and depth sensor actually measured overnight,
then the forecast for the day ahead.

Thresholds and channels are set in the resort file under [alerts]. Nothing is sent anywhere
unless a channel other than "file" is listed there.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import requests

from ..fcst import hrrr
from ..model import final
from ..model.table import elevation_matched, hrrr_window, sites
from ..resort import ROOT, Resort, data_dir, load_resort, web_data_dir
from . import scorecard

EVENING_VALID = pd.Timedelta(hours=13)        # a 00z run speaks for tonight until 5 am, with an hour's grace
DEFAULTS = {"likely_in": 4.0, "high_end_in": 8.0, "measured_in": 3.0, "channels": ["file"]}


def state_dir() -> Path:
    d = data_dir() / "alerts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings(resort: Resort) -> dict:
    return {**DEFAULTS, **(resort.alerts or {})}


def headline_zone(resort: Resort):
    want = (resort.view or {}).get("headline_zone")
    return next((z for z in resort.zones if z.id == want), resort.zones[0])


def mst(t: pd.Timestamp, fmt: str = "%a %-I %p") -> str:
    return (t - pd.Timedelta(hours=7)).strftime(fmt)


def latest_forecast() -> tuple[Path, dict] | None:
    """The newest map forecast on disk."""
    base = web_data_dir()
    runs = json.loads((base / "runs.json").read_text()) if (base / "runs.json").exists() else []
    if not runs:
        return None
    d = base / runs[0]["name"]
    return d, json.loads((d / "meta.json").read_text())


def zone_conversion(resort: Resort, f: pd.Series, p: dict) -> dict:
    """Inches of snow in the headline zone per inch of water at the gauge, for one forecast window."""
    from ..live import grid
    from ..live.forecast import at_gauge
    z, g = headline_zone(resort), grid.static(resort)
    sel = g["zone"] == z.index
    here = at_gauge(f, p, joblib.load(final.final_dir() / "slr.joblib"), float(g["elev"][sel].mean()))
    return {"per_inch": float(g["prism"][sel].mean()) * here["snow_share"] * here["ratio"], "ratio": here["ratio"], "snow_share": here["snow_share"]}


def measured(rec: dict, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Gauge water and depth-sensor change between two times, using whatever hours have reported."""
    w = rec["water"]["water_in"]
    w = w[(w.index > start) & (w.index <= end)]
    depth = rec["depth"].dropna()
    depth = depth[(depth.index >= start) & (depth.index <= end)]
    hours = int((end - start) / pd.Timedelta(hours=1))
    return {"water_in": round(float(w.sum()), 2), "hours_reported": int(len(w)), "hours": hours,
            "through": None if w.empty else w.index[-1].isoformat(),
            "depth_change_in": None if len(depth) < 2 else round(float(depth.iloc[-1] - depth.iloc[0]), 1),
            "depth_in": None if depth.empty else round(float(depth.iloc[-1]), 1)}


def snow_range(q: dict, per_inch: float, already: float = 0.0) -> list[float]:
    """[likely, low, high] inches: the water forecast times the zone's conversion, plus what has already fallen."""
    return [round(already + q[k] * per_inch, 1) for k in ("q50", "q10", "q90")]


# ---------------------------------------------------------------------------
# Evening
# ---------------------------------------------------------------------------

def evening_from_00z(resort: Resort, run: pd.Timestamp, rec: dict) -> dict | None:
    if hrrr.fetch_run(run) not in ("have", "done", "partial"):
        return None
    d = hrrr.load_run(run)
    if d is None:
        return None
    target = scorecard.target(resort)["id"]
    pts = sites(resort)
    cells = {k: hrrr.window_cell(la, lo) for k, (la, lo, _) in pts.items()}
    terrain = hrrr.terrain()
    elev_cell = elevation_matched(terrain, *cells[target], pts[target][2])
    parts = {}
    for tier, (first, last) in ((12, (1, 12)), (36, (13, 36))):
        f = hrrr_window(d, first, last, cells, target, terrain, elev_cell)
        if f is None or "t_ref" not in f:
            return None
        f = pd.Series(f, dtype=float)
        p = final.predict(tier, pd.DataFrame([f.reindex(final.load(tier)["cols"])]))
        parts[tier] = {"water_in": {k: round(v, 3) for k, v in p.items()}, **zone_conversion(resort, f, p)}
    day = measured(rec, run - pd.Timedelta(hours=12), run)          # 5 am to 5 pm today, at the gauge
    conv = parts[12]["per_inch"]
    return {"basis": "00z", "run": run.isoformat(), "measured_today": day, "tonight": snow_range(parts[12]["water_in"], conv),
            "by_5am": snow_range(parts[12]["water_in"], conv, already=day["water_in"] * conv),
            "tomorrow": snow_range(parts[36]["water_in"], parts[36]["per_inch"]), "ratio": round(parts[12]["ratio"], 1), "per_inch": round(conv, 2),
            "per_inch_tomorrow": round(parts[36]["per_inch"], 2)}


def evening_from_12z(resort: Resort, now: pd.Timestamp) -> dict | None:
    got = latest_forecast()
    if got is None:
        return None
    d, meta = got
    run = pd.Timestamp(meta["run"])
    if not pd.Timedelta(0) <= now - run <= pd.Timedelta(hours=24):
        return None
    zone = next(z for z in json.loads((d / "zones.json").read_text()) if z["id"] == headline_zone(resort).id)
    w24, w48 = zone["windows"]["24"], zone["windows"]["48"]
    return {"basis": "12z", "run": run.isoformat(), "measured_today": None, "tonight": None, "by_5am": w24["fallen"],
            "tomorrow": [round(max(w48["fallen"][0] - w24["fallen"][0], 0.0), 1), None, None], "ratio": w24["density"],
            "per_inch": round(w24["fallen"][0] / w24["water"], 2) if w24["water"] > 0.01 else None, "per_inch_tomorrow": None}


def planning(top: int = 3) -> dict:
    """Tomorrow's first-chair picks and lift wind from the newest map forecast, if it is today's."""
    got = latest_forecast()
    if got is None:
        return {}
    d, meta = got
    out = {}
    if (d / "firstchair.json").exists():
        m = json.loads((d / "firstchair.json").read_text())["mornings"][0]
        out["first_chair"] = {"morning": m["first_chair"], "basis": m["basis"], "zones": [{"name": z["name"], "overnight": z["overnight"]} for z in m["zones"][:top]]}
    if (d / "lifts.json").exists():
        lifts = json.loads((d / "lifts.json").read_text())
        day2 = next((x for x in lifts.get("days", []) if x["day"] == 2), None)
        if day2:
            out["wind"] = {"ridge_gust_mph": day2["ridge_gust_mph"], "range_mph": day2["ridge_gust_range_mph"],
                           "holds": [{"name": l["name"], "risk": x["risk"]} for l in lifts["lifts"] for x in l["days"] if x["day"] == 2 and x["risk"] != "low"]}
    return out


def evening(resort: Resort | None = None, now: pd.Timestamp | None = None, rec: dict | None = None) -> dict:
    resort = resort or load_resort()
    now = now or scorecard.now_utc()
    cfg = settings(resort)
    run = now.normalize()
    body = None
    if now - run <= EVENING_VALID:
        body = evening_from_00z(resort, run, rec or scorecard.gauge_record(resort, max_age_h=0.5))
    body = body or evening_from_12z(resort, now)
    zone = headline_zone(resort)
    alert = {"kind": "evening", "time": now.isoformat(timespec="minutes"), "zone": zone.name, "thresholds": {k: cfg[k] for k in ("likely_in", "high_end_in")}}
    if body is None:
        return {**alert, "fire": False, "available": False, "title": "No forecast available for tonight", "lines": []}
    likely, low, high = body["by_5am"]
    reasons = []
    if likely >= cfg["likely_in"]:
        reasons.append(f"likely amount {likely:g} in is at or above your {cfg['likely_in']:g} in")
    if high is not None and high >= cfg["high_end_in"]:
        reasons.append(f"high end {high:g} in is at or above your {cfg['high_end_in']:g} in")
    plan = planning() if body["basis"] == "12z" or now - pd.Timestamp(latest_forecast()[1]["run"]) <= pd.Timedelta(hours=24) else {}
    lines = [f"{zone.name}: {likely:g} in likely in the 24 hours to 5 am, range {low:g} to {high:g}."]
    if body["basis"] == "00z":
        m = body["measured_today"]
        lines.append(f"Tonight, 5 pm to 5 am: {body['tonight'][0]:g} in likely ({body['tonight'][1]:g} to {body['tonight'][2]:g}). "
                     f"Since 5 am the gauge has measured {m['water_in']:.2f} in of water, about {m['water_in'] * body['per_inch']:.1f} in of snow.")
        lines.append(f"Tomorrow, 5 am to 5 am: {body['tomorrow'][0]:g} in likely ({body['tomorrow'][1]:g} to {body['tomorrow'][2]:g}).")
        lines.append("From the 5 pm model run.")
    else:
        lines.append("From this morning's model run; the 5 pm run is not out yet.")
    if plan.get("wind") and plan["wind"]["holds"]:
        lines.append(f"Ridge gusts near {plan['wind']['ridge_gust_mph']} mph tomorrow. Wind hold " + ", ".join(f"{h['risk']} on {h['name']}" for h in plan["wind"]["holds"][:4]) + ".")
    lines.append("Big days are under-called by the likely number: read the high end.")
    title = f"{resort.name}: {likely:g} in likely by 5 am ({low:g} to {high:g})"
    return {**alert, "fire": bool(reasons), "available": True, "reasons": reasons, "title": title, "lines": lines, **body}


# ---------------------------------------------------------------------------
# 5 am
# ---------------------------------------------------------------------------

def morning(resort: Resort | None = None, now: pd.Timestamp | None = None, rec: dict | None = None) -> dict:
    resort = resort or load_resort()
    now = now or scorecard.now_utc()
    cfg = settings(resort)
    rec = rec or scorecard.gauge_record(resort, max_age_h=0.5)
    five_am = now.normalize() + pd.Timedelta(hours=12)
    if five_am > now + pd.Timedelta(hours=6):                     # run just after midnight UTC: the morning meant is today's
        five_am -= pd.Timedelta(days=1)
    night = measured(rec, five_am - pd.Timedelta(hours=12), min(now, five_am))
    day = measured(rec, five_am - pd.Timedelta(hours=24), min(now, five_am))
    saved = state_dir() / "evening.json"
    prev = json.loads(saved.read_text()) if saved.exists() else {}
    fresh = prev.get("basis") == "00z" and pd.Timestamp(prev["run"]) == five_am - pd.Timedelta(hours=12)
    per_inch = prev.get("per_inch") if fresh and prev.get("per_inch") else None
    zone = headline_zone(resort)
    est = None if per_inch is None else round(day["water_in"] * per_inch, 1)
    reasons = []
    if night["depth_change_in"] is not None and night["depth_change_in"] >= cfg["measured_in"]:
        reasons.append(f"the depth sensor gained {night['depth_change_in']:g} in since 5 pm, at or above your {cfg['measured_in']:g} in")
    if est is not None and est >= cfg["likely_in"]:
        reasons.append(f"about {est:g} in in the last 24 hours, at or above your {cfg['likely_in']:g} in")
    site = scorecard.target(resort)
    lines = [f"Measured at {site['name']} SNOTEL ({site['elev_ft']:,} ft), through {mst(pd.Timestamp(night['through'])) if night['through'] else 'no hours yet'}:"]
    depth = "no depth reading" if night["depth_change_in"] is None else f"depth {night['depth_change_in']:+g} in (now {night['depth_in']:g} in)"
    lines.append(f"Since 5 pm: {night['water_in']:.2f} in of water, {depth}.")
    lines.append(f"Last 24 hours: {day['water_in']:.2f} in of water" + (f", about {est:g} in of new snow in {zone.name}." if est is not None else "."))
    if night["hours_reported"] < night["hours"] - 2:
        lines.append(f"Only {night['hours_reported']} of {night['hours']} overnight hours have reported.")
    today = None
    if fresh and prev.get("tomorrow"):
        today = prev["tomorrow"]
        lines.append(f"Today, 5 am to 5 am: {today[0]:g} in likely ({today[1]:g} to {today[2]:g}), from last evening's run.")
    plan = planning()
    if plan.get("wind") and plan["wind"]["holds"]:
        lines.append("Wind hold " + ", ".join(f"{h['risk']} on {h['name']}" for h in plan["wind"]["holds"][:4]) + f" (ridge gusts near {plan['wind']['ridge_gust_mph']} mph).")
    title = f"{resort.name} at 5 am: {night['water_in']:.2f} in water overnight" + ("" if night["depth_change_in"] is None else f", depth {night['depth_change_in']:+g} in")
    return {"kind": "morning", "time": now.isoformat(timespec="minutes"), "zone": zone.name, "thresholds": {k: cfg[k] for k in ("likely_in", "measured_in")},
            "fire": bool(reasons), "available": True, "reasons": reasons, "title": title, "lines": lines, "overnight": night, "last_24h": day,
            "estimate_in": est, "today": today}


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------

def text(alert: dict) -> str:
    return alert["title"] + "\n" + "\n".join(alert["lines"])


def deliver(resort: Resort, alert: dict, dry_run: bool = False, force: bool = False) -> dict:
    """Record the alert, and send it on the configured channels if a threshold was met (or `force`).
    The same alert is not sent twice in a day."""
    d = state_dir()
    if alert["kind"] == "evening" and alert.get("available"):
        (d / "evening.json").write_text(json.dumps(alert, indent=1))
    public = {k: alert.get(k) for k in ("kind", "time", "zone", "fire", "title", "lines", "reasons", "thresholds")}
    if not dry_run:
        (web_data_dir() / "alert.json").write_text(json.dumps(public, separators=(",", ":")))
    sent_path = d / "sent.json"
    sent = json.loads(sent_path.read_text()) if sent_path.exists() else {}
    key = f"{alert['kind']}:{(pd.Timestamp(alert['time']) - pd.Timedelta(hours=7)).date()}"
    result = {"fire": alert["fire"], "sent": [], "skipped": None}
    if not (alert["fire"] or force):
        result["skipped"] = "no threshold met"
    elif dry_run:
        result["skipped"] = "dry run"
    elif key in sent and not force:
        result["skipped"] = "already sent today"
    else:
        for channel in settings(resort)["channels"]:
            try:
                SEND[channel](alert)
                result["sent"].append(channel)
            except Exception as e:
                result.setdefault("failed", {})[channel] = f"{type(e).__name__}: {e}"
        sent[key] = {"time": alert["time"], "channels": result["sent"]}
        sent_path.write_text(json.dumps(sent, indent=1))
    return result


def to_file(alert: dict) -> None:
    out = state_dir() / "outbox"
    out.mkdir(exist_ok=True)
    stamp = pd.Timestamp(alert["time"]).strftime("%Y%m%d_%H%M")
    (out / f"{stamp}_{alert['kind']}.txt").write_text(text(alert) + "\n")


def to_mac(alert: dict) -> None:
    """A notification on this computer. Nothing leaves the machine."""
    body = " ".join(alert["lines"][:2]).replace("\\", "").replace('"', "'")
    title = alert["title"].replace("\\", "").replace('"', "'")
    subprocess.run(["osascript", "-e", f'display notification "{body}" with title "{title}"'], check=True, timeout=20)


def to_ntfy(alert: dict) -> None:
    """A phone push through ntfy.sh. Sends the alert text to that service; needs NTFY_TOPIC in .env."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("NTFY_TOPIC is not set in .env")
    r = requests.post(f"https://ntfy.sh/{topic}", data="\n".join(alert["lines"]).encode(), headers={"Title": alert["title"].encode("ascii", "ignore").decode()}, timeout=30)
    r.raise_for_status()


SEND = {"file": to_file, "mac": to_mac, "ntfy": to_ntfy}


# ---------------------------------------------------------------------------
# What the thresholds would have done in past winters
# ---------------------------------------------------------------------------

def backtest(resort: Resort | None = None, likely_in: float | None = None, high_end_in: float | None = None) -> dict:
    """Run the alert rule over the held-out hindcast (24 h forecasts issued at 5 am for the day to
    5 am), at the mid-mountain gauge. 'Snow' on both sides is gauge water times the day's modelled
    snow-to-liquid ratio, so the test is of the water forecast and the rule, not of the ratio."""
    resort = resort or load_resort()
    cfg = settings(resort)
    likely_in, high_end_in = likely_in or cfg["likely_in"], high_end_in or cfg["high_end_in"]
    m = data_dir() / "model"
    h = pd.read_parquet(m / "hindcast_tier24.parquet").set_index("start")
    c = pd.read_parquet(m / "settle_cases.parquet")
    c = c[c["site"] == scorecard.target(resort)["id"]].set_index("start")
    ratio = (c["ratio"] if "ratio" in c else pd.Series(13.0, index=c.index)).reindex(h.index).fillna(13.0)
    d = pd.DataFrame({"likely": h["q50"] * ratio, "high": h["q90"] * ratio, "fell": h["obs"] * ratio, "season": h["season"]}).dropna()
    d["alert"] = (d["likely"] >= likely_in) | (d["high"] >= high_end_in)
    d["powder"] = d["fell"] >= likely_in
    seasons = d["season"].nunique()
    fired = d[d["alert"]]
    return {"likely_in": likely_in, "high_end_in": high_end_in, "seasons": int(seasons), "days": int(len(d)),
            "alerts_per_season": round(len(fired) / seasons, 1), "powder_days_per_season": round(float(d["powder"].sum()) / seasons, 1),
            "alerts_that_were_powder_days_pct": None if fired.empty else round(100 * float(fired["powder"].mean())),
            "powder_days_alerted_pct": None if not d["powder"].any() else round(100 * float(d.loc[d["powder"], "alert"].mean())),
            "median_fell_on_alert_days_in": None if fired.empty else round(float(fired["fell"].median()), 1),
            "by_rule": {"likely only": int((d["likely"] >= likely_in).sum()), "high end only": int(((d["high"] >= high_end_in) & (d["likely"] < likely_in)).sum())}}
