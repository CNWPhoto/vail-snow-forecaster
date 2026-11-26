"""The forecast log and the public scorecard.

Every forecast is written to a log the moment it is built, before anyone knows the answer.
Once the mid-mountain SNOTEL gauge has reported, each logged forecast is set beside what the
gauge measured, and the running season numbers are published with the map.

Rules that keep it honest:

- A logged forecast is never changed. A later build of the same run does not replace it.
- `live` means the forecast was logged within a day of the model run it came from. `replay`
  means it was rebuilt afterwards from the model runs as they were issued, with the model that
  was frozen before the season began. Both are out of sample; only `live` proves the pipeline
  ran on the day.
- The only truth used is the public SNOTEL record: gauge water and the snow-depth sensor.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..model import settle
from ..obs import hourly, snotel
from ..resort import ROOT, Resort, data_dir, load_resort, web_data_dir

LOG = data_dir() / "log" / "forecasts.jsonl"
LIVE_WITHIN = pd.Timedelta(hours=24)
STORM_DAY_IN = 0.10            # a storm day: this much water at the gauge
STORM_MIN_IN = 0.20
SNOW_POSSIBLE_IN = 0.05        # "snow possible": forecast high end or measured water at least this
MIN_PACK_IN = 6.0              # below this the depth sensor sees grass and stumps, not a pack


def target(resort: Resort) -> dict:
    return next(s for s in resort.snotel if s.get("target"))


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


# ---------------------------------------------------------------------------
# What the gauge measured
# ---------------------------------------------------------------------------

def gauge_record(resort: Resort, max_age_h: float = 3.0, today=None) -> dict:
    """Hourly water (UTC, hour ending), smoothed snow depth and temperature at the target gauge
    for this water year and the last.

    Water is the checked daily total shared out by the hourly record, as in training. The last
    day or so has no daily total yet; there the cleaned hourly readings are used and the hours
    are marked provisional."""
    site = target(resort)
    now = now_utc()
    wy = now.year + (now.month >= 10)
    daily = snotel.load_site(site["triplet"], today=today, max_age_h=max_age_h)["prec"]
    daily = daily[daily.index >= f"{wy - 2}-10-01"]
    h = snotel.load_hourly(site["triplet"], first_wy=wy - 1, today=today, max_age_h=max_age_h)
    return assemble(daily, h)


def assemble(daily_prec: pd.Series, h: pd.DataFrame) -> dict:
    hw = hourly.hourly_water(daily_prec, h["prec"], h["depth"] if "depth" in h else None)
    water = pd.DataFrame({"water_in": hw["water_in"], "provisional": False})
    inc = hourly.clean_increments(h["prec"])
    inc.index = inc.index + hourly.PST
    late = inc[inc.index > water.index[-1]].dropna()
    if len(late):
        water = pd.concat([water, pd.DataFrame({"water_in": late, "provisional": True})])
    shift = h.copy()
    shift.index = shift.index + hourly.PST
    return {"water": water, "depth": settle.smooth_depth(shift["depth"]),
            "swe": shift["swe"].asfreq("h").rolling(5, center=True, min_periods=2).median(),
            "temp_c": ((shift["tobs"].asfreq("h") - 32.0) / 1.8).interpolate(limit=3), "last": shift.index[-1]}


def window_obs(rec: dict, start: pd.Timestamp, hours: int = 24) -> dict | None:
    """Gauge water over (start, start + hours], and the depth sensor's change. None until every hour is in."""
    idx = pd.date_range(start + pd.Timedelta(hours=1), start + pd.Timedelta(hours=hours), freq="h")
    w = rec["water"]["water_in"].reindex(idx)
    if w.isna().any():
        return None
    out = {"obs": float(w.sum()), "provisional": bool(rec["water"]["provisional"].reindex(idx).fillna(False).any()), "depth_change": None, "depth0": None}
    d0, d1 = rec["depth"].get(start, np.nan), rec["depth"].get(idx[-1], np.nan)
    if np.isfinite([d0, d1]).all():
        change = float(d1 - d0)
        fault = change < -settle.MAX_DAILY_DROP_IN or change > settle.MAX_GAIN_PER_IN_WATER * out["obs"] + 4.0
        if not fault:
            out["depth_change"], out["depth0"] = change, float(d0)
    return out


