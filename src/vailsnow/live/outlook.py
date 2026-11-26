"""The 10-day outlook: ten 5 am to 5 am days, rebuilt whenever a new ensemble run is out.

Days the high-resolution forecast covers (normally the first three) are taken from it. The rest
come from the newest complete GEFS run through the long-range model (`model/extended.py`). GEFS
runs four times a day and its 10-day files are complete about 5.5 hours after each run, so the
outlook can be refreshed at about 10:40 pm, 4:40 am, 10:40 am and 4:40 pm Mountain Standard Time.

Every refresh is logged. For each day the file carries the earlier forecasts made for it, so
the map can show how the call has moved, and the scorecard later sets each one against the gauge.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ..fcst import archive, ecmwf, grib
from ..model import exposure, extended, precip
from ..model import zones as model_zones
from ..model.snowlevel import KELVIN, at_elevation, snow_fraction
from ..resort import Resort, data_dir, load_resort, web_data_dir
from . import grid
from .forecast import combine_ranges, compass, density_word

DAYS = 10
CYCLE_H = 6
SNOW_THRESHOLDS_IN = (1.0, 3.0, 6.0)
SHOW_RANGE_FROM = 0.20       # a day with at least a 1-in-5 chance of an inch shows the range expected if it snows; below it, 0.
                             # A judgment call, lowered from 0.30 on 2026-11-25: a 27% day was reading 0 beside a 1 to 4.5 in range
AFTER_MIDNIGHT_H = 5         # a forecast window ends at 5 am (12 UTC): its last five hours belong to the next calendar day
DRAW_FROM_IN = 0.5           # an expected amount under half an inch is not drawn: it means "probably nothing"
LIVE_WITHIN = pd.Timedelta(hours=12)


def log_path() -> Path:
    return data_dir() / "log" / "outlook.jsonl"


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def complete(run: pd.Timestamp) -> bool:
    """True once the run's last file needed here has been published."""
    url = f"{archive.GEFS}/gefs.{run:%Y%m%d}/{run:%H}/atmos/pgrb2ap5/geavg.t{run:%H}z.pgrb2a.0p50.f{archive.LONG_LAST:03d}.idx"
    return archive.path("gefs_240", run).exists() or grib.get(url, optional=True) is not None


def newest_run(now: pd.Timestamp) -> pd.Timestamp:
    """The newest of the four daily GEFS runs whose 10-day files are all out."""
    run = now.floor(f"{CYCLE_H}h")
    for _ in range(8):
        if complete(run):
            return run
        run -= pd.Timedelta(hours=CYCLE_H)
    raise RuntimeError("no complete GEFS run in the last two days")


def reference(now: pd.Timestamp) -> pd.Timestamp:
    """5 am (12z) at the start of day 1: the latest one not in the future."""
    ref = now.normalize() + pd.Timedelta(hours=12)
    return ref if ref <= now else ref - pd.Timedelta(days=1)


def if_it_snows(levels: list[float], values: list[float], chance_of_snow: float) -> float | None:
    """The middle amount among the outcomes where it does snow: the quantile halfway up the snowy
    part of the range. None when snow is too unlikely for the stored quantiles to say."""
    if chance_of_snow < 0.1:
        return None
    return float(np.interp(1.0 - chance_of_snow / 2.0, levels, np.maximum.accumulate(np.asarray(values, float))))


def late_share(amounts) -> float:
    """Share of a 5 am to 5 am window's precipitation that falls after midnight, from amounts in
    equal steps through the window (hourly, or six-hourly, where the last step is split evenly).
    With nothing to go on, or a dry window, the five hours count for five twenty-fourths."""
    a = np.asarray(list(amounts), float)
    even = AFTER_MIDNIGHT_H / 24.0
    if a.size == 0 or not np.isfinite(a).all() or a.sum() < 1e-4:
        return even
    step = 24.0 / a.size
    ends = step * (np.arange(a.size) + 1)
    inside = np.clip(ends - np.maximum(ends - step, 24.0 - AFTER_MIDNIGHT_H), 0.0, step) / step      # part of each step after midnight
    return float(np.clip((np.clip(a, 0, None) * inside).sum() / np.clip(a, 0, None).sum(), 0.0, 1.0))


def lead_range(levels: list[float], water: list[float], chance_of_snow: float, inch_of_snow: float) -> tuple[float, float, bool]:
    """The range a day leads with, in water at the gauge, and whether it is conditional on snow falling.

    When even the low side of the likely range is real snow, that range is shown as it is. When
    snow is uncertain but has a fair chance, the range shown is what falls if it does snow: the
    middle half of the snowy outcomes, never starting below an inch. When the chance is low the
    day reads zero. `inch_of_snow` is the water that makes an inch of snow in the zone."""
    q25, q75 = float(np.interp(0.25, levels, water)), float(np.interp(0.75, levels, water))
    if q25 >= 0.5 * inch_of_snow:
        return q25, q75, False
    if chance_of_snow < SHOW_RANGE_FROM:
        return 0.0, 0.0, False
    dry = 1.0 - chance_of_snow
    values = np.maximum.accumulate(np.asarray(water, float))
    low = max(float(np.interp(dry + 0.25 * chance_of_snow, levels, values)), inch_of_snow)
    high = max(float(np.interp(dry + 0.75 * chance_of_snow, levels, values)), low)
    return low, high, True


