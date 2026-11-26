"""Days 4 to 10: the long-range half of the 10-day forecast.

Past three days there is no high-resolution model, so the forecast rests on the GEFS ensemble
(0.5 degree, four runs a day, published to 16 days). A gradient-boosted model takes the
ensemble's mean and spread for the day, the mean for the day either side (storms arrive early
or late more often than they vanish), the lead time and the 700 mb temperature, and predicts
what the mid-mountain gauge will record: a median and a range.

It is fitted on 12z runs and scored with each season held out. A run from another hour of the
day is treated as the nearest lead. The honest result of the scoring, by lead, travels with
every forecast so the map can say how far out it is worth reading.
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd

from ..fcst import archive, ecmwf, grib
from ..obs import hourly
from ..resort import Resort, data_dir, load_resort
from ..verify import scores
from . import precip
from .hindcast import LAST_SEASON, qcol
from .table import MM_PER_IN

LEADS = tuple(range(4, 11))
FEATURES = ("gefs_mean", "gefs_spread", "gefs_mean_prev", "gefs_mean_next", "gefs_area", "lead_day", "t700")
BLEND = ("nbm_qpf", "nbm_area", "nbm_max", "nbm_prev", "nbm_next")       # the National Blend's own precipitation, 2.5 km
EUROPEAN = ecmwf.FEATURES                # the European ensemble's mean precipitation, 0.25 degree
EUROPEAN_SPREAD = ecmwf.MEMBER_FEATURES  # how far its members disagree, and their wet end (2023 on)
VARIANTS = {"ensemble": FEATURES, "with_blend": FEATURES + BLEND, "with_european": FEATURES + BLEND + EUROPEAN,
            "with_european_members": FEATURES + BLEND + EUROPEAN + EUROPEAN_SPREAD}
USE = "with_european"         # set by the held-out comparison in `score_variants`: see TEN_DAY_OUTLOOK.md
EUROPEAN_LAG_H = 12           # the European run paired with an ensemble run when fitting: the one twelve hours earlier, the
                              # oldest the live outlook ever has to use (a European run is out about 7 h 45 min after it starts)
BLEND_LAG_H = 7               # the Blend cycle paired with an ensemble run: seven hours later (19z for a 12z run), the first
                              # of its long-range refreshes to hold that hour's GFS and European runs
NBM_REACH = 2                 # cells either side of the gauge cell for the area mean and the wettest cell (about 12 km square)
STRETCH = (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5, 1.7, 2.0)
UPPER = (0.75, 0.9)
WET_DAY_IN = 0.25             # a day worth planning around: about 3 to 4 in of snow at mid-mountain
BIG_DAY_IN = 0.5              # a big day: about 7 in of snow or more at mid-mountain
BLOCKS = {"days 4 to 7": (4, 7), "days 8 to 10": (8, 10)}


def paths() -> tuple:
    m = data_dir() / "model"
    return m / "extended_table.parquet", m / "hindcast_extended.parquet", m / "scores_extended.json", m / "final" / "extended.joblib"


def lead_day(end_hour: float) -> int:
    """The lead class of a window ending `end_hour` hours after its run, held to the fitted range."""
    return int(np.clip(round(end_hour / 24.0), LEADS[0], LEADS[-1]))


def _sum(d: dict, prefix: str, first_hour: int, r: int | None = None, c: int | None = None) -> float:
    keys = [f"{prefix}_{first_hour + s:03d}" for s in (6, 12, 18, 24)]
    if first_hour < 0 or any(k not in d for k in keys):
        return np.nan
    if r is None:
        return float(sum(d[k].mean() for k in keys) / MM_PER_IN)
    return float(sum(d[k][r, c] for k in keys) / MM_PER_IN)


def window_features(d: dict, r: int, c: int, first_hour: int) -> dict | None:
    """Model inputs for the 24 hours starting `first_hour` hours into a GEFS run."""
    mean = _sum(d, "mean", first_hour, r, c)
    keys = [f"spread_{first_hour + s:03d}" for s in (6, 12, 18, 24)]
    if not np.isfinite(mean) or any(k not in d for k in keys):
        return None
    temps = [float(d[f"t700_{first_hour + s:03d}"][r, c]) for s in (6, 12, 18, 24) if f"t700_{first_hour + s:03d}" in d]
    return {"gefs_mean": mean, "gefs_spread": float(np.sqrt(sum(d[k][r, c] ** 2 for k in keys))) / MM_PER_IN,
            "gefs_mean_prev": _sum(d, "mean", first_hour - 24, r, c), "gefs_mean_next": _sum(d, "mean", first_hour + 24, r, c),
            "gefs_area": _sum(d, "mean", first_hour), "lead_day": lead_day(first_hour + 24), "t700": float(np.mean(temps)) if temps else np.nan}


def blend_features(d: dict | None, r: int, c: int, first_hour: int) -> dict:
    """The National Blend's precipitation for the 24 hours starting `first_hour` hours into one of
    its runs: at the gauge cell, averaged and at its wettest over the cells around it, and for the
    day before and after. NaN where the run does not reach."""
    def total(start: int) -> np.ndarray | None:
        keys = [f"apcp_{start + s:03d}" for s in (6, 12, 18, 24)]
        return None if d is None or start < 0 or any(k not in d for k in keys) else sum(d[k] for k in keys) / MM_PER_IN

    day, prev, nxt = total(first_hour), total(first_hour - 24), total(first_hour + 24)
    if day is None:
        return {k: np.nan for k in BLEND}
    k = NBM_REACH
    box = day[r - k: r + k + 1, c - k: c + k + 1]
    return {"nbm_qpf": float(day[r, c]), "nbm_area": float(box.mean()), "nbm_max": float(box.max()),
            "nbm_prev": np.nan if prev is None else float(prev[r, c]), "nbm_next": np.nan if nxt is None else float(nxt[r, c])}


def build_table(resort: Resort | None = None) -> pd.DataFrame:
    """One row per 12z run and lead day 4 to 10, with what the gauge then measured."""
    resort = resort or load_resort()
    target = next(s for s in resort.snotel if s.get("target"))
    rows = []
    for run in archive.runs("gefs_240", last=str(pd.Timestamp.now().normalize().date())):
        d = archive.load("gefs_240", run)
        if d is None:
            continue
        r, c = grib.nearest(d["lat"], d["lon"], target["lat"], target["lon"])
        nb = archive.load("nbm_240", run + pd.Timedelta(hours=BLEND_LAG_H))
        nr, nc = grib.nearest(nb["lat"], nb["lon"], target["lat"], target["lon"]) if nb is not None else (0, 0)
        ec = ecmwf.load(run - pd.Timedelta(hours=EUROPEAN_LAG_H))
        ew = ecmwf.weights(ec["lat"], ec["lon"], target["lat"], target["lon"]) if ec is not None else None
        for k in LEADS:
            f = window_features(d, r, c, 24 * (k - 1))
            if f is not None:
                rows.append({"run": run, "start": run + pd.Timedelta(hours=24 * (k - 1)), **f, **blend_features(nb, nr, nc, 24 * (k - 1) - BLEND_LAG_H),
                             **ecmwf.features(ec, ew, 24 * (k - 1) + EUROPEAN_LAG_H)})
    t = pd.DataFrame(rows)
    hw = pd.read_parquet(data_dir() / "obs" / "snotel_hourly_water.parquet")
    hw = hw[hw["site"] == target["id"]].set_index("time_utc")
    starts = pd.DatetimeIndex(t["start"].unique())
    obs = hourly.window_sum(hw, starts, 24)["obs_in"]
    t["obs"] = obs.reindex(t["start"]).to_numpy()
    t["season"] = t["start"].dt.year + (t["start"].dt.month >= 10).astype(int)
    t = t[t["start"].dt.month.isin(precip.WINTER) & t["obs"].notna()].reset_index(drop=True)
    t.to_parquet(paths()[0])
    return t


def stretch_fit(h: pd.DataFrame) -> dict[float, float]:
    """How far each upper quantile must be stretched from the median to be exceeded as often as it claims."""
    live = h[h["q90"] >= 0.05]
    out = {}
    for lv in UPPER:
        width = live[qcol(lv)] - live["q50"]
        out[lv] = float(min(STRETCH, key=lambda k: abs(float((live["obs"] <= live["q50"] + k * width).mean()) - lv)))
    return out


def apply_stretch(p: pd.DataFrame, stretch: dict[float, float]) -> pd.DataFrame:
    p = p.copy()
    for lv in UPPER:
        p[qcol(lv)] = p["q50"] + stretch.get(lv, 1.0) * (p[qcol(lv)] - p["q50"])
    p["q90"] = np.maximum(p["q90"], p["q75"])
    return p


def chance_at_least(row: pd.Series | dict, threshold: float, prefix: str = "q") -> float:
    """Chance of at least `threshold`, read off the forecast quantiles (straight lines between them)."""
    levels = sorted(precip.LEVELS)
    values = np.maximum.accumulate([float(row[f"{prefix}{int(round(lv * 100)):02d}"]) for lv in levels])
    if threshold <= values[0]:
        return 1.0 - levels[0] / 2
    if threshold > values[-1]:
        return (1.0 - levels[-1]) / 2
    return float(1.0 - np.interp(threshold, values, levels))


def hindcast(t: pd.DataFrame | None = None, variant: str | None = None, save: bool = True) -> pd.DataFrame:
    """Every finished season predicted by a model, and a stretch, that never saw it."""
    t = build_table() if t is None else t
    t = t[t["season"] <= LAST_SEASON]
    cols = list(VARIANTS[variant or USE])
    parts = []
    for season in sorted(t["season"].unique()):
        test, train = t[t["season"] == season], t[t["season"] != season]
        p = precip.predict(precip.fit(train, cols), test).join(precip.climatology(train, test))
        parts.append(p)
    h = t.join(pd.concat(parts), how="inner")
    out = []
    for season in sorted(h["season"].unique()):                  # the stretch for a season comes from the others
        out.append(apply_stretch(h[h["season"] == season], stretch_fit(h[h["season"] != season])))
    h = pd.concat(out).sort_index()
    h["p_wet"] = [chance_at_least(r, WET_DAY_IN) for _, r in h.iterrows()]
    h["clim_p_wet"] = [chance_at_least(r, WET_DAY_IN, "clim_q") for _, r in h.iterrows()]
    if save:
        h.to_parquet(paths()[1])
    return h


def score_variants(t: pd.DataFrame | None = None) -> dict:
    """The ensemble alone, with the National Blend, and with the European ensemble, on the same held-out days."""
    t = build_table() if t is None else t
    out = {}
    for name in VARIANTS:
        h = hindcast(t, name, save=False)
        out[name] = {}
        for k, g in h.groupby("lead_day"):
            wet, big = g[g["obs"] >= 0.3], g[g["obs"] >= BIG_DAY_IN]
            out[name][str(int(k))] = {"n": int(len(g)), "mae": round(scores.mae(g["obs"], g["q50"]), 4), "mean_mae": round(scores.mae(g["obs"], g["mean"]), 4),
                                      "crps": round(_crps(g, "q"), 4), "skill_pct": round(100 * (1 - _crps(g, "q") / _crps(g, "clim_q")), 1),
                                      "storm_days": int(len(wet)), "storm_day_mean_miss": round(scores.mae(wet["obs"], wet["mean"]), 3) if len(wet) else None,
                                      "storm_day_mean_forecast": round(float(wet["mean"].mean()), 3) if len(wet) else None,
                                      "big_days": int(len(big)), "big_day_mean_miss": round(scores.mae(big["obs"], big["mean"]), 3) if len(big) else None,
                                      "big_day_mean_forecast": round(float(big["mean"].mean()), 3) if len(big) else None,
                                      "big_day_high_end": round(float(big["q90"].mean()), 3) if len(big) else None,
                                      "big_day_measured": round(float(big["obs"].mean()), 3) if len(big) else None,
                                      "big_days_above_high_end_pct": round(100 * float((big["obs"] > big["q90"]).mean())) if len(big) else None}
        if "nbm_qpf" in h:
            nb = h.dropna(subset=["nbm_qpf"])
            out.setdefault("blend_as_issued", {}).update({str(int(k)): {"n": int(len(g)), "mae": round(scores.mae(g["obs"], g["nbm_qpf"]), 4)} for k, g in nb.groupby("lead_day")})
    (data_dir() / "model" / "scores_extended_variants.json").write_text(json.dumps(out, indent=1))
    return out


def _crps(g: pd.DataFrame, prefix: str) -> float:
    return scores.crps_from_quantiles(g["obs"], {lv: g[f"{prefix}{int(round(lv * 100)):02d}"].to_numpy(float) for lv in scores.CRPS_LEVELS})


def score(h: pd.DataFrame | None = None) -> dict:
    h = pd.read_parquet(paths()[1]) if h is None else h
    out = {"seasons": [int(h["season"].min()), int(h["season"].max())], "runs": int(h["run"].nunique()), "by_lead": {}, "blocks": {}, "wet_day_in": WET_DAY_IN}
    from ..live.outlook import conversion_fit, gauge_per_inch       # the same water-to-snow step the live outlook uses
    resort = load_resort()
    fit = conversion_fit(resort)
    h = h.assign(per=[gauge_per_inch(fit, resort, t) for t in h["t700"]])
    for k, g in h.groupby("lead_day"):
        wet = g["obs"] >= WET_DAY_IN
        live = g[(g["q90"] >= 0.05) | (g["obs"] >= 0.05)]
        crps, clim = _crps(g, "q"), _crps(g, "clim_q")
        brier, brier_clim = float(((g["p_wet"] - wet) ** 2).mean()), float(((g["clim_p_wet"] - wet) ** 2).mean())
        called = g[g["p_wet"] >= 0.3]
        out["by_lead"][str(int(k))] = {
            "n": int(len(g)), "mae": round(scores.mae(g["obs"], g["q50"]), 3), "raw_gefs_mae": round(scores.mae(g["obs"], g["gefs_mean"]), 3),
            "average_for_the_month_mae": round(scores.mae(g["obs"], g["clim_q50"]), 3),
            "snow_mae": round(float(((g["q50"] - g["obs"]).abs() * g["per"]).mean()), 2), "snow_raw_gefs_mae": round(float(((g["gefs_mean"] - g["obs"]).abs() * g["per"]).mean()), 2),
            "snow_average_for_the_month_mae": round(float(((g["clim_q50"] - g["obs"]).abs() * g["per"]).mean()), 2),
            "crps": round(crps, 4), "crps_average_for_the_month": round(clim, 4), "skill_pct": round(100 * (1 - crps / clim)),
            "inside_10_90_pct": round(100 * scores.coverage(live["obs"], live["q10"], live["q90"])), "above_90_pct": round(100 * float((live["obs"] > live["q90"]).mean())),
            "wet_days": int(wet.sum()), "wet_day_skill_pct": round(100 * (1 - brier / brier_clim)),
            "called_30pct": {"days": int(len(called)), "were_wet_pct": None if called.empty else round(100 * float((called["obs"] >= WET_DAY_IN).mean()))},
            "correlation": round(float(np.corrcoef(g["mean"], g["obs"])[0, 1]), 2)}
    for name, (a, b) in BLOCKS.items():                         # multi-day totals: easier than single days
        tot = block_totals(h, a, b)
        inside = []
        for season in sorted(tot["season"].unique()):           # each season's ranges come from the other seasons
            fit, test = block_fit(tot[tot["season"] != season]), tot[tot["season"] == season]
            bounds = np.array([block_range(fit, f) for f in test["fc"]])
            inside.append(pd.DataFrame({"in": (test["obs"] >= bounds[:, 0] - 1e-9) & (test["obs"] <= bounds[:, 1] + 1e-9), "above": test["obs"] > bounds[:, 1] + 1e-9}))
        inside = pd.concat(inside)
        out["blocks"][name] = {"n": int(len(tot)), "mae": round(scores.mae(tot["obs"], tot["fc"]), 3), "raw_gefs_mae": round(scores.mae(tot["obs"], tot["raw"]), 3),
                               "average_for_the_month_mae": round(scores.mae(tot["obs"], tot["clim"]), 3),
                               "correlation": round(float(np.corrcoef(tot["fc"], tot["obs"])[0, 1]), 2), "mean_observed_in": round(float(tot["obs"].mean()), 2),
                               "inside_10_90_pct": round(100 * float(inside["in"].mean())), "above_90_pct": round(100 * float(inside["above"].mean()))}
    paths()[2].write_text(json.dumps(out, indent=1))
    return out


BLOCK_BINS = 6


def block_totals(h: pd.DataFrame, a: int, b: int) -> pd.DataFrame:
    """Forecast (sum of daily means) and measured totals over lead days a..b, one row per run."""
    g = h[h["lead_day"].between(a, b)]
    tot = g.groupby("run").agg(fc=("mean", "sum"), obs=("obs", "sum"), clim=("clim_mean", "sum"), raw=("gefs_mean", "sum"), n=("obs", "size"), season=("season", "first"))
    return tot[tot["n"] == b - a + 1]


def block_fit(tot: pd.DataFrame) -> dict:
    """The 10th and 90th percentile of what was measured, by how much was forecast: past runs are
    sorted into equal groups by forecast total and each group's outcomes are read off. Adding up
    daily ranges does not work at these leads (the low end comes out far too high)."""
    tot = tot.sort_values("fc")
    groups = [tot.iloc[i] for i in np.array_split(np.arange(len(tot)), BLOCK_BINS)]
    fc = [float(g["fc"].mean()) for g in groups]
    low = np.maximum.accumulate([float(g["obs"].quantile(0.1)) for g in groups])
    mid = np.maximum.accumulate([float(g["obs"].quantile(0.5)) for g in groups])
    high = np.maximum.accumulate([float(g["obs"].quantile(0.9)) for g in groups])
    q25 = np.maximum.accumulate([float(g["obs"].quantile(0.25)) for g in groups])
    q75 = np.maximum.accumulate([float(g["obs"].quantile(0.75)) for g in groups])
    return {"fc": fc, "low": [float(v) for v in low], "mid": [float(v) for v in mid], "high": [float(v) for v in high],
            "q25": [float(v) for v in q25], "q75": [float(v) for v in q75]}


def block_range(fit: dict, forecast_total: float) -> tuple[float, float]:
    """(low, high) for a multi-day total. Beyond the largest forecasts on record the range grows in proportion."""
    low, high = float(np.interp(forecast_total, fit["fc"], fit["low"])), float(np.interp(forecast_total, fit["fc"], fit["high"]))
    if forecast_total > fit["fc"][-1]:
        k = forecast_total / fit["fc"][-1]
        low, high = low * k, high * k
    return low, max(high, low)


HORIZONS = tuple(range(1, 11))


def horizon_totals() -> pd.DataFrame:
    """Held-out forecasts for all ten days of each 12z run, side by side: the three-day model's
    days 1 to 3 and this model's days 4 to 10. One row per run and day."""
    m = data_dir() / "model"
    parts = []
    for day, tier in ((1, 24), (2, 48), (3, 72)):
        h = pd.read_parquet(m / f"hindcast_tier{tier}.parquet")[["start", "mean", "q10", "q90", "obs", "clim_mean"]].copy()
        h["mean"] = h["mean"].clip(h["q10"], h["q90"])            # as the live forecast reports it
        h["run"], h["day"] = h["start"] - pd.Timedelta(days=day - 1), day
        parts.append(h[["run", "day", "mean", "obs", "clim_mean"]])
    far = pd.read_parquet(paths()[1])[["run", "lead_day", "mean", "obs", "clim_mean"]].rename(columns={"lead_day": "day"})
    t = pd.concat(parts + [far], ignore_index=True)
    t["season"] = t["run"].dt.year + (t["run"].dt.month >= 10).astype(int)
    return t