def pack_at(rec: dict, when: pd.Timestamp) -> tuple[float, float] | None:
    """(depth in, density g/cm3) of the snowpack at the gauge, or None when there is no pack to speak of."""
    d, s = rec["depth"].get(when, np.nan), rec["swe"].get(when, np.nan)
    if not np.isfinite([d, s]).all() or d < MIN_PACK_IN or s <= 0:
        return None
    return float(d), float(np.clip(s / d, 0.08, 0.5))


# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

def settled_change(water_in: float, gauge: dict, day_shares: np.ndarray, pack: tuple[float, float] | None) -> float:
    """Forecast 24-hour change in depth at the sensor: new snow settling, plus the old pack sinking."""
    depth0, rho0 = pack or (0.0, 0.3)
    w = (water_in * gauge["snow_share"] * np.asarray(day_shares, float))[None, :]
    new, old = settle.simulate(w, np.full_like(w, gauge["temp_c"]), np.array([gauge["ratio"]]), np.array([depth0]), np.array([rho0]), *settle.fitted_rates())
    return float(new[0] + old[0] - depth0)


def entries(run: pd.Timestamp, summary: list[dict], shares: np.ndarray, rec: dict | None, logged_at: pd.Timestamp | None = None) -> list[dict]:
    """Log lines for one run: one per forecast day."""
    logged_at = logged_at or now_utc()
    mode = "live" if pd.Timedelta(0) <= logged_at - run <= LIVE_WITHIN else "replay"
    out = []
    for s in summary:
        if not s.get("available"):
            continue
        q, g = s["water_in"], s["gauge"]
        e = {"run": run.isoformat(), "day": s["day"], "start": s["start"], "mode": mode, "logged_at": logged_at.isoformat(timespec="seconds"),
             **{k: round(float(q[k]), 3) for k in ("q10", "q25", "q50", "q75", "q90", "mean")},
             "raw": None if s.get("raw_in") is None else round(float(s["raw_in"]), 3), "raw_model": "HRRR" if s["day"] < 3 else "GFS",
             "ratio": round(g["ratio"], 1), "snow_share": round(g["snow_share"], 2),
             **{f"snow_{k}": round(float(q[k]) * g["snow_share"] * g["ratio"], 1) for k in ("q10", "q50", "q90")},
             "regime": s["regime"], "upside": bool(s["upside_setup"])}
        if s["day"] == 1 and rec is not None:              # the depth forecast needs the pack as it stood at issue time
            pack = pack_at(rec, run)
            e["pack_in"] = None if pack is None else round(pack[0], 1)
            for k in ("q10", "q50", "q90"):
                e[f"depth_{k}"] = round(settled_change(float(q[k]), g, shares[0], pack), 1)
        out.append(e)
    return out


def append(new: list[dict], path: Path | None = None) -> int:
    """Add entries the log does not already hold. Returns how many were added."""
    path = path or LOG
    have = {(e["run"], e["day"]) for e in read_raw(path)}
    fresh = [e for e in new if (e["run"], e["day"]) not in have]
    if fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for e in fresh:
                f.write(json.dumps(e) + "\n")
    return len(fresh)