def chance(levels: list[float], values: list[float], threshold: float) -> float:
    """Chance of at least `threshold` from a few quantiles, with straight lines between them."""
    v = np.maximum.accumulate(np.asarray(values, float))
    if threshold <= v[0]:
        return 1.0 - levels[0] / 2
    if threshold > v[-1]:
        return (1.0 - levels[-1]) / 2
    return float(1.0 - np.interp(threshold, v, levels))


# ---------------------------------------------------------------------------
# water at the gauge to snow in a zone, when all there is for temperature is 700 mb
# ---------------------------------------------------------------------------

def conversion_fit(resort: Resort) -> dict:
    """Two relations read off the forecast table, for days with no temperature profile:
    the snow-to-liquid ratio the density model gave against 700 mb temperature, and the
    near-surface wet-bulb profile against 700 mb temperature."""
    m = data_dir() / "model"
    c = pd.read_parquet(m / "settle_cases.parquet")
    c = c[(c["site"] == next(s["id"] for s in resort.snotel if s.get("target"))) & (c["water"] >= 0.05)]
    edges = np.arange(-24.0, 2.1, 2.0)
    mid, ratio = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        sel = c[(c["t700_c"] >= a) & (c["t700_c"] < b)]
        if len(sel) >= 15:                                   # weighted by water: the ratio that turns a day's water into its snow
            mid.append(float((a + b) / 2))
            ratio.append(float((sel["ratio"] * sel["water"]).sum() / sel["water"].sum()))
    t = pd.read_parquet(m / "table.parquet")
    # wet days only: air that is precipitating is moister, so its wet-bulb runs 1 to 3 degrees warmer than on all days
    t = t[(t["tier"] == 24) & t["tw_ref"].notna() & t["t700"].notna() & (t["qpf_3x3"] >= 0.05)]
    slope, intercept = np.polyfit(t["t700"].to_numpy(float), t["tw_ref"].to_numpy(float), 1)
    return {"t700_c": mid, "ratio": ratio, "tw_ref_slope": float(slope), "tw_ref_intercept": float(intercept), "tw_per_km": float(t["tw_per_km"].median())}


def zone_table(resort: Resort) -> list[dict]:
    g = grid.static(resort)
    out = []
    for z in resort.zones:
        sel = g["zone"] == z.index
        if sel.any():
            out.append({"id": z.id, "name": z.name, "prism": float(g["prism"][sel].mean()), "elev_m": float(g["elev"][sel].mean())})
    return out


def gauge_per_inch(fit: dict, resort: Resort, t700_k: float) -> float:
    """Inches of snow at the gauge per inch of water there, from the 700 mb temperature."""
    elev = next(x for x in resort.snotel if x.get("target"))["elev_ft"] / 3.280839895
    field, _ = snow_per_inch(fit, {"prism": np.array([1.0]), "elev": np.array([elev])}, t700_k)
    return float(field[0])


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------

def read_log(path: Path | None = None) -> pd.DataFrame:
    path = path or log_path()
    if not path.exists():
        return pd.DataFrame()
    df = pd.DataFrame([json.loads(line) for line in path.read_text().splitlines() if line.strip()])
    df["run"], df["start"] = pd.to_datetime(df["run"]), pd.to_datetime(df["start"])
    return df.drop_duplicates(["run", "start"], keep="first").sort_values(["start", "run"]).reset_index(drop=True)


def append(entries: list[dict], path: Path | None = None) -> int:
    """Add entries not already logged. A logged forecast is never replaced."""
    path = path or log_path()
    have = set()
    if path.exists():
        have = {(e["run"], e["start"]) for e in (json.loads(line) for line in path.read_text().splitlines() if line.strip())}
    fresh = [e for e in entries if (e["run"], e["start"]) not in have]
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for e in fresh:
                f.write(json.dumps(e) + "\n")
    return len(fresh)


def blend_for(gefs_run: pd.Timestamp) -> tuple[pd.Timestamp, dict | None]:
    """The National Blend run to go with an ensemble run. The model was fitted with the Blend cycle
    seven hours after the ensemble run (the first to hold that hour's GFS and European runs). When
    that one is not out yet, the cycle one hour after the ensemble run is used: older guidance, and
    the outlook says which it has."""
    for lag in (extended.BLEND_LAG_H, 1):
        run = gefs_run + pd.Timedelta(hours=lag)
        if archive.path("nbm_240", run).exists():
            d = archive.load("nbm_240", run)
            if d is not None:
                return run, d
            continue
        if run > now_utc():
            continue
        last = max(f for f in range(6, archive.LONG_LAST + 1) if (run.hour + f) % 6 == 0)
        url = f"{archive.NBM}/blend.{run:%Y%m%d}/{run:%H}/core/blend.t{run:%H}z.core.f{last:03d}.co.grib2.idx"
        try:
            if grib.get(url, optional=True) is not None and archive.fetch_nbm_long(run, first=6, snow=True) != "absent":
                return run, archive.load("nbm_240", run)
        except Exception:
            continue
    return gefs_run, None


