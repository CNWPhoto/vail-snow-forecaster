"""Forecast scores. All inputs are plain arrays; units pass through unchanged."""
from __future__ import annotations

import numpy as np
import pandas as pd

CRPS_LEVELS = tuple(round(0.05 + 0.1 * i, 2) for i in range(10))      # 0.05, 0.15, ... 0.95


def mae(obs, pred) -> float:
    return float(np.mean(np.abs(np.asarray(pred, float) - np.asarray(obs, float))))


def bias(obs, pred) -> float:
    """Mean of forecast minus observed: positive means the forecast runs high."""
    return float(np.mean(np.asarray(pred, float) - np.asarray(obs, float)))


def pinball(obs, pred, level: float) -> float:
    """Quantile (pinball) loss of a forecast for one probability level."""
    d = np.asarray(obs, float) - np.asarray(pred, float)
    return float(np.mean(np.maximum(level * d, (level - 1.0) * d)))


def crps_from_quantiles(obs, quantiles: dict[float, np.ndarray]) -> float:
    """Continuous ranked probability score from a set of evenly spaced quantile forecasts.
    CRPS is twice the pinball loss averaged over all probability levels; with levels at the
    midpoints of equal bins this is the midpoint rule. For a single-valued forecast repeated
    at every level it reduces to the mean absolute error."""
    levels = sorted(quantiles)
    return float(2.0 * np.mean([pinball(obs, quantiles[q], q) for q in levels]))


def coverage(obs, low, high) -> float:
    """Share of observations inside [low, high]."""
    obs = np.asarray(obs, float)
    return float(np.mean((obs >= np.asarray(low, float) - 1e-9) & (obs <= np.asarray(high, float) + 1e-9)))


def reliability(obs, quantiles: dict[float, np.ndarray]) -> pd.DataFrame:
    """For each stated level, how often the observation fell at or below the forecast quantile.
    A reliable forecast has `observed` equal to `level`.

    Days where forecast and observation are both zero are ties; they are counted as half
    below so that dry days do not inflate the frequency."""
    obs = np.asarray(obs, float)
    rows = []
    for q in sorted(quantiles):
        f = np.asarray(quantiles[q], float)
        below = (obs < f - 1e-9).mean() + 0.5 * (np.abs(obs - f) <= 1e-9).mean()
        rows.append({"level": q, "observed": float(below), "n": int(len(obs))})
    return pd.DataFrame(rows)


def sort_quantiles(quantiles: dict[float, np.ndarray]) -> dict[float, np.ndarray]:
    """Make quantile forecasts non-decreasing in level, row by row, and never negative."""
    levels = sorted(quantiles)
    stack = np.sort(np.clip(np.column_stack([quantiles[q] for q in levels]), 0.0, None), axis=1)
    return {q: stack[:, i] for i, q in enumerate(levels)}


def paired_gain(obs, pred, ref, groups, n_boot: int = 2000, seed: int = 0) -> dict:
    """Difference in mean absolute error (ref minus pred; positive = pred is better), with a
    90% interval from resampling whole groups (seasons), since days within a season are not independent."""
    obs, pred, ref, groups = (np.asarray(x) for x in (obs, pred, ref, groups))
    e_pred, e_ref = np.abs(pred - obs), np.abs(ref - obs)
    ids = np.unique(groups)
    sums = np.array([[e_ref[groups == g].sum(), e_pred[groups == g].sum(), (groups == g).sum()] for g in ids])
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(ids), size=(n_boot, len(ids)))
    s = sums[pick].sum(axis=1)
    diff = (s[:, 0] - s[:, 1]) / s[:, 2]
    point = float(e_ref.mean() - e_pred.mean())
    return {"gain": point, "low": float(np.quantile(diff, 0.05)), "high": float(np.quantile(diff, 0.95)),
            "skill": float(point / e_ref.mean()) if e_ref.mean() > 0 else float("nan")}
