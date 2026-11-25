"""Run the leave-one-season-out hindcasts and write the score tables for Phase 3."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..resort import Resort, data_dir, load_resort
from ..verify import scores as sc
from . import precip

TIERS = (12, 24, 36, 48, 72)
LAST_SEASON = 2026                 # the season in progress is left out of scoring
SIZE_BINS = ((0.0, 0.005, "dry"), (0.005, 0.25, "under 0.25 in"), (0.25, 0.5, "0.25 to 0.5 in"), (0.5, 99.0, "0.5 in or more"))
# column in the hindcast table -> label in the score tables
METHODS = {
    "q50": "this model (median)", "mean": "this model (mean)", "scaled_raw": "raw model, bias removed",
    "qpf_3x3": "raw HRRR", "gfs_qpf": "raw GFS", "nbm_qpf": "National Blend (NBM)", "ndfd_qpf": "NWS official",
    "clim_q50": "climatology (median)", "clim_mean": "climatology (mean)",
}


def _q(h: pd.DataFrame, prefix: str = "q") -> dict[float, np.ndarray]:
    return {lv: h[f"{prefix}{int(round(lv * 100)):02d}"].to_numpy(float) for lv in sc.CRPS_LEVELS}


def _row(h: pd.DataFrame, col: str) -> dict:
    o = h["obs"].to_numpy(float)
    out = {"mae": sc.mae(o, h[col]), "bias": sc.bias(o, h[col])}
    if col == "q50":
        out["crps"] = sc.crps_from_quantiles(o, _q(h))
    elif col == "clim_q50":
        out["crps"] = sc.crps_from_quantiles(o, _q(h, "clim_q"))
    else:
        out["crps"] = out["mae"]            # a single-valued forecast: CRPS equals MAE
    return {k: round(v, 4) for k, v in out.items()}


STRETCH = tuple(np.round(np.arange(0.7, 3.01, 0.05), 2))
UPPER = tuple(lv for lv in precip.LEVELS if lv > 0.5)
PRIMARY = "own"                    # the variant the zone forecast and the snowfall test use (NBM as an input added nothing)


def qcol(level: float) -> str:
    return f"q{int(round(level * 100)):02d}"


def calibrate(h: pd.DataFrame, tier: int) -> pd.DataFrame:
    """Stretch each upper quantile away from the median until it is exceeded as often as it claims.

    The stretch for a season is fitted on the other seasons' out-of-sample forecasts, on windows
    where snow was possible. (Those forecasts came from models that saw the season being adjusted,
    but the adjustment never sees that season's observations.) Lower quantiles are left alone:
    they sit at or near zero and cannot be judged."""
    h = h.copy()
    half = 0.5 if tier == 12 else 1.0
    raw = {lv: h[qcol(lv)].to_numpy(float).copy() for lv in UPPER}
    med, obs, season = h["q50"].to_numpy(float), h["obs"].to_numpy(float), h["season"].to_numpy()
    live = h["q90"].to_numpy(float) >= 0.1 * half
    for lv in UPPER:
        h[f"{qcol(lv)}_raw"] = raw[lv]
    used = {}
    for s in np.unique(season):
        cal, te = (season != s) & live, season == s
        for lv in UPPER:
            width = raw[lv] - med
            best = min(STRETCH, key=lambda k: abs(np.mean(obs[cal] <= med[cal] + k * width[cal]) - lv))
            h.loc[te, qcol(lv)] = (med + best * width)[te]
            used.setdefault(str(lv), []).append(float(best))
    ordered = np.sort(np.column_stack([h[qcol(lv)].to_numpy(float) for lv in UPPER]), axis=1)     # keep the levels in order
    for i, lv in enumerate(UPPER):
        h[qcol(lv)] = np.maximum(ordered[:, i], med)
    h.attrs["stretch"] = {k: round(float(np.mean(v)), 2) for k, v in used.items()}
    return h


def upside_flag(h: pd.DataFrame) -> pd.Series:
    """The brief's upside-surprise setup, as far as the pulled fields allow: northwest flow, a
    strong jet aloft (500 mb wind of 30 m/s or more) and a moist layer near ridge level."""
    if "spd500" not in h or h["spd500"].isna().all():
        return pd.Series(False, index=h.index)
    return (h["regime"] == "northwest") & (h["spd500"] >= 30.0) & (h["rh700"] >= 0.80)


def tail_block(h: pd.DataFrame, tier: int) -> dict:
    """How the forecast behaves at the top end."""
    half = 0.5 if tier == 12 else 1.0
    live = h[h["q90"] >= 0.1 * half]
    big = h[h["obs"] >= 0.5 * half]
    flag = upside_flag(h)
    out = {"exceed_q90_when_snow_possible": round(float((live["obs"] > live["q90"]).mean()), 3),
           "exceed_q75_when_snow_possible": round(float((live["obs"] > live["q75"]).mean()), 3),
           "big_days": int(len(big)), "big_days_inside_q90": round(float((big["obs"] <= big["q90"]).mean()), 3) if len(big) else None,
           "big_days_median_error": round(sc.mae(big["obs"], big["q50"]), 3) if len(big) else None,
           "big_days_mean_error": round(sc.mae(big["obs"], big["mean"]), 3) if len(big) else None,
           "big_days_q75_error": round(sc.mae(big["obs"], big["q75"]), 3) if len(big) else None,
           "mean_width_10_90_when_snow_possible": round(float((live["q90"] - live["q10"]).mean()), 3)}
    if flag.any():
        f, rest = h[flag], h[~flag & (h["q90"] >= 0.1 * half)]
        out["upside_flag"] = {"windows": int(flag.sum()), "exceed_q90": round(float((f["obs"] > f["q90"]).mean()), 3),
                              "exceed_q90_unflagged": round(float((rest["obs"] > rest["q90"]).mean()), 3),
                              "mean_obs": round(float(f["obs"].mean()), 3), "mean_median_forecast": round(float(f["q50"].mean()), 3),
                              "mean_expected_forecast": round(float(f["mean"].mean()), 3),
                              "share_half_inch_or_more": round(float((f["obs"] >= 0.5 * half).mean()), 3)}
    return out


def score_block(h: pd.DataFrame, cols: list[str]) -> dict:
    return {"n": int(len(h)), "mean_obs": round(float(h["obs"].mean()), 4),
            "methods": {METHODS[c]: _row(h, c) for c in cols if c in h and h[c].notna().all()}}


def score_tier(h: pd.DataFrame, tier: int) -> dict:
    cols = [c for c in METHODS if c in h.columns]
    always = [c for c in cols if h[c].notna().all()]
    out = {"all": score_block(h, always), "seasons": [int(h["season"].min()), int(h["season"].max())]}
    # the fair comparison with NBM and the NWS grids: only windows where both exist
    have = [c for c in ("nbm_qpf", "ndfd_qpf") if c in h and h[c].notna().any()]
    if have:
        common = h[h[have].notna().all(axis=1)]
        if len(common) >= 100:
            out["with_nbm_and_nws"] = score_block(common, [c for c in cols if common[c].notna().all()])
            out["with_nbm_and_nws"]["seasons"] = [int(common["season"].min()), int(common["season"].max())]
            for ref in have:
                g = sc.paired_gain(common["obs"], common["q50"], common[ref], common["season"])
                out["with_nbm_and_nws"][f"gain_over_{METHODS[ref]}"] = {k: round(v, 4) for k, v in g.items()}
    raw = precip.main_input(tier)
    out["gain_over_raw"] = {k: round(v, 4) for k, v in sc.paired_gain(h["obs"], h["q50"], h[raw], h["season"]).items()}
    out["by_regime"] = {r: score_block(g, always) for r, g in h.groupby("regime") if len(g) >= 40}
    half = 0.5 if tier == 12 else 1.0
    out["by_observed_size"] = {name: score_block(h[(h["obs"] >= lo * half) & (h["obs"] < hi * half)], always)
                               for lo, hi, name in SIZE_BINS}
    out["by_forecast_size"] = {name: score_block(h[(h["q50"] >= lo * half) & (h["q50"] < hi * half)], always)
                               for lo, hi, name in SIZE_BINS if ((h["q50"] >= lo * half) & (h["q50"] < hi * half)).sum() >= 15}
    o = h["obs"].to_numpy(float)
    rel = sc.reliability(o, {lv: h[f"q{int(round(lv * 100)):02d}"].to_numpy(float) for lv in (0.1, 0.25, 0.5, 0.75, 0.9)})
    wet = h[h["q90"] >= 0.1 * half]
    out["reliability"] = rel.round(3).to_dict("records")
    out["coverage_10_90"] = round(sc.coverage(o, h["q10"], h["q90"]), 3)
    out["coverage_10_90_when_snow_possible"] = round(sc.coverage(wet["obs"], wet["q10"], wet["q90"]), 3) if len(wet) else None
    out["tail"] = tail_block(h, tier)
    out["stretch"] = h.attrs.get("stretch")
    out["by_season"] = {int(s): {"n": int(len(g)), "model": round(sc.mae(g["obs"], g["q50"]), 4), "raw": round(sc.mae(g["obs"], g[raw]), 4)}
                        for s, g in h.groupby("season")}
    return out


def storm_totals(h24: pd.DataFrame, min_water: float = 1.0) -> pd.DataFrame:
    """Storm totals from the day-ahead forecasts: each catalogued storm against the sum of the
    24-hour forecasts issued each morning during it."""
    ev = pd.read_parquet(data_dir() / "catalog" / "events.parquet")
    ev = ev[(ev["swe_in"] >= min_water) & (ev["start"] >= h24["start"].min())]
    f = h24.set_index("start")
    rows = []
    for _, e in ev.iterrows():
        starts = pd.date_range(e["start"] - pd.Timedelta(hours=12), e["end"] + pd.Timedelta(hours=12), freq="D")
        if not all(s in f.index for s in starts):
            continue
        w = f.loc[starts]
        rows.append({"start": e["start"].date().isoformat(), "end": e["end"].date().isoformat(), "days": int(e["days"]),
                     "regime": w["regime"].mode().iat[0], "pillow_in": float(e["swe_in"]), "gauge_in": round(float(w["obs"].sum()), 2),
                     "model_in": round(float(w["mean"].sum()), 2), "model_low_in": round(float(w["q10"].sum()), 2),
                     "model_high_in": round(float(w["q90"].sum()), 2), "raw_hrrr_in": round(float(w["qpf_3x3"].sum()), 2),
                     "nbm_in": round(float(w["nbm_qpf"].sum()), 2) if "nbm_qpf" in w and w["nbm_qpf"].notna().all() else None,
                     "nws_in": round(float(w["ndfd_qpf"].sum()), 2) if "ndfd_qpf" in w and w["ndfd_qpf"].notna().all() else None})
    return pd.DataFrame(rows)


def run(resort: Resort | None = None, variants: tuple[str, ...] = precip.VARIANTS) -> dict:
    resort = resort or load_resort()
    out_dir = data_dir() / "model"
    table = pd.read_parquet(out_dir / "table.parquet")
    table = table[table["season"] <= LAST_SEASON]
    result = {}
    for variant in variants:
        result[variant] = {}
        for tier in TIERS:
            h = precip.hindcast(table, tier, variant)
            if h.empty:
                continue
            h = calibrate(h, tier)
            h.to_parquet(out_dir / f"hindcast_tier{tier}_{variant}.parquet")
            if variant == PRIMARY:
                h.to_parquet(out_dir / f"hindcast_tier{tier}.parquet")
            result[variant][str(tier)] = score_tier(h, tier)
            print(f"{variant}, tier {tier}: {len(h)} windows scored", flush=True)
            if tier == 24:
                st = storm_totals(h)
                if variant == PRIMARY:
                    st.to_csv(out_dir / "storm_totals_24h.csv", index=False)
                result[variant]["storm_totals"] = {
                    "storms": int(len(st)),
                    "mae": {"model": round(sc.mae(st["gauge_in"], st["model_in"]), 3), "raw_hrrr": round(sc.mae(st["gauge_in"], st["raw_hrrr_in"]), 3)},
                    "bias": {"model": round(sc.bias(st["gauge_in"], st["model_in"]), 3), "raw_hrrr": round(sc.bias(st["gauge_in"], st["raw_hrrr_in"]), 3)},
                }
    (out_dir / "scores_precip.json").write_text(json.dumps(result, indent=1))
    return result


def reliability_tables() -> dict:
    """Observed frequency at each stated level for the primary variant, on windows where snow was possible."""
    out_dir = data_dir() / "model"
    levels = sorted(precip.LEVELS)
    out = {}
    for tier in TIERS:
        path = out_dir / f"hindcast_tier{tier}.parquet"
        if not path.exists():
            continue
        h = pd.read_parquet(path)
        w = h[h["q90"] >= (0.05 if tier == 12 else 0.1)]
        rel = sc.reliability(w["obs"].to_numpy(), {lv: w[qcol(lv)].to_numpy() for lv in levels})
        out[str(tier)] = {"n": int(len(w)), "share_of_windows": round(len(w) / len(h), 2), "levels": levels,
                          "observed": rel["observed"].round(3).tolist(),
                          "cover_10_90": round(sc.coverage(w["obs"], w["q10"], w["q90"]), 3),
                          "cover_25_75": round(sc.coverage(w["obs"], w["q25"], w["q75"]), 3)}
    (out_dir / "reliability.json").write_text(json.dumps(out, indent=1))
    return out