def european_for(gefs_run: pd.Timestamp, now: pd.Timestamp | None = None) -> tuple[pd.Timestamp, dict | None]:
    """The European ensemble run to go with a GEFS run. Live, the newest 00z or 12z run that is
    all out (about 7 h 45 min after it starts), which is never older than the pairing the model was
    fitted with. For a replay of an old run, the run twelve hours before it, as fitted. The
    outlook stands without it: the model was fitted with gaps in this input."""
    now = now or now_utc()
    live = pd.Timedelta(0) <= now - gefs_run <= LIVE_WITHIN
    first = (now - ecmwf.OUT_AFTER if live else gefs_run - pd.Timedelta(hours=extended.EUROPEAN_LAG_H)).floor("12h")
    for back in range(3 if live else 1):
        run = first - pd.Timedelta(hours=12 * back)
        try:
            if ecmwf.fetch(run) != "absent":
                d = ecmwf.load(run)
                if d is not None:
                    return run, d
        except Exception:
            continue
    return gefs_run, None


def day_wind(w: dict | None, r: int, c: int, first_hour: int) -> tuple[float, float] | None:
    """(direction the wind blows from, speed in m/s) near 600 mb for the 24 hours starting `first_hour`
    hours into a run, from the ensemble-mean wind. Members that disagree on direction cancel in the
    mean, so the speed (and with it the loading) falls away on its own as the forecast gets less sure."""
    if w is None:
        return None
    u, v = [], []
    for s in (6, 12, 18, 24):
        keys = [f"{k}_{first_hour + s:03d}" for k in ("u700", "u500", "v700", "v500")]
        if all(k in w for k in keys):
            u.append(0.5 * (w[keys[0]][r, c] + w[keys[1]][r, c]))
            v.append(0.5 * (w[keys[2]][r, c] + w[keys[3]][r, c]))
    if len(u) < 2:
        return None
    um, vm = float(np.mean(u)), float(np.mean(v))
    return float(np.degrees(np.arctan2(-um, -vm)) % 360.0), float(np.hypot(um, vm))


def ensemble_days(resort: Resort, run: pd.Timestamp, starts: list[pd.Timestamp]) -> dict[pd.Timestamp, dict]:
    """The long-range model's forecast for each target day a GEFS run can reach."""
    status = archive.fetch_gefs_long(run, first=6)
    d = archive.load("gefs_240", run)
    if status == "absent" or d is None:
        return {}
    target = next(s for s in resort.snotel if s.get("target"))
    r, c = grib.nearest(d["lat"], d["lon"], target["lat"], target["lon"])
    fit = joblib.load(extended.paths()[3])["conversion"]
    blend_run, nb = blend_for(run)
    try:                                                      # wind for lee loading; the outlook stands without it
        archive.fetch_gefs_wind(run)
        winds = archive.load("gefs_wind", run)
    except Exception:
        winds = None
    nr, nc = grib.nearest(nb["lat"], nb["lon"], target["lat"], target["lon"]) if nb is not None else (0, 0)
    ec_run, ec = european_for(run)
    ew = ecmwf.weights(ec["lat"], ec["lon"], target["lat"], target["lon"]) if ec is not None else None
    out = {}
    for start in starts:
        first = int((start - run) / pd.Timedelta(hours=1))
        f = extended.window_features(d, r, c, first) if first >= 0 else None
        if f is None or not np.isfinite([f["gefs_mean_prev"], f["gefs_mean_next"]]).all():
            continue
        f.update(extended.blend_features(nb, nr, nc, int((start - blend_run) / pd.Timedelta(hours=1)) if nb is not None else -1))
        f.update(ecmwf.features(ec, ew, int((start - ec_run) / pd.Timedelta(hours=1)) if ec is not None else -1))
        p = extended.predict(f)
        b0 = int((start - blend_run) / pd.Timedelta(hours=1)) if nb is not None else -1
        keys = [f"asnow_{b0 + k:03d}" for k in (6, 12, 18, 24)]
        blend_snow = float(sum(nb[k][nr, nc] for k in keys) * 39.3701) if nb is not None and all(k in nb for k in keys) else None
        six = [f"apcp_{b0 + k:03d}" for k in (6, 12, 18, 24)]
        gefs6 = [f"mean_{first + k:03d}" for k in (6, 12, 18, 24)]
        timing = [float(nb[k][nr, nc]) for k in six] if nb is not None and all(k in nb for k in six) else [float(d[k][r, c]) for k in gefs6] if all(k in d for k in gefs6) else []
        out[start] = {**p, "late": late_share(timing), "lead_h": first + 24, "lead_day": f["lead_day"], "t700": f["t700"], "raw": f["gefs_mean"], "blend": f.get("nbm_qpf", np.nan), "blend_snow_in": blend_snow,
                      "blend_run": blend_run.isoformat() if nb is not None else None, "wind": day_wind(winds, r, c, first),
                      "european": f["ec_mean"], "european_run": ec_run.isoformat() if ec is not None else None,
                      "per_inch": gauge_per_inch(fit, resort, f["t700"])}
    return out


def log_entries(run: pd.Timestamp, days: dict[pd.Timestamp, dict], logged_at: pd.Timestamp) -> list[dict]:
    mode = "live" if pd.Timedelta(0) <= logged_at - run <= LIVE_WITHIN else "replay"
    # only true long-range leads are logged: a nearer day shown from the ensemble as a stopgap would spoil the by-lead scores
    return [{"run": run.isoformat(), "start": s.isoformat(), "lead_h": d["lead_h"], "lead_day": d["lead_day"], "mode": mode,
             "logged_at": logged_at.isoformat(timespec="seconds"), **{k: round(d[k], 3) for k in ("q10", "q50", "q90", "mean", "p_wet", "raw")}, "per_inch": round(d["per_inch"], 2)}
            for s, d in sorted(days.items()) if round(d["lead_h"] / 24.0) >= extended.LEADS[0]]


