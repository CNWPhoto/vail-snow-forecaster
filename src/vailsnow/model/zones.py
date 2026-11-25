"""The four layers put together for the ski area: one row per zone and elevation band.

    water  = mid-mountain forecast (layer 1)
             x PRISM ratio of the zone band to the gauge cell (layer 2, long-term pattern)
             x damped HRRR departure for the day at the zone's model cell (layer 2, today's pattern)
    snow   = water x share falling as snow at the band's elevation (layer 3)
                   x snow-to-liquid ratio at the band's elevation (layer 4)

Only the mid-mountain water is checked against a measurement on the hill. The spread between
zones is physics and climatology; wind and settling come in Phase 4.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from ..fcst import hrrr
from ..resort import Resort, data_dir, load_resort
from . import exposure, settle, slr
from .downscale import ANOMALY_LIMIT, HRRR_FLOOR_IN
from .snowfall import SITE_DAMPING
from .snowlevel import KELVIN, at_elevation, snow_fraction, snow_level_m
from .table import MM_PER_IN

REACH = 4                    # HRRR cells kept either side of the gauge cell
RIDGE_WIND_BACKING_DEG = 35  # ridge-top wind blows from this much further anticlockwise than the 600 mb wind. Measured at the
                             # 12,075 ft summit station over 17,543 windy hours: 31 deg in west flow, 36 in northwest, 61 in southwest.


def ridge_direction(dir600: float) -> float:
    """Direction the wind blows from at ridge level, given the 600 mb direction."""
    return float((dir600 - RIDGE_WIND_BACKING_DEG) % 360.0)
RIDGE_WIND_SHARE = 0.5       # sustained ridge-top wind as a share of the free-air wind near 600 mb. Measured: 0.47 at a 12,075 ft
                             # summit station over 41,556 forecast hours (extras/liftwind.py). Phases 4 and 5 assumed 0.7.


def day_fields(resort: Resort) -> tuple[pd.DatetimeIndex, np.ndarray]:
    """24-hour HRRR precipitation (inches) around the mountain for every 12z run, cached."""
    path = data_dir() / "model" / "hrrr_24h_fields.npz"
    if path.exists():
        with np.load(path) as z:
            return pd.DatetimeIndex(z["start"]), z["field"]
    target = next(s for s in resort.snotel if s.get("target"))
    r, c = hrrr.window_cell(target["lat"], target["lon"])
    starts, fields = [], []
    for run in hrrr.season_runs(hours=(12,), last=str(pd.Timestamp.now().normalize().date())):
        d = hrrr.load_run(run)
        if d is None:
            continue
        idx = np.nonzero((d["h_apcp"] >= 1) & (d["h_apcp"] <= 24))[0]
        if len(idx) == 24:
            starts.append(run)
            fields.append(d["apcp"][idx, r - REACH: r + REACH + 1, c - REACH: c + REACH + 1].sum(axis=0) / MM_PER_IN)
    np.savez_compressed(path, start=np.array(starts, dtype="datetime64[ns]"), field=np.array(fields, dtype=np.float32))
    return pd.DatetimeIndex(starts), np.array(fields)


def zone_cells(resort: Resort) -> dict[str, tuple[int, int]]:
    """Each zone's HRRR cell, as (row, col) inside the `day_fields` window."""
    target = next(s for s in resort.snotel if s.get("target"))
    r0, c0 = hrrr.window_cell(target["lat"], target["lon"])
    out = {}
    with rasterio.open(resort.out_dir() / "zones_10m.tif") as ds:
        zone = ds.read(1)
        to_ll = Transformer.from_crs(ds.crs, 4326, always_xy=True)
        for z in resort.zones:
            rows, cols = np.nonzero(zone == z.index)
            x, y = ds.xy(rows.mean(), cols.mean())
            lon, lat = to_ll.transform(x, y)
            r, c = hrrr.window_cell(lat, lon)
            out[z.id] = (r - r0 + REACH, c - c0 + REACH)
    return out