def through_day(t: pd.DataFrame, k: int) -> pd.DataFrame:
    """Forecast and measured totals from day 1 through day k, for runs that have every one of those days."""
    g = t[t["day"] <= k]
    tot = g.groupby("run").agg(fc=("mean", "sum"), obs=("obs", "sum"), clim=("clim_mean", "sum"), n=("obs", "size"), season=("season", "first"))
    return tot[tot["n"] == k]


def score_horizons() -> dict:
    """Totals through each day: miss against the average for the date, and how often the range held
    (each season's ranges fitted on the other seasons)."""
    t = horizon_totals()
    out = {}
    for k in HORIZONS:
        tot = through_day(t, k)
        hits = []
        for season in sorted(tot["season"].unique()):
            fit, test = block_fit(tot[tot["season"] != season]), tot[tot["season"] == season]
            b = np.array([block_range(fit, f) for f in test["fc"]])
            hits.append(pd.DataFrame({"in": (test["obs"] >= b[:, 0] - 1e-9) & (test["obs"] <= b[:, 1] + 1e-9), "above": test["obs"] > b[:, 1] + 1e-9}))
        hits = pd.concat(hits)
        out[str(k)] = {"n": int(len(tot)), "mae": round(scores.mae(tot["obs"], tot["fc"]), 3), "average_for_the_month_mae": round(scores.mae(tot["obs"], tot["clim"]), 3),
                       "correlation": round(float(np.corrcoef(tot["fc"], tot["obs"])[0, 1]), 2), "mean_observed_in": round(float(tot["obs"].mean()), 2),
                       "inside_10_90_pct": round(100 * float(hits["in"].mean())), "above_90_pct": round(100 * float(hits["above"].mean()))}
    s = json.loads(paths()[2].read_text())
    s["through_day"] = out
    paths()[2].write_text(json.dumps(s, indent=1))
    return out