def replay(resort: Resort | None = None, first: str | None = None) -> dict:
    """Log the long-range forecasts this season's earlier 12z runs would have made (marked replay)."""
    resort = resort or load_resort()
    now = now_utc()
    season = now.year + (now.month >= 10)
    counts = {"logged": 0, "runs": 0}
    for day in pd.date_range(pd.Timestamp(first) if first else pd.Timestamp(f"{season - 1}-11-01"), now.normalize() - pd.Timedelta(days=1), freq="D"):
        run = day + pd.Timedelta(hours=12)
        if day.month not in precip.WINTER or not complete(run):
            continue
        days = ensemble_days(resort, run, [run + pd.Timedelta(days=k - 1) for k in extended.LEADS])
        counts["logged"] += append(log_entries(run, days, now))
        counts["runs"] += 1
    return counts


# ---------------------------------------------------------------------------
# the file the map reads
# ---------------------------------------------------------------------------

def main_forecast(starts: list[pd.Timestamp]) -> dict[pd.Timestamp, dict]:
    """Days of the strip that the newest high-resolution forecast covers, with its zone numbers."""
    base = web_data_dir()
    if not (base / "runs.json").exists():
        return {}
    runs = json.loads((base / "runs.json").read_text())
    out: dict[pd.Timestamp, dict] = {}
    for r in runs[:3]:                                       # newest first; an older run fills days a newer one does not reach
        d = base / r["name"]
        meta, zones = json.loads((d / "meta.json").read_text()), json.loads((d / "zones.json").read_text())
        for day in meta["days"]:
            start = pd.Timestamp(day["start"]) if day.get("available") else None
            if start in starts and start not in out and all("days" in z for z in zones):
                curve = meta.get("hourly_curve_in") or []
                hours = np.diff(curve[24 * (day["day"] - 1): 24 * day["day"] + 1]) if len(curve) > 24 * day["day"] else []
                out[start] = {"run": meta["run"], "day": day["day"], "water": day["water_in"], "quality": day["quality"], "ratio": day["snow_to_liquid_mid"], "late": late_share(hours),
                              "zones": {z["id"]: z["days"][day["day"] - 1] for z in zones}}
    return out


def morning_measured(resort: Resort, five_am: pd.Timestamp, fallback_ratio: float | None = None) -> dict | None:
    """New snow in the 24 hours before `five_am`, as the mid-mountain gauge measured it: the figure
    day 1's morning total shows, since that morning has already happened. Water comes from the
    gauge; inches of snow are that water times the snow ratio the forecast gave for the same day.
    None when the gauge cannot be read."""
    from ..extras import alerts, scorecard
    try:
        rec = scorecard.gauge_record(resort, max_age_h=1.0)
        got = alerts.measured(rec, five_am - pd.Timedelta(hours=24), five_am)
    except Exception:
        return None
    if not got["hours_reported"]:
        return None
    per_inch = None
    log = scorecard.read_log()
    if len(log):
        day = log[log["start"] == five_am - pd.Timedelta(hours=24)].sort_values("run")
        if len(day) and np.isfinite(day["ratio"].iloc[-1]):
            per_inch = float(day["ratio"].iloc[-1]) * float(day["snow_share"].iloc[-1] if np.isfinite(day["snow_share"].iloc[-1]) else 1.0)
    if per_inch is None and fallback_ratio:
        per_inch = float(fallback_ratio)
    site = scorecard.target(resort)
    late = alerts.measured(rec, five_am - pd.Timedelta(hours=AFTER_MIDNIGHT_H), five_am)
    return {"at": five_am.isoformat(), "site": site["name"], "elev_ft": site["elev_ft"], "water_in": got["water_in"],
            "snow_in": None if per_inch is None else round(got["water_in"] * per_inch, 1), "depth_change_in": got["depth_change_in"],
            "hours_reported": got["hours_reported"], "hours": got["hours"],
            "since_midnight_water_in": late["water_in"], "since_midnight_snow_in": None if per_inch is None else round(late["water_in"] * per_inch, 1)}


QUANTILES = ("q10", "q25", "q50", "q75", "q90")
LEVELS5 = [0.1, 0.25, 0.5, 0.75, 0.9]