def departures(starts: pd.DatetimeIndex, fields: np.ndarray, start: pd.Timestamp, season_of: pd.Series) -> np.ndarray:
    """Today's HRRR pattern relative to its own average pattern, cell by cell, with the day's
    season left out of the average. 1 everywhere when the model has almost nothing at the gauge."""
    i = int(np.nonzero(starts == start)[0][0])
    today, at_gauge = fields[i], fields[i, REACH, REACH]
    if at_gauge < HRRR_FLOOR_IN:
        return np.ones_like(today)
    seasons = season_of.reindex(starts).to_numpy()
    use = (seasons != seasons[i]) & (fields[:, REACH, REACH] >= HRRR_FLOOR_IN)
    average = fields[use].sum(axis=0) / fields[use, REACH, REACH].sum()
    return np.clip((today / at_gauge) / average, 1 / ANOMALY_LIMIT, ANOMALY_LIMIT)


def forecast(start: pd.Timestamp, resort: Resort | None = None) -> pd.DataFrame:
    """Zone-by-band forecast for the 24 hours from `start` (a 12z time), from the hindcast."""
    resort = resort or load_resort()
    out = data_dir() / "model"
    h = pd.read_parquet(out / "hindcast_tier24.parquet").set_index("start")
    row = h.loc[start]
    table = pd.read_parquet(out / "table.parquet")
    cases = slr.cases(resort, table[table["season"] <= h["season"].max()])
    model = slr.fit(cases[cases["season"] != row["season"]])
    starts, fields = day_fields(resort)
    dep = departures(starts, fields, start, h["season"])
    cells = zone_cells(resort)
    prior = json.loads((data_dir() / "prism" / f"{resort.id}_zone_prior.json").read_text())["zones"]
    stats = json.loads((resort.out_dir() / "zone_stats.json").read_text())["zones"]
    rows = []
    for z in resort.zones:
        for band in resort.bands:
            ratio = prior[z.id]["bands"].get(band.id)
            info = stats[z.id]["bands"].get(band.id, {})
            if ratio is None or not info.get("cells"):
                continue
            elev = info["elev_mean_ft"] / 3.280839895
            factor = ratio * dep[cells[z.id]] ** SITE_DAMPING
            tw = float(at_elevation(row["tw_ref"], row["tw_per_km"], elev)) - KELVIN
            t = float(at_elevation(row["t_ref"], row["t_per_km"], elev)) - KELVIN
            water = row["mean"] * factor
            feats = pd.DataFrame([{"t_site_c": t, "tw_site_c": tw, "t700_c": row["t700"] - KELVIN, "t500_c": row["t500"] - KELVIN,
                                   "spd600": row["spd600"], "rh700": row["rh700"], "liquid": max(water, 0.05), "elev_m": elev}])
            ratio_snow, share = float(slr.predict(model, feats)[0]), float(snow_fraction(tw))
            rows.append({"zone": z.id, "name": z.name, "group": z.group, "band": band.id, "acres": info["acres"],
                         "elev_ft": info["elev_mean_ft"], "prism_ratio": ratio, "hrrr_departure": round(float(dep[cells[z.id]]), 2),
                         "water_in": round(water, 2), "water_low_in": round(row["q10"] * factor, 2), "water_high_in": round(row["q90"] * factor, 2),
                         "snow_share": round(share, 2), "snow_to_liquid": round(ratio_snow, 1),
                         "snow_in": round(water * share * ratio_snow, 1), "snow_low_in": round(row["q10"] * factor * share * ratio_snow, 1),
                         "snow_high_in": round(row["q90"] * factor * share * ratio_snow, 1)})
    df = pd.DataFrame(rows)
    df.attrs["snow_level_ft"] = float(snow_level_m(row["tw_ref"], row["tw_per_km"])) * 3.280839895
    return add_wind_and_settling(df, row, start, resort)


