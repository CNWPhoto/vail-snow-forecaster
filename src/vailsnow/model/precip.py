"""Layer 1: liquid precipitation at mid-mountain.

A gradient-boosted model takes what the weather models said when they were issued (their
precipitation at and around the gauge, the wind aloft, temperature and moisture) and predicts
what the SNOTEL gauge will record. It is always scored on a season it was not trained on.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ..verify.scores import CRPS_LEVELS, sort_quantiles

QPF = ("qpf_site", "qpf_3x3", "qpf_5x5", "qpf_max5", "qpf_elev", "qpf_area", "hrrr_snow_water")
FLOW = ("wet_hours", "peak6_share", "u600", "v600", "spd600", "steady600", "t700", "lapse", "rh700", "pwat", "tw_site")
GFS_QPF = ("gfs_qpf", "gfs_qpf_area")
LAGGED = ("prev_qpf_3x3", "prev_qpf_elev")          # the HRRR run issued 12 hours earlier (24 h lead only)
GEFS = ("gefs_mean",)
SPREAD = ("gefs_spread", "run_gap")                 # how much the forecasts disagree
NBM = ("nbm_qpf",)
LONG_RANGE = ("gefs_mean_prev", "gefs_mean_next", "gefs_area", "nbm_area", "nbm_max", "nbm_prev", "nbm_next")      # days 4 to 10 (model/extended.py)
RISING = QPF + GFS_QPF + LAGGED + GEFS + NBM + LONG_RANGE          # more forecast precipitation never lowers the answer
WINTER = (11, 12, 1, 2, 3, 4)
VARIANTS = ("phase3", "own", "with_nbm")
GFS_FLOW = ("gfs_u600", "gfs_v600", "gfs_spd600", "gfs_t600", "gfs_rh600", "gfs_t700", "gfs_pwat")
LEVELS = tuple(sorted(set(CRPS_LEVELS) | {0.1, 0.5, 0.9}))
SETTINGS = dict(max_depth=3, learning_rate=0.05, max_iter=250, min_samples_leaf=20, l2_regularization=1.0,
                early_stopping=False, random_state=0)


def features_for(tier: int, table: pd.DataFrame, variant: str = "phase3") -> list[str]:
    """Inputs used at a lead time.

    phase3     HRRR, with GFS where the table has it; GFS alone at 72 h
    own        adds the previous HRRR run, the GEFS ensemble mean and the two measures of disagreement
    with_nbm   adds the National Blend's own forecast as an input
    """
    if tier == 72:
        cols = list(GFS_QPF + GFS_FLOW)
    else:
        cols = list(QPF + FLOW)
        if tier in (24, 48):
            cols += list(GFS_QPF)
    if variant in ("own", "with_nbm"):
        cols += list(GEFS + SPREAD) + (list(LAGGED) if tier == 24 else [])
    if variant == "with_nbm":
        cols += list(NBM)
    return [c for c in cols if c in table.columns and table[c].notna().any()]


def main_input(tier: int) -> str:
    return "gfs_qpf" if tier == 72 else "qpf_3x3"


def usable(table: pd.DataFrame, tier: int) -> pd.DataFrame:
    """Rows of one tier that have an observation and the main model input."""
    t = table[(table["tier"] == tier) & table["start"].dt.month.isin(WINTER)]
    return t[t["obs"].notna() & t[main_input(tier)].notna()].copy()


def _model(loss: str, cols: list[str], level: float | None = None) -> HistGradientBoostingRegressor:
    rising = [1 if c in RISING else 0 for c in cols]
    kw = dict(SETTINGS, monotonic_cst=rising)
    return HistGradientBoostingRegressor(loss=loss, quantile=level, **kw) if loss == "quantile" else HistGradientBoostingRegressor(loss=loss, **kw)


def spread_zeros(y: np.ndarray, width: float = 0.02, seed: int = 0) -> np.ndarray:
    """Replace exact zeros with small negative values spread over `width`.

    Most days are dry, so the starting guess for a low or middle quantile is exactly zero and
    every day sits at or above it. Quantile boosting then sees identical gradients everywhere
    and never makes a split. Spreading the dry days just below zero breaks the tie; forecasts
    are clipped back to zero afterwards."""
    y = np.array(y, dtype=float)
    dry = y <= 0
    y[dry] = -np.random.default_rng(seed).uniform(0.0, width, int(dry.sum()))
    return y


def fit(train: pd.DataFrame, cols: list[str]) -> dict:
    """Mean model plus one model per probability level."""
    x, y = train[cols].to_numpy(float), train["obs"].to_numpy(float)
    yq = spread_zeros(y)
    return {"cols": cols, "mean": _model("squared_error", cols).fit(x, y),
            "q": {q: _model("quantile", cols, q).fit(x, yq) for q in LEVELS}}


def predict(models: dict, rows: pd.DataFrame) -> pd.DataFrame:
    x = rows[models["cols"]].to_numpy(float)
    q = sort_quantiles({lv: m.predict(x) for lv, m in models["q"].items()})
    out = pd.DataFrame({f"q{int(round(lv * 100)):02d}": v for lv, v in q.items()}, index=rows.index)
    out["mean"] = np.clip(models["mean"].predict(x), 0.0, None)
    return out


def scale_fit(train: pd.DataFrame, col: str) -> float:
    """Single multiplier that removes the average bias of one raw input."""
    s = train[col].sum()
    return float(train["obs"].sum() / s) if s > 0 else 1.0


def climatology(train: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """What the gauge did in the same calendar month of the training seasons: mean and quantiles."""
    out = pd.DataFrame(index=rows.index, dtype=float)
    month = rows["start"].dt.month
    for m in month.unique():
        y = train.loc[train["start"].dt.month == m, "obs"].to_numpy(float)
        if len(y) == 0:
            y = train["obs"].to_numpy(float)
        sel = month == m
        out.loc[sel, "clim_mean"] = y.mean()
        for q in LEVELS:
            out.loc[sel, f"clim_q{int(round(q * 100)):02d}"] = np.quantile(y, q)
    return out


def hindcast(table: pd.DataFrame, tier: int, variant: str = "phase3") -> pd.DataFrame:
    """Leave-one-season-out predictions for one tier. Every row's forecast comes from models
    that never saw its season."""
    t = usable(table, tier)
    cols = features_for(tier, t, variant)
    parts = []
    for season in sorted(t["season"].unique()):
        test, train = t[t["season"] == season], t[t["season"] != season]
        if len(train) < 200 or len(test) == 0:
            continue
        p = predict(fit(train, cols), test)
        raw = main_input(tier)
        p["scaled_raw"] = test[raw] * scale_fit(train, raw)
        p = p.join(climatology(train, test))
        parts.append(p)
    pred = pd.concat(parts) if parts else pd.DataFrame()
    return t.join(pred, how="inner")