def read_raw(path: Path | None = None) -> list[dict]:
    path = path or LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_log(path: Path | None = None) -> pd.DataFrame:
    df = pd.DataFrame(read_raw(path))
    if df.empty:
        return df
    df["run"], df["start"] = pd.to_datetime(df["run"]), pd.to_datetime(df["start"])
    return df.drop_duplicates(["run", "day"], keep="first").sort_values(["start", "day"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def verify(log: pd.DataFrame, rec: dict) -> pd.DataFrame:
    """The log with what the gauge measured in each window. Unfinished windows keep NaN."""
    out = log.copy()
    obs = [window_obs(rec, s) or {} for s in out["start"]]
    for k in ("obs", "provisional", "depth_change", "depth0"):
        out[k] = [o.get(k) for o in obs]
    out["obs"], out["depth_change"] = out["obs"].astype(float), out["depth_change"].astype(float)
    # the gauge measures water. Snow figures are that water at the snow share and ratio the forecast itself used,
    # so forecast and measurement are converted the same way and the comparison is still of the water forecast.
    out["per_inch"] = out["snow_share"].astype(float) * out["ratio"].astype(float)
    out["obs_snow"], out["raw_snow"] = out["obs"] * out["per_inch"], out["raw"].astype(float) * out["per_inch"]
    return out


def block(g: pd.DataFrame) -> dict:
    """Scores for a set of verified forecasts of one lead."""
    if g.empty:
        return {"n": 0}
    err = g["q50"] - g["obs"]
    out = {"n": int(len(g)), "mae": round(float(err.abs().mean()), 3), "bias": round(float(err.mean()), 3),
           "observed_total_in": round(float(g["obs"].sum()), 2), "forecast_total_in": round(float(g["q50"].sum()), 2)}
    raw = g.dropna(subset=["raw"])
    if len(raw):
        out["raw_mae"], out["raw_model"] = round(float((raw["raw"] - raw["obs"]).abs().mean()), 3), raw["raw_model"].iat[0]
    if "obs_snow" in g:
        out["snow_mae"] = round(float((g["snow_q50"] - g["obs_snow"]).abs().mean()), 2)
        out["snow_observed_total_in"], out["snow_forecast_total_in"] = round(float(g["obs_snow"].sum()), 1), round(float(g["snow_q50"].sum()), 1)
        if len(raw):
            out["snow_raw_mae"] = round(float((raw["raw_snow"] - raw["obs_snow"]).abs().mean()), 2)
    wet = g[(g["q90"] >= SNOW_POSSIBLE_IN) | (g["obs"] >= SNOW_POSSIBLE_IN)]
    if len(wet):
        out["snow_possible"] = {"n": int(len(wet)), "mae": round(float((wet["q50"] - wet["obs"]).abs().mean()), 3),
                                "inside_10_90": int(((wet["obs"] >= wet["q10"] - 1e-9) & (wet["obs"] <= wet["q90"] + 1e-9)).sum()),
                                "above_90": int((wet["obs"] > wet["q90"] + 1e-9).sum()), "below_10": int((wet["obs"] < wet["q10"] - 1e-9).sum())}
    return out


def depth_block(g: pd.DataFrame) -> dict:
    """Day-1 depth forecasts against the sensor, on days something could fall. The sensor reads
    to the inch, and early in the season new snow lands on warm bare ground, so expect scatter."""
    if "depth_q50" not in g:
        return {"n": 0}
    g = g[g["depth_change"].notna() & g["depth_q50"].notna() & ((g["q90"] >= SNOW_POSSIBLE_IN) | (g["obs"] >= SNOW_POSSIBLE_IN))]
    if g.empty:
        return {"n": 0}
    err = g["depth_q50"] - g["depth_change"]
    return {"n": int(len(g)), "mae": round(float(err.abs().mean()), 2), "bias": round(float(err.mean()), 2),
            "inside_10_90": int(((g["depth_change"] >= g["depth_q10"] - 0.5) & (g["depth_change"] <= g["depth_q90"] + 0.5)).sum())}


def storms(day1: pd.DataFrame, all_days: pd.DataFrame) -> list[dict]:
    """Runs of consecutive storm days, each with the day-ahead forecasts added up and the
    forecast made on the storm's first morning for up to three days."""
    from ..live.forecast import combine_ranges
    d = day1.dropna(subset=["obs"]).sort_values("start").reset_index(drop=True)
    wet = d["obs"] >= STORM_DAY_IN
    group = ((d["start"].diff() != pd.Timedelta(days=1)) | (wet != wet.shift())).cumsum()
    out = []
    for _, g in d[wet].groupby(group[wet]):
        if g["obs"].sum() < STORM_MIN_IN:
            continue
        low, high = combine_ranges(g[["q50"]].to_numpy(), g[["q10"]].to_numpy(), g[["q90"]].to_numpy())
        s = {"start": g["start"].iat[0].isoformat(), "days": int(len(g)), "observed_in": round(float(g["obs"].sum()), 2),
             "day_ahead_in": round(float(g["q50"].sum()), 2), "day_ahead_range": [round(float(low[0]), 2), round(float(high[0]), 2)],
             "provisional": bool(g["provisional"].any()), "regimes": sorted(set(g["regime"]))}
        if "obs_snow" in g:
            lo_s, hi_s = combine_ranges(g[["snow_q50"]].to_numpy(), g[["snow_q10"]].to_numpy(), g[["snow_q90"]].to_numpy())
            s.update({"observed_snow_in": round(float(g["obs_snow"].sum()), 1), "day_ahead_snow_in": round(float(g["snow_q50"].sum()), 1),
                      "day_ahead_snow_range": [round(float(lo_s[0]), 1), round(float(hi_s[0]), 1)]})
        depth = g["depth_change"].dropna()
        if len(depth) == len(g):
            s["depth_gain_in"] = round(float(depth.sum()), 1)
            if "depth_q50" in g and g["depth_q50"].notna().all():
                s["depth_forecast_in"] = round(float(g["depth_q50"].sum()), 1)
        first = all_days[(all_days["run"] == g["run"].iat[0]) & (all_days["day"] <= min(len(g), 3))].dropna(subset=["obs"])
        if len(first) == min(len(g), 3):
            lo, hi = combine_ranges(first[["q50"]].to_numpy(), first[["q10"]].to_numpy(), first[["q90"]].to_numpy())
            s["first_morning"] = {"days": int(len(first)), "forecast_in": round(float(first["q50"].sum()), 2),
                                  "range": [round(float(lo[0]), 2), round(float(hi[0]), 2)], "observed_in": round(float(first["obs"].sum()), 2)}
        out.append(s)
    return out[::-1]


def past_seasons(target_id: str | None = None) -> list[dict]:
    """Earlier winters from the hindcast: each season forecast by a model that never saw it."""
    path = data_dir() / "model" / "hindcast_tier24.parquet"
    if not path.exists():
        return []
    h = pd.read_parquet(path).dropna(subset=["obs", "q50"])
    cases = data_dir() / "model" / "settle_cases.parquet"          # each day's modelled snow-to-water ratio at the gauge
    ratio = pd.Series(dtype=float)
    if cases.exists():
        c = pd.read_parquet(cases)
        ratio = c[c["site"] == c["site"].mode().iat[0]].drop_duplicates("start").set_index("start")["ratio"] if target_id is None else c[c["site"] == target_id].drop_duplicates("start").set_index("start")["ratio"]
    h["per"] = ratio.reindex(h["start"]).to_numpy() if len(ratio) else np.nan
    h["per"] = h["per"].fillna(h["per"].median() if h["per"].notna().any() else 14.0)
    out = []
    for season, g in h.groupby("season"):
        wet = g[(g["q90"] >= SNOW_POSSIBLE_IN) | (g["obs"] >= SNOW_POSSIBLE_IN)]
        row = {"season": f"{season - 1}-{season % 100:02d}", "n": int(len(g)), "mae": round(float((g["q50"] - g["obs"]).abs().mean()), 3),
               "raw_mae": round(float((g["qpf_3x3"] - g["obs"]).abs().mean()), 3),
               "snow_mae": round(float(((g["q50"] - g["obs"]).abs() * g["per"]).mean()), 2), "snow_raw_mae": round(float(((g["qpf_3x3"] - g["obs"]).abs() * g["per"]).mean()), 2),
               "inside_10_90_pct": round(100 * float(((wet["obs"] >= wet["q10"]) & (wet["obs"] <= wet["q90"])).mean())) if len(wet) else None}
        nbm = g.dropna(subset=["nbm_qpf"])
        if len(nbm) > 100:
            row["nbm_mae"], row["mae_same_days"] = round(float((nbm["nbm_qpf"] - nbm["obs"]).abs().mean()), 3), round(float((nbm["q50"] - nbm["obs"]).abs().mean()), 3)
            row["snow_nbm_mae"] = round(float(((nbm["nbm_qpf"] - nbm["obs"]).abs() * nbm["per"]).mean()), 2)
        out.append(row)
    return out


def long_range(rec: dict, season: int, log: pd.DataFrame | None = None) -> dict:
    """Days 4 to 10 this season: every logged long-range forecast against the gauge, by lead day,
    beside what the same model did on held-out winters."""
    from ..live import outlook
    from ..model import extended
    out = {"by_lead": {}, "logged": 0, "verified": 0, "held_out": {}}
    if extended.paths()[2].exists():
        h = json.loads(extended.paths()[2].read_text())
        out["held_out"] = {k: {x: v.get(x) for x in ("n", "mae", "average_for_the_month_mae", "skill_pct", "inside_10_90_pct", "snow_mae", "snow_average_for_the_month_mae")} for k, v in h["by_lead"].items()}
        out["held_out_seasons"] = h["seasons"]
    log = outlook.read_log() if log is None else log
    if log.empty:
        return out
    log = log[log["start"] >= pd.Timestamp(f"{season - 1}-10-01")].copy()
    seen = {s: window_obs(rec, s) for s in log["start"].unique()}
    log["obs"] = [(seen[s] or {}).get("obs", np.nan) for s in log["start"]]
    done = log.dropna(subset=["obs"])
    out.update({"logged": int(len(log)), "verified": int(len(done)), "live": int((log["mode"] == "live").sum())})
    for k, g in done.groupby("lead_day"):
        wet = g[(g["q90"] >= SNOW_POSSIBLE_IN) | (g["obs"] >= SNOW_POSSIBLE_IN)]
        per = g["per_inch"].astype(float) if "per_inch" in g else 14.0
        out["by_lead"][str(int(k))] = {"n": int(len(g)), "mae": round(float((g["q50"] - g["obs"]).abs().mean()), 3), "raw_mae": round(float((g["raw"] - g["obs"]).abs().mean()), 3),
                                       "snow_mae": round(float(((g["q50"] - g["obs"]).abs() * per).mean()), 2),
                                       "inside_10_90": int(((wet["obs"] >= wet["q10"] - 1e-9) & (wet["obs"] <= wet["q90"] + 1e-9)).sum()), "snow_possible": int(len(wet))}
    return out


def scorecard(resort: Resort, rec: dict, log: pd.DataFrame | None = None, now: pd.Timestamp | None = None) -> dict:
    now = now or now_utc()
    log = read_log() if log is None else log
    site = target(resort)
    season = now.year + (now.month >= 10)
    card = {"updated": now.strftime("%Y-%m-%d %H:%MZ"), "site": f"{site['name']} SNOTEL, {site['elev_ft']:,} ft", "season": f"{season - 1}-{season % 100:02d}",
            "gauge_through": rec["last"].isoformat(), "leads": {}, "depth": {"n": 0}, "days": [], "storms": [], "past_seasons": past_seasons(site["id"]), "counts": {}}
    try:
        card["long_range"] = long_range(rec, season)
    except Exception as e:                       # the long-range model may not be built yet
        card["long_range"] = {"by_lead": {}, "held_out": {}, "error": f"{type(e).__name__}: {e}"}
    try:                                         # the hourly mountain state beside the depth sensor (state/score.py)
        from ..state import score as state_score
        card["state"] = state_score.block(now=now)
    except Exception as e:
        card["state"] = {"n": 0, "error": f"{type(e).__name__}: {e}"}
    if log.empty:
        return card
    v = verify(log, rec)
    v = v[v["start"] >= pd.Timestamp(f"{season - 1}-10-01")]
    done = v.dropna(subset=["obs"])
    for day in (1, 2, 3):
        card["leads"][str(day)] = block(done[done["day"] == day])
    day1 = v[v["day"] == 1]
    card["depth"] = depth_block(done[done["day"] == 1])
    card["storms"] = storms(day1, v)
    card["counts"] = {"logged": int(len(day1)), "verified": int(day1["obs"].notna().sum()), "live": int((day1["mode"] == "live").sum()),
                      "replay": int((day1["mode"] == "replay").sum())}

    def num(x, nd=2):
        return None if x is None or not np.isfinite(x) else round(float(x), nd)

    for _, r in day1.sort_values("start", ascending=False).iterrows():
        card["days"].append({"start": r["start"].isoformat(), "mode": r["mode"], "q10": r["q10"], "q50": r["q50"], "q90": r["q90"], "raw": num(r["raw"]),
                             "obs": num(r["obs"]), "obs_snow": num(r["obs_snow"], 1), "raw_snow": num(r["raw_snow"], 1), "provisional": bool(r["provisional"]) if r["provisional"] is not None and r["provisional"] == r["provisional"] else False,
                             "snow": [r["snow_q10"], r["snow_q50"], r["snow_q90"]],
                             "depth": [num(r.get("depth_q10"), 1), num(r.get("depth_q50"), 1), num(r.get("depth_q90"), 1)], "depth_change": num(r["depth_change"], 1),
                             "regime": r["regime"], "upside": bool(r["upside"])})
    return card


def in_training(run: pd.Timestamp) -> bool:
    """True for runs from winters the frozen model was fitted on: scoring those would flatter it."""
    from ..model.hindcast import LAST_SEASON
    return run.year + (run.month >= 10) <= LAST_SEASON


def log_run(resort: Resort, run: pd.Timestamp, summary: list[dict], shares: np.ndarray, rec: dict | None = None) -> int:
    """Log one run's forecast (once). Runs from training winters are not logged."""
    if in_training(run):
        return 0
    return append(entries(run, summary, shares, rec or gauge_record(resort)))


def replay(resort: Resort | None = None, first: str | None = None, last: str | None = None) -> dict:
    """Log this season's earlier 12z runs, rebuilt from the model runs as they were issued.
    The model was frozen before the season began, so these are out of sample; they are marked
    `replay` because nobody saw them on the day."""
    from ..live.forecast import gauge_days
    from ..model.precip import WINTER
    resort = resort or load_resort()
    now = now_utc()
    season = now.year + (now.month >= 10)
    start = pd.Timestamp(first) if first else pd.Timestamp(f"{season - 1}-11-01")
    end = pd.Timestamp(last) if last else now.normalize() - pd.Timedelta(days=1)
    have = {(e["run"], e["day"]) for e in read_raw()}
    rec = gauge_record(resort)
    counts = {"logged": 0, "already": 0, "unavailable": 0}
    for day in pd.date_range(start, end, freq="D"):
        run = day + pd.Timedelta(hours=12)
        if day.month not in WINTER or in_training(run):
            continue
        if (run.isoformat(), 1) in have:
            counts["already"] += 1
            continue
        try:
            summary, shares = gauge_days(resort, run)
        except (RuntimeError, FileNotFoundError):
            counts["unavailable"] += 1
            continue
        counts["logged"] += int(append(entries(run, summary, shares, rec)) > 0)
    return counts


def publish(resort: Resort | None = None, rec: dict | None = None) -> dict:
    """Write the scorecard beside the forecasts, where the map reads it."""
    resort = resort or load_resort()
    card = scorecard(resort, rec or gauge_record(resort))
    path = web_data_dir() / "scorecard.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(card, separators=(",", ":")))
    return card
