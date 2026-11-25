"""Hourly precipitation at SNOTEL sites, built so that it adds up to the quality-controlled daily record.

The daily gauge record is checked by NRCS; the hourly record is raw and wobbles by 0.1 inch.
So the daily gain is taken as the truth for how much fell, and the hourly record is used only
to decide when within the day it fell. Days with no usable hourly signal are spread evenly
and flagged.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..resort import Resort, data_dir
from . import snotel

MAX_HOURLY_IN = 0.6          # larger hourly jumps are gauge servicing, not precipitation
SPIKE_IN = 1.0              # a reading this far from the two-day median around it is a glitch
PST = pd.Timedelta(hours=8)  # SNOTEL clocks run on Pacific Standard Time all year


def clean_increments(prec: pd.Series) -> pd.Series:
    """Hourly gauge increments with spikes and downward wobble removed.
    Index is the end of each hour (station clock).

    Within each water year the accumulated reading should only rise, so readings far from
    their neighbours are dropped and a rising curve is fitted through the rest (least squares)."""
    from sklearn.isotonic import IsotonicRegression
    s = prec.asfreq("h")
    year = (s.index - pd.Timedelta(hours=1) + pd.DateOffset(months=3)).year       # the gauge resets on 1 October
    parts = []
    for _, seg in s.groupby(year):
        local = seg.rolling(49, center=True, min_periods=5).median()
        seg = seg.where((seg - local).abs() <= SPIKE_IN)
        ok = seg.notna()
        if ok.sum() < 2:
            parts.append(pd.Series(np.nan, index=seg.index))
            continue
        x = np.arange(len(seg), dtype=float)
        fit = IsotonicRegression().fit(x[ok.to_numpy()], seg[ok].to_numpy()).predict(x)
        gap = seg.isna() & seg.interpolate(limit=3).isna()                         # holes too long to bridge
        parts.append(pd.Series(fit, index=seg.index).where(~gap))
    mono = pd.concat(parts).sort_index()
    inc = mono.diff()
    return inc.clip(lower=0, upper=MAX_HOURLY_IN).where(year == np.roll(year, 1))


def daily_gain(prec: pd.Series) -> pd.Series:
    """Gauge gain over each station day (start-of-day readings), never negative."""
    s = prec.asfreq("D")
    return (s.shift(-1) - s).clip(lower=0)


TICK_IN = 0.1                # the gauge record moves in steps of a tenth of an inch
RISE_END_IN = 0.5           # depth ending the day this much higher counts as new snow that day
RISE_PEAK_IN = 1.0          # so does depth peaking this far above where the day began
LOOK_BACK_DAYS = 5


def depth_rise(depth: pd.Series, days: pd.DatetimeIndex) -> pd.DataFrame:
    """For each station day: how much the depth sensor ended up, and how far it peaked above its
    starting value. NaN where the sensor has no reading at the start of the day."""
    d = depth.asfreq("h")
    local = d.rolling(25, center=True, min_periods=6).median()
    d = d.where((d - local).abs() <= 8.0).rolling(5, center=True, min_periods=2).median()      # single-reading spikes out
    start = d.reindex(days)
    peak = d.rolling(24, min_periods=12).max().reindex(days + pd.Timedelta(days=1))             # highest reading in the day's 24 hours
    return pd.DataFrame({"end": d.reindex(days + pd.Timedelta(days=1)).to_numpy() - start.to_numpy(), "peak": peak.to_numpy() - start.to_numpy()}, index=days)


def settle_ticks(gain: pd.Series, depth: pd.Series) -> pd.Series:
    """Daily gauge gains with late ticks put back where they belong.

    The gauge record rises a tenth of an inch at a time, and the tick often lands a day or
    more after the snow: snow caps the gauge and drops in later, and the remainder of a storm
    registers when the next trace arrives. On two days in three that the gauge rose by a single
    tick, the depth sensor beside it showed no new snow. Counting those as snowfall on the day
    they registered taught the model to expect snow on dry days.

    A single tick on a day the depth sensor shows no new snow is moved to the most recent day,
    up to five days back, that did have snow (a gain of two ticks or more, or one tick with a
    rise in depth). With no such day it is dropped. Only the two instruments are used, never a
    forecast. Days without a depth reading are left alone."""
    out = gain.copy()
    rise = depth_rise(depth, gain.index)
    snowed = (rise["end"] >= RISE_END_IN) | (rise["peak"] >= RISE_PEAK_IN)
    one_tick = (gain > 0.005) & (gain <= TICK_IN + 0.005)
    real = ((gain > TICK_IN + 0.005) | (one_tick & snowed)).to_numpy()
    late = (one_tick & ~snowed & rise["end"].notna()).to_numpy()
    values = out.to_numpy(copy=True)
    for i in np.nonzero(late)[0]:
        back = [j for j in range(i - 1, max(i - LOOK_BACK_DAYS, 0) - 1, -1) if real[j]]
        if back:
            values[back[0]] += values[i]
        values[i] = 0.0
    return pd.Series(values, index=gain.index)


def hourly_water(daily_prec: pd.Series, hourly_prec: pd.Series, depth: pd.Series | None = None) -> pd.DataFrame:
    """Hourly precipitation in inches, indexed by the UTC end of each hour.
    `spread` is True where the day's total had to be spread evenly. With the hourly depth
    record given, late gauge ticks are moved back to the day it snowed (see `settle_ticks`)."""
    gain = daily_gain(daily_prec).dropna()
    if depth is not None:
        gain = settle_ticks(gain, depth)
    inc = clean_increments(hourly_prec)
    hours = pd.date_range(gain.index[0] + pd.Timedelta(hours=1), gain.index[-1] + pd.Timedelta(days=1), freq="h")
    inc = inc.reindex(hours)
    day = (hours - pd.Timedelta(hours=1)).normalize()            # the station day each hour belongs to
    df = pd.DataFrame({"inc": inc.to_numpy(), "day": day}, index=hours)
    stats = df.groupby("day")["inc"].agg(total="sum", have="count")
    g = gain.reindex(stats.index)
    usable = (stats["total"] > 0) & (stats["have"] >= 22)
    share = np.where(usable.reindex(df["day"]).to_numpy(),
                     df["inc"].fillna(0).to_numpy() / stats["total"].replace(0, np.nan).reindex(df["day"]).to_numpy(),
                     1.0 / 24.0)
    water = g.reindex(df["day"]).to_numpy() * share
    spread = ~usable.reindex(df["day"]).to_numpy() & (g.reindex(df["day"]).to_numpy() > 0)
    out = pd.DataFrame({"water_in": water, "spread": spread}, index=hours + PST)
    out.index.name = "time_utc"
    return out.dropna(subset=["water_in"])


def window_sum(hw: pd.DataFrame, starts: pd.DatetimeIndex, hours: int) -> pd.DataFrame:
    """Total over [start, start + hours) for each start. NaN when any hour is missing."""
    full = hw.reindex(pd.date_range(hw.index[0], hw.index[-1], freq="h"))
    w = full["water_in"].rolling(hours, min_periods=hours).sum()
    s = full["spread"].astype(float).mul(full["water_in"]).rolling(hours, min_periods=hours).sum()
    ends = starts + pd.Timedelta(hours=hours)
    total = w.reindex(ends).to_numpy()
    return pd.DataFrame({"obs_in": total, "spread_in": s.reindex(ends).to_numpy()}, index=starts)


def build(resort: Resort, first_wy: int = 2019) -> pd.DataFrame:
    """Hourly precipitation for every SNOTEL site in the resort file, saved as one table."""
    daily = pd.read_parquet(data_dir() / "obs" / "snotel_daily.parquet")
    frames = []
    for site in resort.snotel:
        d = daily[daily["site"] == site["id"]].set_index("date")["prec"]
        d = d[d.index >= f"{first_wy - 1}-10-01"]
        h = snotel.load_hourly(site["triplet"], first_wy=first_wy)
        hw = hourly_water(d, h["prec"], h["depth"])
        hw.insert(0, "site", site["id"])
        frames.append(hw.reset_index())
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(data_dir() / "obs" / "snotel_hourly_water.parquet")
    return out