def calendar_days(days: list[dict], buckets: dict[int, dict], starts: list[pd.Timestamp], measured: dict | None, head: str) -> tuple[list[dict], dict[int, dict]]:
    """The forecast re-cut from 5 am to 5 am windows into calendar days, midnight to midnight.

    The models forecast 5 am to 5 am, because that is how the gauge record they learned from is
    cut. A calendar day is the last five hours of one window plus the first nineteen of the next,
    so each window is split by when its precipitation is forecast to fall (`late`). Quantiles of
    the two parts are added level by level, which treats a wet end to one window and a wet start
    to the next as going together: they are usually the same storm. The first day's hours before
    5 am have already happened and come from the gauge.

    Each row also carries the morning total: the whole window ending at 5 am that day."""
    rows, fields = [], {}
    for k, start in enumerate(starts, 1):
        row = {"day": k, "start": (start - pd.Timedelta(hours=AFTER_MIDNIGHT_H)).isoformat(), "available": False}
        b, prev = buckets.get(k), buckets.get(k - 1)
        if b is None or (k > 1 and prev is None) or b["conv"].get(head, 0) <= 0:
            rows.append(row)
            continue
        keep = 1.0 - b["late"]                                   # share of this window that falls before midnight
        head_c = b["conv"][head]
        if k == 1:                                                # midnight to 5 am today: measured, not forecast
            got = (measured or {}).get("since_midnight_snow_in") or 0.0
            got_w = (measured or {}).get("since_midnight_water_in") or 0.0
            before = {q: got for q in (*QUANTILES, "mean")}
            before_w = {q: got_w for q in (*QUANTILES, "mean")}
            before_zone = {z: got * c / head_c for z, c in b["conv"].items()}
            before_field = {q: got * b["pi"] / head_c for q in (*QUANTILES, "mean")}
        else:
            before = {q: prev["late"] * prev["w"][q] * prev["conv"][head] for q in (*QUANTILES, "mean")}
            before_w = {q: prev["late"] * prev["w"][q] for q in (*QUANTILES, "mean")}
            before_zone = {z: prev["late"] * prev["w"]["mean"] * prev["conv"].get(z, 0.0) for z in b["conv"]}
            before_field = {q: prev["late"] * prev["w"][q] * prev["pi"] for q in (*QUANTILES, "mean")}
        snow = {q: before[q] + keep * b["w"][q] * head_c for q in (*QUANTILES, "mean")}       # inches at the headline zone
        water = {q: before_w[q] + keep * b["w"][q] for q in (*QUANTILES, "mean")}
        mean = snow["mean"]
        zone_mean = {z: before_zone[z] + keep * b["w"]["mean"] * c for z, c in b["conv"].items()}
        scale = {z: (zone_mean[z] / mean if mean > 0.02 else c / head_c) for z, c in b["conv"].items()}      # each zone against the headline zone
        values = [snow[q] for q in QUANTILES]
        odds = {f"{int(t)}in": round(100 * chance(LEVELS5, values, t)) for t in SNOW_THRESHOLDS_IN}
        lo, hi, conditional = lead_range(LEVELS5, values, odds["1in"] / 100.0, 1.0)
        wet = if_it_snows(LEVELS5, values, odds["1in"] / 100.0)
        src = days[k - 1]
        row = {**src, "day": k, "start": row["start"], "window": "midnight to midnight", "after_midnight_share": round(b["late"], 2),
               "zones": {z: [round(snow[q] * c, 1) for q in ("q50", "q10", "q90")] for z, c in scale.items()},
               "mid": {z: [round(snow["q25"] * c, 1), round(snow["q75"] * c, 1)] for z, c in scale.items()},
               "expected": {z: round(mean * c, 1) for z, c in scale.items()},
               "water": [round(water[q], 2) for q in ("q50", "q10", "q90")], "expected_water": round(water["mean"], 2),
               **{name: (round(snow[q] / mean, 3) if mean > 0.002 else 0.0) for name, q in (("low_ratio", "q10"), ("mid_ratio", "q50"), ("mid_lo_ratio", "q25"), ("mid_hi_ratio", "q75"), ("high_ratio", "q90"))},
               "chance": odds, "lead": {z: [round(lo * c, 1), round(hi * c, 1)] for z, c in scale.items()}, "lead_if_snow": conditional,
               "lead_lo_ratio": round(lo / mean, 3) if mean > 0.002 else 0.0, "lead_hi_ratio": round(hi / mean, 3) if mean > 0.002 else 0.0,
               "if_it_snows": None if wet is None else {z: round(max(wet * c, 1.0), 1) for z, c in scale.items()}}
        if k > 1 and src.get("blend_snow_in") is not None and days[k - 2].get("blend_snow_in") is not None:
            row["blend_snow_in"] = round(prev["late"] * days[k - 2]["blend_snow_in"] + keep * src["blend_snow_in"], 1)
        # the morning total: everything in the window that ends at 5 am this day
        if k == 1:
            row["morning"] = None if measured is None else {"measured": measured}
        else:
            p = days[k - 2]
            row["morning"] = {"lead": p["lead"], "lead_if_snow": p["lead_if_snow"], "chance": p["chance"]["1in"], "expected": p["expected"]}
        field = {q: before_field[q] + keep * b["w"][q] * b["pi"] for q in ("mean", "q10", "q50", "q90")}
        pattern = field["mean"] / mean if mean > 0.02 else b["pi"] / head_c
        fields[k] = {"expected": field["mean"], "likely": field["q50"], "low": field["q10"], "high": field["q90"], "water": water["mean"], "lead": 0.5 * (lo + hi) * pattern}
        rows.append(row)
    return rows, fields


def cell_fields(resort: Resort) -> dict:
    """The 30 m grid pieces the map needs: which cells are ski terrain, their elevation, long-term pattern and zone."""
    g = grid.static(resort)
    inside = g["zone"] > 0
    return {"g": g, "inside": inside, "elev": g["elev"][inside], "prism": g["prism"][inside], "zone": g["zone"][inside]}


