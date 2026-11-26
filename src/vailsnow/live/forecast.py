"""The live forecast: newest model runs in, map files out.

One forecast is built from a 12z HRRR run (issued 5 am Mountain Standard Time) with the GFS and
GEFS runs of the same hour. It covers three 24-hour days. Every layer from Phases 3 and 4 is
applied on the 30 m grid, and the results are written as static files the map reads:

    meta.json      what was run, the day-by-day numbers at the gauge, the hourly curve, the outlook
    zones.json     one card per zone: likely total and range, by layer and time window
    why.json       the plain-English explanation and the closest past days
    grid.json      a 60 m grid of every layer, for tap-anywhere point forecasts
    contours/      filled bands and contour lines per layer and window, plus 6-hourly frames
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ..catalog.regimes import sector
from ..extras import analogs as analog_finder
from ..extras import firstchair, liftwind
from ..fcst import archive, ecmwf, grib, hrrr
from ..model import exposure, final, settle, slr, zones
from ..model import orographic as og
from ..model.downscale import ANOMALY_LIMIT, HRRR_FLOOR_IN
from ..model.hindcast import PRIMARY
from ..model.snowfall import SITE_DAMPING
from ..model.snowlevel import KELVIN, at_elevation, snow_fraction, snow_level_m
from ..model.table import EUROPEAN_LAG_H, MM_PER_IN, elevation_matched, hrrr_window, sites
from ..resort import ROOT, Resort, data_dir, load_resort, web_data_dir
from . import contours, grid

WINDOWS = (24, 48, 72)
FRAME_STEP = 6
DENSITY_EDGES = (0.0, 8.0, 11.0, 14.0, 18.0)
DENSITY_LABELS = ("heavy", "dense", "average", "light", "blower")
COMPASS = ("north", "north-northeast", "northeast", "east-northeast", "east", "east-southeast", "southeast", "south-southeast",
           "south", "south-southwest", "southwest", "west-southwest", "west", "west-northwest", "northwest", "north-northwest")


def compass(deg: float) -> str:
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def density_word(ratio: float) -> str:
    return DENSITY_LABELS[int(np.searchsorted(DENSITY_EDGES[1:], ratio, side="right"))]


def latest_run(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """The newest 12z run whose HRRR forecast has been published."""
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    day = now.normalize()
    for back in range(4):
        run = day - pd.Timedelta(days=back) + pd.Timedelta(hours=12)
        if run <= now and hrrr.fetch_run(run) in ("have", "done", "partial") and hrrr.load_run(run) is not None:
            return run
    raise RuntimeError("no recent 12z HRRR run is available")


def day_features(resort: Resort, run: pd.Timestamp) -> tuple[dict[int, pd.Series], dict]:
    """Model inputs for day 1, 2 and 3 of a run, plus the HRRR arrays the fields need."""
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    pts = sites(resort)
    cells = {k: hrrr.window_cell(la, lo) for k, (la, lo, _) in pts.items()}
    terrain = hrrr.terrain()
    elev_cell = elevation_matched(terrain, *cells[target], pts[target][2])
    hrrr.fetch_run(run)
    d = hrrr.load_run(run)
    if d is None:
        raise RuntimeError(f"HRRR run {run} is not available")
    days: dict[int, dict] = {1: hrrr_window(d, 1, 24, cells, target, terrain, elev_cell) or {},
                             2: hrrr_window(d, 25, 48, cells, target, terrain, elev_cell) or {}, 3: {}}
    prev_run = run - pd.Timedelta(hours=12)
    hrrr.fetch_run(prev_run)
    prev = hrrr.load_run(prev_run)
    if prev is not None and days[1]:
        p = hrrr_window(prev, 13, 36, cells, target, terrain, elev_cell)
        if p:
            days[1].update(prev_qpf_3x3=p["qpf_3x3"], prev_qpf_elev=p["qpf_elev"], run_gap=abs(days[1]["qpf_3x3"] - p["qpf_3x3"]))
    la, lo, _ = pts[target]
    archive.fetch_gfs(run)
    g = archive.load("gfs", run)
    if g is not None:
        r, c = grib.nearest(g["lat"], g["lon"], la, lo)
        acc = {0: 0.0, **{h: float(g[f"apcp_{h:03d}"][r, c]) if f"apcp_{h:03d}" in g else np.nan for h in (24, 48, 72)}}
        area = {0: 0.0, **{h: float(g[f"apcp_{h:03d}"].mean()) if f"apcp_{h:03d}" in g else np.nan for h in (24, 48, 72)}}
        for day in (1, 2, 3):
            a, b, mid = (day - 1) * 24, day * 24, (day - 1) * 24 + 12
            days[day].update(gfs_qpf=(acc[b] - acc[a]) / MM_PER_IN, gfs_qpf_area=(area[b] - area[a]) / MM_PER_IN)
            if f"u600_{mid:03d}" in g:
                u, v = float(g[f"u600_{mid:03d}"][r, c]), float(g[f"v600_{mid:03d}"][r, c])
                days[day].update(gfs_u600=u, gfs_v600=v, gfs_spd600=float(np.hypot(u, v)), gfs_dir600=float(np.degrees(np.arctan2(-u, -v)) % 360),
                                 gfs_t600=float(g[f"t600_{mid:03d}"][r, c]), gfs_rh600=float(g[f"rh600_{mid:03d}"][r, c]),
                                 gfs_t700=float(g[f"t700_{mid:03d}"][r, c]), gfs_pwat=float(g[f"pwat_{mid:03d}"][r, c]))
    try:
        archive.fetch_gefs(run)
        e = archive.load("gefs", run)
    except Exception:
        e = None
    if e is not None:
        r, c = grib.nearest(e["lat"], e["lon"], la, lo)
        for day in (1, 2, 3):
            keys = [f"{(day - 1) * 24 + s:03d}" for s in (6, 12, 18, 24)]
            if all(f"mean_{k}" in e and f"spread_{k}" in e for k in keys):
                days[day].update(gefs_mean=sum(float(e[f"mean_{k}"][r, c]) for k in keys) / MM_PER_IN,
                                 gefs_spread=float(np.sqrt(sum(float(e[f"spread_{k}"][r, c]) ** 2 for k in keys))) / MM_PER_IN)
    try:                                                      # the European ensemble's 00z run: out since about 07:45
        ec_run = run - pd.Timedelta(hours=EUROPEAN_LAG_H)
        ecmwf.fetch(ec_run)
        for day in (1, 2, 3):
            f = ecmwf.for_window(la, lo, ec_run, (day - 1) * 24 + EUROPEAN_LAG_H)
            days[day].update(ec_mean=f["ec_mean"], ec_area=f["ec_area"])
    except Exception:
        pass                                                  # the forecast stands without it
    gr, gc = cells[target]
    raw = {"apcp": d["apcp"], "hours": d["h_apcp"], "gauge_cell": (gr, gc)}
    return {k: pd.Series(v, dtype=float) for k, v in days.items()}, raw


def at_gauge(f: pd.Series, p: dict, slr_model, elev_m: float) -> dict:
    """Snow share, snow-to-liquid ratio and temperature at the gauge's own elevation."""
    if not np.isfinite(f.get("t_ref", np.nan)):
        return {"ratio": 13.0, "snow_share": 1.0, "temp_c": -6.0}
    t = float(at_elevation(f["t_ref"], f["t_per_km"], elev_m)) - KELVIN
    tw = float(at_elevation(f["tw_ref"], f["tw_per_km"], elev_m)) - KELVIN
    feats = pd.DataFrame({"t_site_c": [t], "tw_site_c": [tw], "t700_c": [f["t700"] - KELVIN], "t500_c": [f["t500"] - KELVIN], "spd600": [f["spd600"]],
                          "rh700": [f["rh700"]], "liquid": [max(p["q75"], slr.MIN_LIQUID_IN)], "elev_m": [elev_m]})
    return {"ratio": float(slr.predict(slr_model, feats)[0]), "snow_share": float(snow_fraction(tw)), "temp_c": t}