def add_wind_and_settling(df: pd.DataFrame, row: pd.Series, start: pd.Timestamp, resort: Resort) -> pd.DataFrame:
    """Phase 4 columns: snow after wind (zone-band mean and the spread between cells), the new
    snow's settled depth the next morning, and after a further clear day."""
    layers = exposure.load_layers(resort)
    zone_id = {z.id: z.index for z in resort.zones}
    band_id = {b.id: b.index for b in resort.bands}
    # as-fallen snow on the 10 m grid: every cell takes its zone band's value
    field = np.full(layers["zone"].shape, np.nan)
    for z, b, snow in zip(df["zone"], df["band"], df["snow_in"]):
        field[(layers["zone"] == zone_id[z]) & (layers["band"] == band_id[b])] = snow
    speed = RIDGE_WIND_SHARE * float(row["spd600"])
    tw_ridge = float(at_elevation(row["tw_ref"], row["tw_per_km"], 3400.0)) - KELVIN
    moving = exposure.mobility(speed, float(df["snow_to_liquid"].mean()), tw_ridge)
    ridge_from = ridge_direction(float(row["dir600"]))
    sx = exposure.load_sx(resort, ridge_from)
    loaded = exposure.lee_load(field, sx, layers["ridge_distance"], layers["canopy_cover"], layers["height"], exposure.lee_strength(speed, float(df["snow_to_liquid"].mean()), tw_ridge))
    blown = exposure.redistribute(loaded, sx, layers["canopy_cover"], ridge_from, moving) if moving > 0 else loaded
    stats = exposure.by_zone_band(blown, layers["zone"], layers["band"], resort)
    df["after_wind_in"] = [round(stats[(z, b)]["mean"], 1) for z, b in zip(df["zone"], df["band"])]
    df["after_wind_low_in"] = [round(stats[(z, b)]["p10"], 1) for z, b in zip(df["zone"], df["band"])]
    df["after_wind_high_in"] = [round(stats[(z, b)]["p90"], 1) for z, b in zip(df["zone"], df["band"])]
    # settling: the day's water spread evenly through the window, at each band's temperature
    new_rate, old_rate, self_rate = settle.fitted_rates()
    n = len(df)
    elev = df["elev_ft"].to_numpy() / 3.280839895
    temp = (at_elevation(row["t_ref"], row["t_per_km"], elev) - KELVIN)[:, None]
    water = np.repeat((df["water_in"] * df["snow_share"]).to_numpy()[:, None] / 24.0, 24, axis=1)
    ratio = df["snow_to_liquid"].to_numpy()
    morning, _ = settle.simulate(water, np.repeat(temp, 24, axis=1), ratio, np.full(n, 40.0), np.full(n, 0.3), new_rate, old_rate, self_rate)
    sun = exposure.sun_index(layers["slope"], layers["aspect"], 39.6, int(start.dayofyear))
    sun_stats = exposure.by_zone_band(sun, layers["zone"], layers["band"], resort)
    index = np.array([sun_stats[(z, b)]["mean"] for z, b in zip(df["zone"], df["band"])])
    two_day = np.concatenate([water, np.zeros_like(water)], axis=1)
    factor = 0.5 * (1.0 + exposure.sun_factor(index, 1.0))          # cloud for the storm day, clear sky for the day after
    clear, _ = settle.simulate(two_day, np.repeat(temp, 48, axis=1), ratio, np.full(n, 40.0), np.full(n, 0.3), new_rate, old_rate, self_rate, sun=factor)
    df["settled_next_morning_in"] = morning.round(1)
    df["sun_index"] = index.round(2)
    df["settled_after_clear_day_in"] = clear.round(1)
    df.attrs.update(wind_from=ridge_from, ridge_wind_ms=round(speed, 1), share_moved=round(moving, 2))
    return df


def by_zone(df: pd.DataFrame) -> pd.DataFrame:
    """Area-weighted zone totals from the band rows."""
    cols = [c for c in ("water_in", "water_low_in", "water_high_in", "snow_in", "snow_low_in", "snow_high_in", "after_wind_in",
                        "settled_next_morning_in", "settled_after_clear_day_in") if c in df]
    w = df["acres"]
    out = df[cols].mul(w, axis=0).groupby([df["group"], df["name"]], sort=False).sum().div(w.groupby([df["group"], df["name"]], sort=False).sum(), axis=0)
    return out.round(2).reset_index()