def snow_per_inch(fit: dict, cells: dict, t700_k: float) -> tuple[np.ndarray, float]:
    """(inches of snow on every cell per inch of water at the gauge, snow-to-liquid ratio) from the
    700 mb temperature: the long-term pattern, times the share falling as snow at each cell's
    elevation, times the ratio. This is all that separates one part of the mountain from another
    past day 3."""
    if not np.isfinite(t700_k):
        return cells["prism"] * 13.0, 13.0
    ratio = float(np.interp(t700_k - KELVIN, fit["t700_c"], fit["ratio"]))
    tw = at_elevation(fit["tw_ref_intercept"] + fit["tw_ref_slope"] * t700_k, fit["tw_per_km"], cells["elev"]) - KELVIN
    return cells["prism"] * snow_fraction(tw) * ratio, ratio


def main_per_inch(run_iso: str, day: int) -> np.ndarray | None:
    """The high-resolution forecast's own snow-per-inch field for one of its days, if it was kept."""
    path = data_dir() / "live" / f"per_inch_{pd.Timestamp(run_iso):%Y%m%d%H}.npz"
    if not path.exists():
        return None
    with np.load(path) as z:
        field = z["per_inch"][day - 1].astype(float)
    return field if np.isfinite(field).all() else None


def write_map(resort: Resort, cells: dict, fields: dict[int, dict], total: dict | None) -> None:
    """Contours for each day and for the ten-day total, and a 60 m grid for tap-anywhere lookups."""
    from . import contours
    g, inside = cells["g"], cells["inside"]
    out = web_data_dir() / "outlook"
    out.mkdir(parents=True, exist_ok=True)
    x, y = g["x"][0], g["y"][:, 0]

    def full(values: np.ndarray) -> np.ndarray:
        a = np.full(g["elev"].shape, np.nan)
        a[inside] = values
        return a

    def put(name: str, values: np.ndarray) -> None:
        (out / f"{name}.geojson").write_text(json.dumps(contours.bands(full(values), x, y, resort.epsg), separators=(",", ":")))

    for k, f in fields.items():
        for kind in ("expected", "low", "high"):
            put(f"day_{k:02d}_{kind}", np.where(f[kind] >= DRAW_FROM_IN, f[kind], np.nan) if kind == "expected" else f[kind])
        # the layer behind the day's own figure: the middle of the range it leads with, everywhere on the mountain.
        # On an "if it snows" day that is what falls if it does snow; on a day that reads 0 it is empty.
        put(f"day_{k:02d}_lead", f["lead"])
    if total is not None:
        for kind in ("expected", "low", "high"):
            put(f"total_{kind}", total[kind])
    step = 2
    thin_inside = grid.block_mean(np.where(inside, 1.0, np.nan), step)
    keep = np.isfinite(thin_inside).ravel()
    zone_thin = g["zone"][step // 2::step, step // 2::step][: thin_inside.shape[0], : thin_inside.shape[1]].ravel()[keep]
    point = {"rows": thin_inside.shape[0], "cols": thin_inside.shape[1], "fit": grid.lonlat_to_cell(g, 1), "step": step, "scale": 10,
             "cells": [int(v) for v in np.nonzero(keep)[0]], "zone": [int(v) for v in zone_thin],
             "elev_ft": [int(v) for v in np.round(grid.block_mean(full(cells["elev"] * 3.28084), step).ravel()[keep])]}
    for k, f in fields.items():
        point[f"d{k:02d}"] = [int(v) for v in np.round(grid.block_mean(full(f["expected"]), step).ravel()[keep] * 10)]
    (out / "grid.json").write_text(json.dumps(point, separators=(",", ":")))


def build(resort: Resort | None = None, now: pd.Timestamp | None = None, write: bool = True) -> dict:
    resort = resort or load_resort()
    now = now or now_utc()
    ref, run = reference(now), newest_run(now)
    starts = [ref + pd.Timedelta(days=k) for k in range(DAYS)]
    ens = ensemble_days(resort, run, starts)
    logged = append(log_entries(run, ens, now_utc())) if write else 0
    main = main_forecast(starts)
    final = joblib.load(extended.paths()[3])
    fit, zones, cells = final["conversion"], zone_table(resort), cell_fields(resort)
    try:
        layers = exposure.load_layers(resort)
    except Exception:
        layers = None
    index = {z.id: z.index for z in resort.zones}
    head = (resort.view or {}).get("headline_zone") or zones[0]["id"]
    skill = json.loads(extended.paths()[2].read_text())
    history = read_log()
    from ..extras import scorecard                              # the three-day forecasts already logged join the trail
    near = scorecard.read_log()
    if len(near):
        both = pd.concat([near[["run", "start", "mean", "q90"]], history[["run", "start", "mean", "q90"]] if len(history) else None])
        history = both.drop_duplicates(["run", "start"], keep="first").sort_values(["start", "run"]).reset_index(drop=True)
    levels5, keys5 = [0.1, 0.25, 0.5, 0.75, 0.9], ("q10", "q25", "q50", "q75", "q90")
    days, fields, buckets = [], {}, {}
    for k, start in enumerate(starts, 1):
        row = {"day": k, "start": start.isoformat(), "available": False}
        if start in main:
            m = main[start]
            w, kept = m["water"], main_per_inch(m["run"], m["day"])
            pi, ratio = (kept, m["ratio"]) if kept is not None else (cells["prism"] * m["ratio"], m["ratio"])
            row.update({"available": True, "source": "high-resolution", "issued": m["run"], "zones": m["zones"], "quality": m["quality"], "ratio": ratio})
        elif start in ens:
            w = ens[start]
            pi, ratio = snow_per_inch(fit, cells, w["t700"])
            wind_note = None
            if w.get("wind") and layers is not None:              # lee loading from the ensemble's wind for the day
                ridge_from = model_zones.ridge_direction(w["wind"][0])
                ridge_ms = model_zones.RIDGE_WIND_SHARE * w["wind"][1]
                tw_ridge = float(at_elevation(fit["tw_ref_intercept"] + fit["tw_ref_slope"] * w["t700"], fit["tw_per_km"], float(np.quantile(cells["elev"], 0.98)))) - KELVIN
                strength = exposure.lee_strength(ridge_ms, ratio, tw_ridge) if np.isfinite(w["t700"]) else 0.0
                if strength > 0:
                    whole = np.full(cells["g"]["elev"].shape, np.nan)
                    whole[cells["inside"]] = pi
                    loaded = exposure.lee_load(grid.upsample(whole, tuple(int(v) for v in cells["g"]["shape10"])), exposure.load_sx(resort, ridge_from),
                                               layers["ridge_distance"], layers["canopy_cover"], layers["height"], strength)
                    pi = grid.block_mean(loaded)[cells["inside"]]
                wind_note = {"from": compass(ridge_from), "mph": round(ridge_ms * 2.237), "strength": round(strength, 2)}
            row.update({"available": True, "source": "ensemble", "wind": wind_note, "issued": run.isoformat(), "lead_day": w["lead_day"], "quality": density_word(ratio), "ratio": round(ratio, 1),
                        "skill_pct": skill["by_lead"][str(w["lead_day"])]["skill_pct"],
                        "blend_snow_in": None if w.get("blend_snow_in") is None else round(w["blend_snow_in"], 1),
                        "blend_water_in": None if not np.isfinite(w.get("blend", np.nan)) else round(float(w["blend"]), 2),
                        "european_water_in": None if not np.isfinite(w.get("european", np.nan)) else round(float(w["european"]), 2)})
        if row["available"]:
            conv = {z["id"]: float(pi[cells["zone"] == index[z["id"]]].mean()) for z in zones}
            if row["source"] == "ensemble":
                row["zones"] = {zid: [round(w[q] * c, 1) for q in ("q50", "q10", "q90")] for zid, c in conv.items()}
                row["mid"] = {zid: [round(w["q25"] * c, 1), round(w["q75"] * c, 1)] for zid, c in conv.items()}
            else:                                                 # the high-resolution forecast carries its own likely range per zone
                row["mid"] = {zid: [v[3], v[4]] if len(v) >= 5 else [v[0], v[0]] for zid, v in row["zones"].items()}
            mean = float(w["mean"])
            row.update({"water": [round(float(w[q]), 2) for q in ("q50", "q10", "q90")], "expected_water": round(mean, 2),
                        "expected": {zid: round(mean * c, 1) for zid, c in conv.items()},
                        "low_ratio": round(float(w["q10"]) / mean, 3) if mean > 0.002 else 0.0, "mid_ratio": round(float(w["q50"]) / mean, 3) if mean > 0.002 else 0.0,
                        "mid_lo_ratio": round(float(w["q25"]) / mean, 3) if mean > 0.002 else 0.0, "mid_hi_ratio": round(float(w["q75"]) / mean, 3) if mean > 0.002 else 0.0,
                        "high_ratio": round(float(w["q90"]) / mean, 3) if mean > 0.002 else 0.0,
                        "chance": {f"{int(t)}in": round(100 * chance(levels5, [float(w[q]) for q in keys5], t / conv[head])) if conv[head] > 0 else 0 for t in SNOW_THRESHOLDS_IN}})
            lo_w, hi_w, conditional = lead_range(levels5, [float(w[q]) for q in keys5], row["chance"]["1in"] / 100.0, 1.0 / conv[head]) if conv[head] > 0 else (0.0, 0.0, False)
            row.update({"lead": {zid: [round(lo_w * c, 1), round(hi_w * c, 1)] for zid, c in conv.items()}, "lead_if_snow": conditional,
                        "lead_lo_ratio": round(lo_w / mean, 3) if mean > 0.002 else 0.0, "lead_hi_ratio": round(hi_w / mean, 3) if mean > 0.002 else 0.0})
            wet = if_it_snows(levels5, [float(w[q]) for q in keys5], row["chance"]["1in"] / 100.0) if conv[head] > 0 else None
            row["if_it_snows"] = None if wet is None else {zid: round(max(wet * c, 1.0), 1) for zid, c in conv.items()}
            fields[k] = {"expected": mean * pi, "likely": float(w["q50"]) * pi, "low": float(w["q10"]) * pi, "high": float(w["q90"]) * pi, "water": mean,
                         "lead": 0.5 * (lo_w + hi_w) * pi}
            buckets[k] = {"w": {q: float(w[q]) for q in (*QUANTILES, "mean")}, "conv": conv, "pi": pi, "late": float((main[start] if start in main else w).get("late", AFTER_MIDNIGHT_H / 24.0))}
            # how the call for this day has moved: every earlier forecast made for it, oldest first
            issued = pd.Timestamp(row["issued"])
            past = history[(history["start"] == start) & (history["run"] < issued)] if len(history) else history
            row["trail"] = [{"issued": r["run"].isoformat(), "expected_in": round(float(r["mean"]) * conv[head], 1), "high_in": round(float(r["q90"]) * conv[head], 1)} for _, r in past.tail(12).iterrows()]
            day_ago = past[(past["run"] <= issued - pd.Timedelta(hours=18)) & (past["run"] >= issued - pd.Timedelta(hours=36))] if len(past) else past
            row["change_in"] = None if not len(day_ago) else round(row["expected"][head] - float(day_ago["mean"].iloc[-1]) * conv[head], 1)
        days.append(row)

    # the ten-day total: the range comes from what past runs with a similar forecast total went on to measure
    total, total_fields = None, None
    if len(fields) == DAYS:
        water = sum(f["water"] for f in fields.values())
        low, high = extended.block_range(final["horizons"][DAYS], water)
        mid = extended.block_mid(final["horizons"][DAYS], water)
        lo_r, mid_r, hi_r = (min(low / water, 1.0), mid / water, max(high / water, 1.0)) if water > 0.005 else (0.0, 0.0, 1.0)
        field = sum(f["expected"] for f in fields.values())           # the days add up to the total
        total_fields = {"expected": field, "low": field * lo_r, "high": field * hi_r}
        q25, q75 = extended.block_likely_range(final["horizons"][DAYS], water)
        mlo_r, mhi_r = (max(q25 / water, lo_r), min(q75 / water, hi_r)) if water > 0.005 else (0.0, 0.0)
        total = {"expected_water": round(water, 2), "low_ratio": round(lo_r, 3), "mid_ratio": round(mid_r, 3), "high_ratio": round(hi_r, 3),
                 "mid_lo_ratio": round(mlo_r, 3), "mid_hi_ratio": round(mhi_r, 3),
                 "mid": {z["id"]: [round(float((field * r)[cells["zone"] == index[z["id"]]].mean()), 1) for r in (mlo_r, mhi_r)] for z in zones},
                 "zones": {z["id"]: [round(float(v[cells["zone"] == index[z["id"]]].mean()), 1) for v in (field, field * lo_r, field * hi_r)] for z in zones},
                 "check": skill.get("through_day", {}).get(str(DAYS))}

    blocks = []
    main_dir = next((web_data_dir() / r["name"] for r in json.loads((web_data_dir() / "runs.json").read_text())[:1]), None) if main else None
    if main_dir is not None and all(d["available"] and d["source"] == "high-resolution" for d in days[:3]) and days[0]["issued"] == days[2]["issued"]:
        cards = json.loads((main_dir / "zones.json").read_text())
        blocks.append({"name": "Days 1 to 3", "word": "likely", "first": 1, "last": 3, "zones": {c["id"]: c["windows"]["72"]["fallen"] for c in cards}})
    for label, (a, b) in (("Days 4 to 7", (4, 7)), ("Days 8 to 10", (8, 10))):
        part = [d for d in days[a - 1: b] if d["available"] and d["source"] == "ensemble"]
        if len(part) != b - a + 1:
            continue
        water = sum(d["expected_water"] for d in part)
        low, high = extended.block_range(final["blocks"][f"days {a} to {b}"], water)
        z = {}
        for zone in zones:
            snow = sum(d["expected"][zone["id"]] for d in part)
            per = snow / water if water > 0.005 else zone["prism"] * 13.0
            z[zone["id"]] = [round(snow, 1), round(low * per, 1), round(max(high * per, snow), 1)]
        blocks.append({"name": label, "word": "expected", "first": a, "last": b, "zones": z})

    blend_runs = sorted({e["blend_run"] for e in ens.values() if e.get("blend_run")})
    european_runs = sorted({e["european_run"] for e in ens.values() if e.get("european_run") and np.isfinite(e.get("european", np.nan))})
    out = {"resort": resort.name, "headline_zone": head, "updated": now.isoformat(timespec="minutes"), "ensemble_run": run.isoformat(),
           "blend_run": blend_runs[-1] if blend_runs else None, "european_run": european_runs[-1] if european_runs else None,
           "next_update": (run + pd.Timedelta(hours=CYCLE_H + 5, minutes=40)).isoformat(timespec="minutes"), "reference": ref.isoformat(),
           "days": days, "blocks": blocks, "total": total, "map": bool(write and fields), "map_lead": bool(write and fields), "zones": [{"id": z["id"], "name": z["name"]} for z in zones],
           "skill": {k: {x: v[x] for x in ("skill_pct", "mae", "average_for_the_month_mae", "inside_10_90_pct", "n")} for k, v in skill["by_lead"].items()},
           "skill_blocks": skill["blocks"], "skill_seasons": f"{skill['seasons'][0] - 1} to {skill['seasons'][1]}", "wet_day_in": skill["wet_day_in"], "logged": logged}
    # what is shown: calendar days, midnight to midnight, re-cut from the 5 am to 5 am windows above.
    # The windows themselves are what the log and the scorecard keep, and each is the morning total of the day after it.
    first = next((d for d in days if d["available"]), None)
    measured = morning_measured(resort, starts[0], first.get("ratio") if first else None) if write else None
    shown, shown_fields = calendar_days(days, buckets, starts, measured, head)
    out.update({"days": shown, "window": "midnight to midnight", "morning_measured": measured})
    if write:
        write_map(resort, cells, shown_fields, total_fields)
        path = web_data_dir() / "outlook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, separators=(",", ":")))
    return out