def gauge_days(resort: Resort, run: pd.Timestamp) -> tuple[list[dict], np.ndarray]:
    """The forecast at the gauge only, with no map: what the scorecard logs. Used to replay past runs."""
    days, raw = day_features(resort, run)
    fill_day3(days)
    slr_model = joblib.load(final.final_dir() / "slr.joblib")
    elev_m = next(s for s in resort.snotel if s.get("target"))["elev_ft"] / 3.280839895
    out = []
    for i, tier in enumerate(final.TIERS):
        f = days[i + 1]
        main = "gfs_qpf" if tier == 72 else "qpf_3x3"
        if not np.isfinite(f.get(main, np.nan)):
            out.append({"day": i + 1, "available": False})
            continue
        p = final.predict(tier, pd.DataFrame([f.reindex(final.load(tier)["cols"])]))
        regime = sector(float(f["dir600"]), float(f.get("spd600_vec", f["spd600"])))
        out.append({"day": i + 1, "available": True, "start": (run + pd.Timedelta(days=i)).isoformat(), "water_in": {k: round(v, 3) for k, v in p.items()},
                    "raw_in": float(f[main]), "gauge": at_gauge(f, p, slr_model, elev_m), "regime": regime,
                    "upside_setup": bool(regime == "northwest" and f.get("spd500", 0) >= 30 and f["rh700"] >= 0.8)})
    return out, hourly_shares(raw)


RANGE_BLEND = 0.25