def block_likely_range(fit: dict, forecast_total: float) -> tuple[float, float]:
    """The middle half of what past runs with this forecast total measured (25th to 75th percentile)."""
    lo, hi = float(np.interp(forecast_total, fit["fc"], fit["q25"])), float(np.interp(forecast_total, fit["fc"], fit["q75"]))
    if forecast_total > fit["fc"][-1]:
        k = forecast_total / fit["fc"][-1]
        lo, hi = lo * k, hi * k
    return lo, max(hi, lo)


def block_mid(fit: dict, forecast_total: float) -> float:
    """The middle outcome for a multi-day total: half of past runs with this forecast measured more, half less."""
    mid = float(np.interp(forecast_total, fit["fc"], fit["mid"]))
    return mid * forecast_total / fit["fc"][-1] if forecast_total > fit["fc"][-1] else mid


def train(t: pd.DataFrame | None = None) -> dict:
    """The model used live: every finished season, with the stretch from the held-out forecasts."""
    t = pd.read_parquet(paths()[0]) if t is None else t
    t = t[t["season"] <= LAST_SEASON]
    models = precip.fit(t, list(VARIANTS[USE]))
    h = pd.read_parquet(paths()[1])
    models["stretch"] = stretch_fit(h)
    models["blocks"] = {name: block_fit(block_totals(h, a, b)) for name, (a, b) in BLOCKS.items()}
    totals = horizon_totals()
    models["horizons"] = {k: block_fit(through_day(totals, k)) for k in HORIZONS}
    from ..live.outlook import conversion_fit
    models["conversion"] = conversion_fit(load_resort())
    joblib.dump(models, paths()[3])
    return {"rows": int(len(t)), "stretch": models["stretch"], "conversion": models["conversion"]}


def predict(features: dict) -> dict[str, float]:
    """Calibrated quantiles and the mean for one window from the final model."""
    m = joblib.load(paths()[3])
    row = pd.DataFrame([features])
    p = apply_stretch(precip.predict(m, row), m["stretch"]).iloc[0]
    out = {k: float(p[k]) for k in ("q10", "q25", "q50", "q75", "q90")}
    out["mean"] = float(np.clip(p["mean"], out["q10"], out["q90"]))
    full = {**p.to_dict(), **{k: out[k] for k in ("q75", "q90")}}
    out["p_wet"] = chance_at_least(full, WET_DAY_IN)
    return out
