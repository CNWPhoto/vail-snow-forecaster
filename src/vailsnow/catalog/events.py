"""Detect snow events from a SNOTEL daily record.

SNOTEL daily values are start-of-day readings, so the change from day D to
day D+1 is what accumulated during day D. An event is a run of consecutive
days on which the snow pillow gained water, split into separate storms wherever
the gain drops to a lull and picks up again.

The pillow reads to 0.1 inch and jitters by that much, so a lone 0.1 inch
rise only counts when the precipitation gauge also rose that day.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def daily_gain(series: pd.Series) -> pd.Series:
    """Change over each day: value at the start of the next day minus value at the start of this one."""
    s = series.asfreq("D")
    return (s.shift(-1) - s).round(2)


def snow_days(swe: pd.Series, prec: pd.Series | None = None, min_gain: float = 0.1, sure_gain: float = 0.2) -> pd.Series:
    """True on days the pillow gained at least `sure_gain` inches of water, or at least
    `min_gain` with the precipitation gauge agreeing."""
    d_swe = daily_gain(swe)
    sure = d_swe >= sure_gain - 1e-9
    if prec is None:
        return sure | (d_swe >= min_gain - 1e-9)
    d_prec = daily_gain(prec).reindex(d_swe.index)
    return sure | ((d_swe >= min_gain - 1e-9) & (d_prec >= min_gain - 1e-9))


def split_at_lulls(run: pd.Series, d_swe: pd.Series, core_gain: float) -> pd.Series:
    """Break each run of snow days into separate storms wherever the gain drops below
    `core_gain` and then picks up again. The lull days stay with the storm before them."""
    out, label = [], 0
    prev_run, seen_core, in_lull = None, False, False
    for day, r in run.items():
        core = d_swe.loc[day] >= core_gain - 1e-9
        if r != prev_run:
            label, prev_run, seen_core, in_lull = label + 1, r, False, False
        elif core and seen_core and in_lull:
            label, seen_core = label + 1, False
        seen_core = seen_core or core
        in_lull = not core
        out.append(label)
    return pd.Series(out, index=run.index)


def detect_events(site: pd.DataFrame, min_total: float = 0.2, core_gain: float = 0.2) -> pd.DataFrame:
    """One row per snow event at a site. `site` is date-indexed with `swe` and, if present,
    `prec`, `depth`, `tavg`/`tobs`. Events with less than `min_total` inches of water are dropped.
    A run of snow days is split into separate storms at lulls (days gaining less than `core_gain`)."""
    site = site.asfreq("D")
    d_swe = daily_gain(site["swe"])
    d_prec = daily_gain(site["prec"]) if "prec" in site else None
    is_snow = snow_days(site["swe"], site.get("prec")).fillna(False).astype(bool)
    run = (is_snow != is_snow.shift(fill_value=False)).cumsum()[is_snow]
    run = split_at_lulls(run, d_swe, core_gain)
    temp = site["tavg"] if "tavg" in site and site["tavg"].notna().any() else site.get("tobs")
    rows = []
    for _, days in run.groupby(run):
        start, end = days.index[0], days.index[-1]
        gain = d_swe.loc[start:end]
        total = float(gain.sum())
        if total < min_total - 1e-9:
            continue
        row = {
            "start": start, "end": end, "days": len(gain), "swe_in": round(total, 2),
            "max_1day_swe_in": round(float(gain.max()), 2),
            "max_2day_swe_in": round(float(gain.rolling(2, min_periods=1).sum().max()), 2),
            "peak_day": gain.idxmax(),
        }
        if d_prec is not None:
            row["prec_in"] = round(float(d_prec.loc[start:end].clip(lower=0).sum()), 2)
        if "depth" in site:
            depth = site["depth"].loc[start:end + pd.Timedelta(days=1)]
            if depth.notna().sum() >= 2 and pd.notna(depth.iloc[0]):
                row["depth_gain_in"] = float(depth.max() - depth.iloc[0])
        if temp is not None:
            t = temp.loc[start:end]
            row["temp_f"] = round(float(t.mean()), 1) if t.notna().any() else np.nan
        rows.append(row)
    ev = pd.DataFrame(rows)
    if ev.empty:
        return ev
    ev["water_year"] = ev["start"].dt.year + (ev["start"].dt.month >= 10).astype(int)
    ev["rank"] = ev["swe_in"].rank(ascending=False, method="first").astype(int)
    ev["rank_2day"] = ev["max_2day_swe_in"].rank(ascending=False, method="first").astype(int)
    ev = ev.sort_values("start").reset_index(drop=True)
    ev.insert(0, "event_id", ev["start"].dt.strftime("%Y%m%d"))
    return ev


def window_gain(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Sum of the positive daily gains of `series` over an event window (NaN if any day is missing)."""
    g = daily_gain(series).reindex(pd.date_range(start, end, freq="D"))
    return float("nan") if g.isna().any() else round(float(g.clip(lower=0).sum()), 2)


def valley_total(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Sum of a morning observer's daily reports covering an event. A report dated D covers the
    24 hours to about 7 am on D, so the window is the mornings after each event day."""
    days = pd.date_range(start + pd.Timedelta(days=1), end + pd.Timedelta(days=1), freq="D")
    vals = series.reindex(days)
    return float("nan") if vals.isna().any() else round(float(vals.sum()), 2)


MAX_HOURLY_IN = 0.6


def hourly_shape(prec: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    """How a storm's precipitation was spread through time, from the hourly gauge record.
    The gauge reads to 0.1 inch, so short bursts are only resolved in the bigger storms.

    `peak_6h_share` near 1 means nearly everything fell in one six-hour burst (a band or front);
    a low value with many wet hours means long, steady snowfall (typical of orographic flow)."""
    win = prec.reindex(pd.date_range(start, end + pd.Timedelta(days=1), freq="h"))      # midnight to midnight
    if win.notna().mean() < 0.9:
        return {}
    inc = win.interpolate(limit=3).diff().clip(lower=0).fillna(0.0)
    inc = inc.where(inc <= MAX_HOURLY_IN, 0.0)          # larger jumps are gauge servicing, not precipitation
    total = float(inc.sum())
    if total < 0.3:
        return {}
    six = inc.rolling(6, min_periods=1).sum()
    wet = inc.rolling(3, min_periods=1).sum() > 0
    return {"hourly_total_in": round(total, 2), "peak_1h_in": round(float(inc.max()), 2), "peak_6h_in": round(float(six.max()), 2),
            "peak_6h_share": round(float(six.max()) / total, 2), "wet_hours": int(wet.sum()),
            "peak_time": six.idxmax()}