def combine_ranges(likely: np.ndarray, low: np.ndarray, high: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """10th and 90th percentile of a multi-day total from each day's (first axis is the day).

    Adding the daily bounds assumes every day misses the same way and is too wide: on held-out
    three-day totals it held 92% of outcomes. Adding the half-widths in quadrature assumes the
    days are unrelated and is too narrow (76%). A quarter of the way from the second to the first
    held 81%, with 9.5% above the top, so that is used."""
    centre = likely.sum(axis=0)
    down = np.sqrt(((likely - low) ** 2).sum(axis=0))
    up = np.sqrt(((high - likely) ** 2).sum(axis=0))
    k = RANGE_BLEND
    return (1 - k) * (centre - down) + k * low.sum(axis=0), (1 - k) * (centre + up) + k * high.sum(axis=0)


def hourly_shares(raw: dict) -> np.ndarray:
    """(3, 24): the share of each day's water falling in each hour, from HRRR around the gauge.
    Day 3, and any day HRRR keeps dry, is spread evenly."""
    r, c = raw["gauge_cell"]
    out = np.full((3, 24), 1.0 / 24.0)
    for day in (0, 1):
        idx = np.nonzero((raw["hours"] > day * 24) & (raw["hours"] <= (day + 1) * 24))[0]
        if len(idx) == 24:
            near = np.nanmean(raw["apcp"][idx, r - 1: r + 2, c - 1: c + 2], axis=(1, 2))
            if near.sum() > 0.25:                      # a quarter of a millimetre: enough to trust the timing
                out[day] = near / near.sum()
    return out


def departure(raw: dict, day: int, g: dict) -> np.ndarray:
    """HRRR's pattern for the day over its own average pattern, on the forecast grid."""
    r, c = raw["gauge_cell"]
    idx = np.nonzero((raw["hours"] > (day - 1) * 24) & (raw["hours"] <= day * 24))[0]
    if day > 2 or len(idx) != 24:
        return np.ones(g["elev"].shape)
    k = zones.REACH
    field = raw["apcp"][idx, r - k: r + k + 1, c - k: c + k + 1].sum(axis=0) / MM_PER_IN
    if field[k, k] < HRRR_FLOOR_IN:
        return np.ones(g["elev"].shape)
    average = np.load(final.final_dir() / "hrrr_average_pattern.npy")
    dep = np.clip((field / field[k, k]) / average, 1 / ANOMALY_LIMIT, ANOMALY_LIMIT)
    return og.sample(dep, g["hrrr_row"].ravel(), g["hrrr_col"].ravel()).reshape(g["elev"].shape)


def fill_day3(days: dict[int, pd.Series]) -> None:
    """Day 3 has no HRRR. Its temperature profile is day 2's, shifted by GFS's 700 mb change."""
    d2, d3 = days[2], days[3]
    shift = float(d3.get("gfs_t700", np.nan) - d2.get("gfs_t700", np.nan))
    shift = shift if np.isfinite(shift) else 0.0
    for k in ("t_ref", "tw_ref", "t700", "t500"):
        d3[k] = d2.get(k, np.nan) + shift
    for k in ("t_per_km", "tw_per_km", "rh700"):
        d3[k] = d2.get(k, np.nan)
    d3["spd600"], d3["dir600"] = d3.get("gfs_spd600", d2.get("spd600", np.nan)), d3.get("gfs_dir600", d2.get("dir600", np.nan))
    d3["spd600_vec"] = d3["spd600"]
    if np.isfinite(d3.get("gfs_rh600", np.nan)):
        d3["rh700"] = d3["gfs_rh600"] / 100.0


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":")))


