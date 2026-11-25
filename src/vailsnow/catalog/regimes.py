"""Flow regimes, and how snowfall differs between sites under each.

A regime is named for where the wind at 600 mb comes from. That level, about
13,400 ft, is the first standard level clear of the terrain over Vail; 700 mb
sits below mid-mountain there.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .events import daily_gain

SECTORS = (("north", 337.5, 22.5), ("northwest", 292.5, 337.5), ("west", 247.5, 292.5), ("southwest", 202.5, 247.5))
ORDER = ("northwest", "west", "southwest", "north", "east or south", "weak flow", "closed low")
WEAK_MS = 5.0
CLOSED_LOW_KM = 700.0
WINTER = (11, 12, 1, 2, 3, 4)


def sector(direction: float, speed: float) -> str:
    """Regime name for a wind direction (degrees it blows from) and vector-mean speed (m/s)."""
    if not np.isfinite(direction) or not np.isfinite(speed):
        return "unknown"
    if speed < WEAK_MS:
        return "weak flow"
    d = direction % 360.0
    for name, lo, hi in SECTORS:
        if (lo <= d < hi) if lo < hi else (d >= lo or d < hi):
            return name
    return "east or south"


def event_regime(row: pd.Series) -> str:
    """Regime of a storm from its features: a nearby closed low overrides the wind sector."""
    if np.isfinite(row.get("closed_low_km", np.nan)) and row["closed_low_km"] <= CLOSED_LOW_KM:
        return "closed low"
    return sector(row["dir600"], row["spd600"] * row.get("steady600", 1.0))


def daily_regimes(daily: pd.DataFrame) -> pd.Series:
    return pd.Series([sector(d, s) for d, s in zip(daily["dir600"], daily["spd600_vec"])], index=daily.index, name="regime")


def water_year(index: pd.DatetimeIndex) -> np.ndarray:
    return index.year + (index.month >= 10)


def ratio_with_interval(num: pd.Series, den: pd.Series, groups: np.ndarray, n_boot: int = 1000, seed: int = 0) -> dict:
    """Ratio of totals with a 90% interval from resampling whole seasons, so that storms
    within a season are not treated as independent."""
    total_n, total_d = float(num.sum()), float(den.sum())
    if total_d <= 0:
        return {"ratio": np.nan, "lo": np.nan, "hi": np.nan}
    by = pd.DataFrame({"n": num.to_numpy(), "d": den.to_numpy(), "g": groups}).groupby("g")[["n", "d"]].sum()
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, len(by), size=(n_boot, len(by)))
    n, d = by["n"].to_numpy()[pick].sum(axis=1), by["d"].to_numpy()[pick].sum(axis=1)
    r = n[d > 0] / d[d > 0]
    return {"ratio": round(total_n / total_d, 2), "lo": round(float(np.percentile(r, 5)), 2), "hi": round(float(np.percentile(r, 95)), 2)}


def site_gains(snotel: pd.DataFrame) -> pd.DataFrame:
    """Daily water gained at every SNOTEL site (melt days count as zero)."""
    return pd.DataFrame({s: daily_gain(d.set_index("date")["swe"]).clip(lower=0) for s, d in snotel.groupby("site")})


def by_regime_site_ratios(gains: pd.DataFrame, regimes: pd.Series, target: str = "vail", min_days: int = 60) -> pd.DataFrame:
    """For every regime and every other site: total water at the target over total water at the
    site, on all winter days both reported. No storm selection is involved."""
    rows = []
    g = gains[gains.index.month.isin(WINTER)].join(regimes, how="inner")
    for site in [c for c in gains.columns if c != target]:
        pair = g[[target, site, "regime"]].dropna()
        for name, sub in [("all days", pair)] + [(r, pair[pair["regime"] == r]) for r in ORDER if r != "closed low"]:
            if len(sub) < min_days or sub[site].sum() <= 0:
                continue
            r = ratio_with_interval(sub[target], sub[site], water_year(sub.index))
            rows.append({"site": site, "regime": name, "days": len(sub), "target_in": round(float(sub[target].sum()), 1),
                         "site_in": round(float(sub[site].sum()), 1), **r})
    return pd.DataFrame(rows)


def regime_shares(gains: pd.DataFrame, regimes: pd.Series, sites: list[str]) -> pd.DataFrame:
    """Share of winter days, and of each site's winter water, that falls in each regime."""
    g = gains[gains.index.month.isin(WINTER)].join(regimes, how="inner")
    rows = []
    for name in ORDER:
        sub = g[g["regime"] == name]
        if len(sub) == 0:
            continue
        row = {"regime": name, "days": len(sub), "day_share": round(len(sub) / len(g), 3)}
        for s in sites:
            ok = g[s].notna()
            row[f"{s}_share"] = round(float(sub.loc[sub[s].notna(), s].sum() / g.loc[ok, s].sum()), 3)
            row[f"{s}_in_per_day"] = round(float(sub[s].mean()), 3)
        rows.append(row)
    return pd.DataFrame(rows)


def valley_east_west(valley: pd.DataFrame, regimes: pd.Series, east: str = "east_vail", west: str = "west_vail") -> pd.DataFrame:
    """East over west on the valley floor, by regime. A morning report dated D mostly covers day D-1."""
    out = []
    v = valley[~valley["multi_day"]]
    for col in ("snow", "pcpn"):
        e = v[v["site"] == east].set_index("date")[col]
        w = v[v["site"] == west].set_index("date")[col]
        pair = pd.DataFrame({"e": e, "w": w}).dropna()
        pair.index = pair.index - pd.Timedelta(days=1)
        pair = pair[pair.index.month.isin(WINTER)].join(regimes, how="inner")
        for name, sub in [("all days", pair)] + [(r, pair[pair["regime"] == r]) for r in ORDER]:
            if len(sub) < 30 or sub["w"].sum() <= 0:
                continue
            out.append({"measure": col, "regime": name, "days": len(sub), "east_in": round(float(sub["e"].sum()), 1),
                        "west_in": round(float(sub["w"].sum()), 1), **ratio_with_interval(sub["e"], sub["w"], water_year(sub.index))})
    return pd.DataFrame(out)


def kmeans_check(feats: pd.DataFrame, k: int = 5, seed: int = 0) -> pd.Series:
    """An independent grouping of storms by k-means on standardised wind components, temperature,
    moisture and lapse rate, to see whether the wind-sector regimes reflect real structure."""
    from scipy.cluster.vq import kmeans2
    rad = np.radians(feats["dir600"])
    x = np.column_stack([-feats["spd600"] * np.sin(rad), -feats["spd600"] * np.cos(rad), feats["t700_c"],
                         feats["tcwv_mm"], feats["lapse_700_500"]])
    x = (x - x.mean(axis=0)) / x.std(axis=0)
    _, labels = kmeans2(x, k, seed=seed, minit="++")
    return pd.Series(labels, index=feats.index, name="cluster")