def build(run: pd.Timestamp | None = None, name: str | None = None, resort: Resort | None = None) -> dict:
    resort = resort or load_resort()
    run = run or latest_run()
    out = web_data_dir() / (name or f"{run:%Y%m%d}")
    g = grid.static(resort)
    inside = g["zone"] > 0
    elev = g["elev"][inside]
    days, raw = day_features(resort, run)
    fill_day3(days)
    slr_model = joblib.load(final.final_dir() / "slr.joblib")
    shares = hourly_shares(raw)
    layers10 = exposure.load_layers(resort)
    shape10 = tuple(int(v) for v in g["shape10"])
    new_rate, old_rate, self_rate = settle.fitted_rates()
    gauge_elev = next(s for s in resort.snotel if s.get("target"))["elev_ft"] / 3.280839895
    ridge_elev, base_elev = float(np.quantile(elev, 0.98)), float(elev.min())

    n = int(inside.sum())
    snow, low, high, wind, water, ratio_d, share_d, temp_d = (np.zeros((3, n)) for _ in range(8))
    wind_ratio = np.ones((3, n))
    per_inch_d = np.full((3, n), np.nan)
    mid_lo, mid_hi = np.zeros((3, n)), np.zeros((3, n))       # 25th and 75th percentile: the likely range
    summary = []
    for i, tier in enumerate(final.TIERS):
        f = days[i + 1]
        main = "gfs_qpf" if tier == 72 else "qpf_3x3"
        if not np.isfinite(f.get(main, np.nan)):
            summary.append({"day": i + 1, "available": False})
            continue
        cols = final.load(tier)["cols"]
        p = final.predict(tier, pd.DataFrame([f.reindex(cols)]))
        dep = departure(raw, i + 1, g)[inside]
        factor = g["prism"][inside] * dep ** SITE_DAMPING
        tw = at_elevation(f["tw_ref"], f["tw_per_km"], elev) - KELVIN
        t = at_elevation(f["t_ref"], f["t_per_km"], elev) - KELVIN
        w = p["q50"] * factor                      # "likely" is the median: half the time more falls, half the time less
        feats = pd.DataFrame({"t_site_c": t, "tw_site_c": tw, "t700_c": f["t700"] - KELVIN, "t500_c": f["t500"] - KELVIN,
                              "spd600": f["spd600"], "rh700": f["rh700"], "liquid": np.maximum(p["q75"] * factor, slr.MIN_LIQUID_IN), "elev_m": elev})
        ratio = slr.predict(slr_model, feats) if np.isfinite(f["t_ref"]) else np.full(n, 13.0)
        share = snow_fraction(tw) if np.isfinite(f["tw_ref"]) else np.ones(n)
        water[i], ratio_d[i], share_d[i], temp_d[i] = w, ratio, share, np.nan_to_num(t, nan=-6.0)
        per_inch = factor * share * ratio          # inches of snow per inch of water at the gauge
        snow[i], low[i], high[i] = p["q50"] * per_inch, p["q10"] * per_inch, p["q90"] * per_inch
        # wind: on the 10 m grid, then averaged back
        full = np.full(g["elev"].shape, np.nan)
        full[inside] = snow[i]
        speed = zones.RIDGE_WIND_SHARE * float(f["spd600"]) if np.isfinite(f["spd600"]) else 0.0
        moving = exposure.mobility(speed, float(np.nanmean(ratio)), float(at_elevation(f["tw_ref"], f["tw_per_km"], ridge_elev)) - KELVIN) if np.isfinite(f["tw_ref"]) else 0.0
        has_likely = bool(snow[i].max() > 0.05)
        base = snow[i] if has_likely else high[i]                # a dry "likely" still has a high end the wind would work on
        loading = exposure.lee_strength(speed, float(np.nanmean(ratio)), float(at_elevation(f["tw_ref"], f["tw_per_km"], ridge_elev)) - KELVIN) if np.isfinite(f["tw_ref"]) else 0.0
        if (moving > 0 or loading > 0) and np.isfinite(f["dir600"]) and base.max() > 0.05:
            full[inside] = base
            ridge_from = zones.ridge_direction(float(f["dir600"]))
            sx = exposure.load_sx(resort, ridge_from)
            # first the snow carried over the ridges as it falls, then what the wind moves once it is down
            fine = exposure.lee_load(grid.upsample(full, shape10), sx, layers10["ridge_distance"], layers10["canopy_cover"], layers10["height"], loading)
            if moving > 0:
                fine = exposure.redistribute(fine, sx, layers10["canopy_cover"], ridge_from, moving)
            blown = grid.block_mean(fine)[inside]
            wind_ratio[i] = np.where(base > 0.01, blown / np.maximum(base, 0.01), 1.0)
        # The forecast is the wind-loaded field: where the snow ends up, not where it would land in still air.
        # Every amount for the day (likely, range, low and high end) is moved by the same ratio.
        per_inch_d[i] = per_inch * wind_ratio[i]
        snow[i], low[i], high[i] = (p[q] * per_inch_d[i] for q in ("q50", "q10", "q90"))
        mid_lo[i], mid_hi[i] = p["q25"] * per_inch_d[i], p["q75"] * per_inch_d[i]
        wind[i] = snow[i]
        gauge_t = float(at_elevation(f["t_ref"], f["t_per_km"], gauge_elev)) - KELVIN
        base_t = float(at_elevation(f["t_ref"], f["t_per_km"], base_elev)) - KELVIN
        regime = sector(float(f["dir600"]), float(f.get("spd600_vec", f["spd600"])))
        summary.append({
            "day": i + 1, "available": True, "start": (run + pd.Timedelta(days=i)).isoformat(), "model": ("HRRR, GFS, GEFS" if tier < 72 else "GFS, GEFS") + (", European ensemble" if "ec_mean" in cols and np.isfinite(f.get("ec_mean", np.nan)) else ""),
            "water_in": {k: round(v, 2) for k, v in p.items()}, "regime": regime,
            "wind_from_deg": round(float(f["dir600"])), "wind_from": compass(float(f["dir600"])), "wind_600mb_ms": round(float(f["spd600"]), 1),
            "ridge_wind_mph": round(speed * 2.237), "share_moved_by_wind": round(moving, 2), "lee_loading": round(loading, 2),
            "ridge_wind_from": compass(zones.ridge_direction(float(f["dir600"]))),
            "temp_mid_f": round(gauge_t * 1.8 + 32), "temp_base_f": round(base_t * 1.8 + 32),
            "snow_level_ft": None if not np.isfinite(snow_level_m(f["tw_ref"], f["tw_per_km"])) else round(float(snow_level_m(f["tw_ref"], f["tw_per_km"])) * 3.28084, -2),
            "snow_to_liquid_mid": round(float(np.median(ratio)), 1), "quality": density_word(float(np.median(ratio))),
            "humidity_700mb": round(float(f["rh700"]), 2), "column_water_mm": None if not np.isfinite(f.get("pwat", np.nan)) else round(float(f["pwat"]), 1),
            "upside_setup": bool(regime == "northwest" and f.get("spd500", 0) >= 30 and f["rh700"] >= 0.8),
            "raw_in": float(f[main]), "gauge": at_gauge(f, p, slr_model, gauge_elev),
            "features": {k: float(f[k]) for k in final.ANALOG_FEATURES if k in f and np.isfinite(f[k])}})

    # settled depth of the new snow at the end of each window
    hourly_water = np.concatenate([np.outer(water[d] * share_d[d], shares[d]) for d in range(3)], axis=1)
    hourly_ratio = np.concatenate([np.repeat(ratio_d[d][:, None], 24, axis=1) for d in range(3)], axis=1)
    hourly_temp = np.concatenate([np.repeat(temp_d[d][:, None], 24, axis=1) for d in range(3)], axis=1)
    settled = {}
    for h in WINDOWS:
        settled[h], _ = settle.simulate(hourly_water[:, :h], hourly_temp[:, :h], np.where(hourly_ratio[:, :h] > 0, hourly_ratio[:, :h], 13.0),
                                        np.full(n, 40.0), np.full(n, 0.3), new_rate, old_rate, self_rate)
    cum = {}
    for h in WINDOWS:
        d = h // 24
        likely = snow[:d].sum(axis=0)
        lo, hi = combine_ranges(snow[:d], low[:d], high[:d])
        calm = sum(water[k] * share_d[k] * ratio_d[k] for k in range(d))             # the same snow before wind
        moved = np.where(calm > 0.05, likely / np.maximum(calm, 0.05), 1.0)
        # the likely range is the 25th to 75th percentile, combined across days the same way as the wider range.
        # Held-out totals fell inside it 66% of the time for one day, 60% for two and 54% for three.
        m_lo, m_hi = combine_ranges(snow[:d], mid_lo[:d], mid_hi[:d])
        cum[h] = {"fallen": likely, "low": lo, "high": hi, "mid_lo": np.maximum(m_lo, lo), "mid_hi": np.minimum(m_hi, hi), "wind": likely,
                  "settled": settled[h] * moved, "water": water[:d].sum(axis=0)}
    for h in WINDOWS:
        # snow-to-liquid ratio over the window, weighted by each day's high-end snowfall so that a
        # forecast whose likely amount is zero still says what kind of snow it would be
        d = h // 24
        weight = np.where(ratio_d[:d] > 0, high[:d] / np.where(ratio_d[:d] > 0, ratio_d[:d], 1.0), 0.0).sum(axis=0)
        fallback = np.where(ratio_d[:d] > 0, ratio_d[:d], np.nan)
        cum[h]["density"] = np.where(weight > 1e-6, high[:d].sum(axis=0) / np.maximum(weight, 1e-9), np.nan_to_num(np.nanmean(fallback, axis=0), nan=13.0))

    def full(values: np.ndarray) -> np.ndarray:
        a = np.full(g["elev"].shape, np.nan)
        a[inside] = values
        return a

    # contours
    x, y = g["x"][0], g["y"][:, 0]
    sizes = {}
    for h in WINDOWS:
        for layer in ("fallen", "low", "high", "settled"):
            gj = contours.bands(full(cum[h][layer]), x, y, resort.epsg)
            _write(out / "contours" / f"{layer}_{h}.geojson", gj)
            sizes[f"{layer}_{h}"] = len(gj["features"])
        dens = np.where(cum[h]["high"] >= 0.5, cum[h]["density"], np.nan)
        _write(out / "contours" / f"density_{h}.geojson", contours.bands(full(dens), x, y, resort.epsg, DENSITY_EDGES, DENSITY_LABELS))
    curve = np.concatenate([[0.0], np.cumsum(np.concatenate([shares[d] * np.median(snow[d]) for d in range(3)]))])
    frames = list(range(FRAME_STEP, 73, FRAME_STEP))
    for h in frames:
        part = sum(snow[d] * shares[d][: max(0, min(24, h - d * 24))].sum() for d in range(3))
        _write(out / "contours" / f"frame_{h:02d}.geojson", contours.bands(full(part), x, y, resort.epsg))

    # zone cards
    cards = []
    for z in resort.zones:
        sel = g["zone"][inside] == z.index
        if not sel.any():
            continue
        card = {"id": z.id, "name": z.name, "group": z.group, "elev_ft": [round(float(elev[sel].min()) * 3.28084, -1), round(float(elev[sel].max()) * 3.28084, -1)],
                "windows": {}}
        for h in WINDOWS:
            likely, lo, hi = (float(cum[h][k][sel].mean()) for k in ("fallen", "low", "high"))
            lo_r, hi_r = (lo / likely, hi / likely) if likely > 0.05 else (0.0, 0.0)
            settled_m = float(cum[h]["settled"][sel].mean())
            card["windows"][str(h)] = {
                "fallen": [round(likely, 1), round(lo, 1), round(hi, 1)],
                "mid": [round(float(cum[h]["mid_lo"][sel].mean()), 1), round(float(cum[h]["mid_hi"][sel].mean()), 1)],
                "across": [round(float(np.quantile(cum[h]["fallen"][sel], 0.1)), 1), round(float(np.quantile(cum[h]["fallen"][sel], 0.9)), 1)],
                "settled": [round(settled_m, 1), round(settled_m * lo_r, 1), round(settled_m * hi_r, 1)],
                "water": round(float(cum[h]["water"][sel].mean()), 2),
                "density": round(float(np.median(cum[h]["density"][sel])), 1), "quality": density_word(float(np.median(cum[h]["density"][sel])))}
        card["days"] = [[round(float(a[d][sel].mean()), 1) for a in (snow, low, high, mid_lo, mid_hi)] for d in range(3)]      # each day: likely, low end, high end, likely range
        cards.append(card)
    _write(out / "zones.json", cards)
    if name is None:                              # each day's snow per inch of gauge water, for the 10-day map (not a public file)
        live = data_dir() / "live"
        live.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(live / f"per_inch_{run:%Y%m%d%H}.npz", per_inch=per_inch_d.astype(np.float32))
        for old in sorted(live.glob("per_inch_*.npz"))[:-6]:
            old.unlink()

    # lift wind-hold risk, and where to ski first on the next two mornings
    try:
        lifts = liftwind.forecast(resort, run)
    except Exception as e:                       # the gust field is a separate download: the map must not depend on it
        lifts = {"available": False, "days": [], "lifts": [], "error": f"{type(e).__name__}: {e}"}
    _write(out / "lifts.json", lifts)

    # point grid at 60 m: only the cells inside the ski area are stored
    step = 2
    thin_inside = grid.block_mean(np.where(inside, 1.0, np.nan), step)
    keep = np.isfinite(thin_inside).ravel()

    def thin(values: np.ndarray, scale: float = 10.0) -> list[int]:
        return [int(v) for v in np.round(grid.block_mean(full(values), step).ravel()[keep] * scale)]

    zone_thin = g["zone"][step // 2::step, step // 2::step][: thin_inside.shape[0], : thin_inside.shape[1]].ravel()[keep]
    point = {"rows": thin_inside.shape[0], "cols": thin_inside.shape[1], "fit": grid.lonlat_to_cell(g, 1), "step": step, "scale": 10,
             "cells": [int(v) for v in np.nonzero(keep)[0]], "elev_ft": thin(elev * 3.28084, 1.0), "zone": [int(v) for v in zone_thin]}
    for h in WINDOWS:
        for layer in ("fallen", "low", "high", "mid_lo", "mid_hi", "settled", "density"):
            point[f"{layer}_{h}"] = thin(cum[h][layer])
    _write(out / "grid.json", point)

    live_days = [s for s in summary if s.get("available")]
    lead = max(live_days, key=lambda s: (s["water_in"]["q50"], s["water_in"]["q90"])) if live_days else None
    why = explain(live_days, lead, run)
    _write(out / "why.json", why)
    total = {str(h): {"likely": round(float(np.median(cum[h]["fallen"])), 1)} for h in WINDOWS}
    replay = bool(pd.Timestamp.now(tz="UTC").tz_localize(None) - run > pd.Timedelta(hours=36))      # rebuilt after the fact from a past run
    meta = {"resort": resort.name, "view": resort.view or {}, "run": run.isoformat(), "replay": replay, "issued_local": (run - pd.Timedelta(hours=7)).strftime("%a %b %-d, %-I %p MST"),
            "built": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%MZ"), "windows": list(WINDOWS), "frames": frames,
            "bins": {"edges_in": list(contours.BINS_IN), "labels": list(contours.LABELS)},
            "density": {"edges": list(DENSITY_EDGES), "labels": list(DENSITY_LABELS)},
            "days": [{k: v for k, v in s.items() if k not in ("features", "gauge", "raw_in")} for s in summary],
            "hourly_curve_in": [round(float(v), 2) for v in curve], "outlook": [], "median_total_in": total,
            "notes": verification_notes()}
    _write(out / "meta.json", meta)
    index_runs()
    result = {"run": run.isoformat(), "out": str(out), "days": len(live_days), "contour_features": sizes,
              "median_72h_in": total["72"]["likely"], "max_72h_in": round(float(cum[72]["fallen"].max()), 1)}
    # log the forecast before the answer is known, then score everything the gauge has now reported
    from ..extras import scorecard
    try:
        rec = scorecard.gauge_record(resort)
        result["logged"] = scorecard.log_run(resort, run, summary, shares, rec)
        card = scorecard.publish(resort, rec)
        result["scorecard"] = card["counts"]
    except Exception as e:                       # the gauge service being down must not stop the map
        result["scorecard"] = f"not updated: {type(e).__name__}: {e}"
    if name is None and scorecard.now_utc() - run <= pd.Timedelta(hours=36):      # a fresh forecast: refresh the 10-day strip it heads
        try:
            from . import outlook as ten_day
            o = ten_day.build(resort)
            result["outlook"] = {"ensemble_run": o["ensemble_run"], "days": sum(d["available"] for d in o["days"])}
        except Exception as e:
            result["outlook"] = f"not updated: {type(e).__name__}: {e}"
        try:
            from . import depth as base_depth
            result["base_depth_in"] = base_depth.build(resort)["range_in"]
        except Exception as e:
            result["base_depth_in"] = f"not updated: {type(e).__name__}: {e}"
        try:                                      # the daily job tidies up after itself
            from ..extras import housekeeping
            result["pruned"] = housekeeping.prune()["freed"]
        except Exception as e:
            result["pruned"] = f"not run: {type(e).__name__}: {e}"
    return result


def first_chair(resort: Resort, run: pd.Timestamp, g: dict, inside: np.ndarray, snow: np.ndarray, low: np.ndarray, high: np.ndarray,
                wind_ratio: np.ndarray, ratio_d: np.ndarray, shares: np.ndarray, lifts: dict) -> dict:
    """Zones ranked for first chair tomorrow and the day after."""
    zone = g["zone"][inside]
    risk = {(l["name"], d["day"]): d["risk"] for l in lifts.get("lifts", []) for d in l["days"]}
    order = {"low": 0, "possible": 1, "likely": 2}
    mornings = []
    for m in (1, 2):
        parts = firstchair.overnight_parts(shares, m)
        used = [d for d in range(3) if parts[d] > 0 and snow[d].max() + high[d].max() >= 0]
        likely = sum(snow[d] * parts[d] for d in used)
        lo, hi = combine_ranges(np.array([snow[d] * parts[d] for d in used]), np.array([low[d] * parts[d] for d in used]), np.array([high[d] * parts[d] for d in used]))
        weight = sum(high[d] * parts[d] for d in used)                      # the high end says which day's wind and snow type matter
        blown = np.where(weight > 1e-6, sum(high[d] * parts[d] * wind_ratio[d] for d in used) / np.maximum(weight, 1e-9), 1.0)
        water = sum(np.where(ratio_d[d] > 0, high[d] * parts[d] / np.where(ratio_d[d] > 0, ratio_d[d], 1.0), 0.0) for d in used)
        ratio = np.where(water > 1e-6, weight / np.maximum(water, 1e-9), np.nanmean(np.where(ratio_d > 0, ratio_d, np.nan), axis=0))
        rows = []
        for z in resort.zones:
            sel = zone == z.index
            if not sel.any():
                continue
            named = [(n, risk.get((n, m + 1))) for n in z.lifts if (n, m + 1) in risk]      # the morning after a 12z run is its day m + 1
            worst = max(named, key=lambda x: order[x[1]], default=None)
            r = float(np.nan_to_num(np.median(ratio[sel]), nan=13.0))
            rows.append({"id": z.id, "name": z.name, "overnight": [round(float(likely[sel].mean()), 1), round(float(max(lo[sel].mean(), 0.0)), 1), round(float(hi[sel].mean()), 1)],
                         "ratio": round(r, 1), "quality": density_word(r), "scoured_pct": round(100 * float((blown[sel] < firstchair.SCOUR_BELOW).mean())),
                         "loaded_pct": round(100 * float((blown[sel] > firstchair.LOAD_ABOVE).mean())),
                         "lift": None if worst is None else {"name": worst[0], "risk": worst[1]}})
        basis, ranked = firstchair.rank(rows)
        when = run + pd.Timedelta(hours=firstchair.FIRST_CHAIR_HOUR + 24 * (m - 1))
        mornings.append({"morning": m, "first_chair": when.isoformat(), "since": (run + pd.Timedelta(hours=firstchair.CLOSE_HOUR + 24 * (m - 1))).isoformat(),
                         "basis": basis, "lift_risk_known": any(d["day"] == m + 1 for d in lifts.get("days", [])), "zones": ranked})
    return {"mornings": mornings, "weights": {"wind_penalty": firstchair.WIND_PENALTY, "lift_factor": {k: v for k, v in firstchair.LIFT_FACTOR.items() if k},
                                              "quality_limits": list(firstchair.QUALITY_LIMITS), "average_ratio": firstchair.AVERAGE_RATIO}}


def explain(days: list[dict], lead: dict | None, run: pd.Timestamp | None = None) -> dict:
    """Plain-English reasons for the forecast, built from the numbers, plus the closest past days."""
    if not lead:
        return {"headline": "No forecast available.", "points": [], "analogs": []}
    def snow(d: dict, q: str) -> float:                       # as-fallen snow at the gauge's elevation
        return d["water_in"][q] * d["gauge"]["snow_share"] * d["gauge"]["ratio"]

    total, high_end = sum(snow(d, "q50") for d in days), sum(snow(d, "q90") for d in days)
    if total < 0.5:
        headline = "Little or no snow is the most likely outcome over the next three days."
        if high_end >= 2:
            headline += f" The high end is about {high_end:.0f} in at the mid-mountain gauge in total."
    else:
        headline = (f"The most snow is likely on day {lead['day']}: about {snow(lead, 'q50'):.0f} in at the mid-mountain gauge "
                    f"(range {snow(lead, 'q10'):.0f} to {snow(lead, 'q90'):.0f}), from {lead['water_in']['q50']:.2f} in of water.")
    points = []
    for d in days:
        bits = [f"Wind aloft from the {d['wind_from']} at {round(d['wind_600mb_ms'] * 2.237)} mph ({d['regime']} flow)."]
        bits.append(f"Mid-mountain about {d['temp_mid_f']} F, base about {d['temp_base_f']} F.")
        if d["snow_level_ft"] is not None and d["snow_level_ft"] > 7800:
            bits.append(f"Snow level near {int(d['snow_level_ft']):,} ft, so the base may see rain or wet snow.")
        if d["water_in"]["q90"] >= 0.1:
            bits.append(f"Snow quality: {d['quality']} (about {d['snow_to_liquid_mid']:.0f} in of snow per inch of water).")
            if d.get("lee_loading", 0) >= 0.5:
                lee = COMPASS[(COMPASS.index(d["ridge_wind_from"]) + 8) % 16]
                bits.append(f"Ridge winds near {d['ridge_wind_mph']} mph from the {d['ridge_wind_from']} will load {lee}-facing slopes just below the ridges "
                            f"{'heavily' if d['lee_loading'] >= 1.2 else 'and thin the exposed side'}.")
        if d["column_water_mm"] is not None:
            bits.append(f"Moisture: {d['column_water_mm']:.0f} mm of water in the column, {round(d['humidity_700mb'] * 100)}% humidity near ridge level.")
        if d["upside_setup"]:
            bits.append("Upside setup: northwest flow with a strong jet and a moist layer at ridge level. In the record this setup beat the median forecast by about half, on few cases.")
        points.append({"day": d["day"], "start": d["start"], "text": " ".join(bits)})
    found = analog_finder.find(lead["features"], k=3, not_near=run) if len(lead.get("features", {})) == len(final.ANALOG_FEATURES) else []
    return {"headline": headline, "points": points, "analog_day": lead["day"], "analogs": found, "resembles": analog_finder.sentence(found)}


def verification_notes() -> dict:
    """Short, true statements about how well each layer has verified, for the map's footer."""
    m = data_dir() / "model"
    notes = {"zones": "Differences between zones come from long-term precipitation patterns (PRISM), mostly elevation. Nothing measures snow by zone.",
             "wind": "Days 1 to 3 include wind loading: more snow on slopes just over a ridge from the wind, less on the exposed side. Its strength is tuned to "
                     "one skier's account of where Vail loads up (about 13 in in the lee when the base amount is 10). Nothing measures it.",
             "sun": "Sun effects are modelled and unchecked.",
             "first_chair": "The ranking's weights are choices, not fitted values: nothing records where the skiing was best. Zone amounts differ mostly by elevation.",
             "analogs": "Shown for context. On held-out days the average of the closest past days missed by more than the model did."}
    try:
        s = json.loads((m / "scores_precip.json").read_text())[PRIMARY]["24"]
        notes["water"] = (f"Mid-mountain water, 24 h ahead: typical miss {s['all']['methods']['this model (median)']['mae']:.2f} in over "
                          f"{s['all']['n']:,} held-out days ({s['seasons'][0] - 1} to {s['seasons'][1]}). Level with the National Blend of Models.")
        st = json.loads((m / "scores_settle.json").read_text())
        notes["settled"] = f"Settled depth is checked against seven SNOTEL depth sensors: typical miss {st['snow_days']['this model (ratio, then settling)']['mae']:.1f} in on snow days."
        notes["big_days"] = "Big days are under-called by the median. When the setup looks good, read the high end of the range."
        w = json.loads((m / "scores_wind.json").read_text())
        g = w["lift_hours"]["day1"]["HRRR surface gust, as issued (used)"]
        odds = w["odds_check"]["40mph"]
        notes["gusts"] = (f"Ridge gusts are checked at {w['station']['name']} ({w['station']['elev_ft']:,} ft, 20 km away): the day's peak gust in lift hours "
                          f"missed by {g['mae_mph']:.0f} mph on average over {g['n']:,} days.")
        notes["lifts"] = (f"Checked at the summit station only: days called likely to reach 40 mph did so {odds['likely (50% or more)']['reached_pct']}% of the time, "
                          f"possible {odds['possible (20 to 50%)']['reached_pct']}%, low {odds['low (under 20%)']['reached_pct']}%. "
                          "Each lift's share of the ridge gust and the hold thresholds are assumptions; no record of holds is public.")
    except Exception:
        pass
    return notes


def index_runs() -> None:
    """List the forecasts on disk, newest first, for the map's run picker."""
    base = web_data_dir()
    runs = []
    for d in sorted(base.iterdir(), reverse=True):
        meta = d / "meta.json"
        if meta.exists():
            m = json.loads(meta.read_text())
            runs.append({"name": d.name, "run": m["run"], "issued": m["issued_local"], "replay": bool(m.get("replay")), "median_72h_in": m["median_total_in"]["72"]["likely"]})
    runs.sort(key=lambda r: r["run"], reverse=True)
    _write(base / "runs.json", runs)
    if not (base / "alert.json").exists():        # the map asks for it; an empty one until the first alert is built
        _write(base / "alert.json", {"fire": False, "time": "1970-01-01T00:00", "title": "", "lines": []})
