"""Upper-air features for each catalogued storm, and a flow direction for every winter day."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..resort import data_dir
from . import physics as ph
from . import wb2
from .download import FINE_VARS, PAD, event_chunks, season_chunks

LAT, LON = 39.6, -106.35
PST = pd.Timedelta(hours=8)           # SNOTEL days run midnight to midnight Pacific Standard Time
RIDGE_RELIEF_M = 1000.0               # valley floor to ridge crest, for the Froude number
SHORT = {"u_component_of_wind": "u", "v_component_of_wind": "v", "temperature": "t", "specific_humidity": "q", "geopotential": "z"}


def snotel_day(times: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The SNOTEL day (Pacific Standard Time) each UTC time falls in."""
    return (times - PST).floor("D")


# ---------------------------------------------------------------------------
# Every winter day, coarse grid
# ---------------------------------------------------------------------------

def daily_flow(store: wb2.Store = wb2.COARSE) -> pd.DataFrame:
    """Daily mean wind at 600 and 500 mb and temperature at 700 and 600 mb over Vail."""
    frames = []
    for c in season_chunks(store):
        u, v, t = (store.profile(var, c, LAT, LON) for var in ("u_component_of_wind", "v_component_of_wind", "temperature"))
        f = pd.DataFrame({"u600": u[600], "v600": v[600], "u500": u[500], "v500": v[500],
                          "t700": t[700] - 273.15, "t600": t[600] - 273.15, "t500": t[500] - 273.15})
        f["spd600"] = np.hypot(f["u600"], f["v600"])
        frames.append(f)
    six = pd.concat(frames).sort_index()
    six = six[~six.index.duplicated()]
    day = six.groupby(snotel_day(six.index)).agg(["mean", "count"])
    full = day[("u600", "count")] == 4
    d = day.xs("mean", axis=1, level=1)[full].copy()
    d.index.name = "date"
    d["spd600_vec"], d["dir600"] = wb2.wind_from(d["u600"], d["v600"])
    d["spd500_vec"], d["dir500"] = wb2.wind_from(d["u500"], d["v500"])
    return d


# ---------------------------------------------------------------------------
# Top storms, fine grid
# ---------------------------------------------------------------------------

def _closed_low(z500: np.ndarray, store: wb2.Store, search_deg: float = 9.0, ring_deg: float = 7.5, depth_m: float = 30.0):
    """Nearest closed 500 mb low to Vail: a local minimum within `search_deg` that is at least
    `depth_m` lower than every point on a ring `ring_deg` away. Returns (distance km, bearing deg) or (nan, nan)."""
    lon, lat = store.lon, store.lat
    best = (np.nan, np.nan)
    il0, ia0 = store.nearest(LAT, LON)
    n = int(round(search_deg / 1.5))
    k = int(round(ring_deg / 1.5))
    for di in range(-n, n + 1):
        for dj in range(-n, n + 1):
            i, j = (il0 + di) % store.n_lon, ia0 + dj
            centre = z500[i, j]
            around = [z500[(i + a) % store.n_lon, j + b] for a in (-1, 0, 1) for b in (-1, 0, 1) if a or b]
            if centre > min(around):
                continue                      # not the bottom of the low
            ring = [z500[(i + a) % store.n_lon, j + b] for a in range(-k, k + 1) for b in range(-k, k + 1)
                    if max(abs(a), abs(b)) == k]
            if centre + depth_m <= min(ring):
                dx = ((lon[i] - LON % 360.0 + 180) % 360 - 180) * 111.2 * np.cos(np.radians(LAT))
                dy = (lat[j] - LAT) * 111.2
                dist = float(np.hypot(dx, dy))
                if np.isnan(best[0]) or dist < best[0]:
                    best = (dist, float(np.degrees(np.arctan2(dx, dy)) % 360.0))
    return best


def _trough_lon(z500: np.ndarray, store: wb2.Store) -> float:
    """Longitude (degrees east, negative west) of the lowest 500 mb height along Vail's latitude, 140W to 90W."""
    _, ia = store.nearest(LAT, LON)
    lons = (store.lon + 180.0) % 360.0 - 180.0
    sel = (lons >= -140.0) & (lons <= -90.0)
    row = np.mean(z500[:, ia - 1:ia + 2], axis=1)
    return float(lons[sel][np.argmin(row[sel])])


def _jet(u300: np.ndarray, v300: np.ndarray, store: wb2.Store) -> tuple[float, float, float]:
    """Strongest 300 mb wind within 7.5 degrees of longitude of Vail between 25N and 55N:
    (speed, latitude offset from Vail in degrees, speed directly overhead)."""
    il, ia = store.nearest(LAT, LON)
    spd = np.hypot(u300, v300)
    lats = store.lat
    js = np.flatnonzero((lats >= 25.0) & (lats <= 55.0))
    cols = [(il + d) % store.n_lon for d in range(-5, 6)]
    band = spd[np.ix_(cols, js)].mean(axis=0)
    k = int(np.argmax(band))
    return float(band[k]), float(lats[js[k]] - LAT), float(spd[il, ia])


def event_features(event: pd.Series, daily_gain: pd.Series, store: wb2.Store = wb2.FINE) -> dict | None:
    """Upper-air summary of one storm. Six-hourly values are weighted by the water that fell
    on the SNOTEL day they belong to, so the summary describes the hours that produced the snow."""
    start, end = event["start"], event["end"]
    if end + pd.Timedelta(days=1) + PAD > wb2.END:
        return None
    chunks = event_chunks(store, pd.DataFrame({"start": [start], "end": [end]}))
    prof = {SHORT[v]: pd.concat([store.profile(v, c, LAT, LON) for c in chunks]) for v in FINE_VARS}
    sfc = {v: pd.concat([store.profile(v, c, LAT, LON, levels=False) for c in chunks])
           for v in ("mean_sea_level_pressure", "total_column_water_vapour")}
    times = prof["u"].index
    w = daily_gain.reindex(snotel_day(times)).to_numpy(dtype=float)
    w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
    if w.sum() <= 0:
        return None
    on = w > 0
    wn = w / w.sum()
    avg = lambda s: float((np.asarray(s, dtype=float) * wn).sum())
    u, v, t, q, z = (prof[k] for k in "uvtqz")
    hgt = z / ph.G

    out = {"event_id": event["event_id"]}
    for lev in (700, 600, 500):
        d, vec, steady = ph.vector_mean_direction(u[lev], v[lev], w)
        out[f"dir{lev}"], out[f"spd{lev}"], out[f"steady{lev}"] = round(d, 1), round(avg(np.hypot(u[lev], v[lev])), 1), round(steady, 2)
        out[f"t{lev}_c"] = round(avg(t[lev]) - 273.15, 1)
    out["spd600_max"] = round(float(np.hypot(u[600], v[600])[on].max()), 1)
    first, last = np.flatnonzero(on)[0], np.flatnonzero(on)[-1]
    _, d0 = wb2.wind_from(u[600].iloc[first], v[600].iloc[first])
    _, d1 = wb2.wind_from(u[600].iloc[last], v[600].iloc[last])
    out["turn600_deg"] = round(float(ph.angle_diff(d1, d0)), 0)         # positive = veered clockwise
    out["rh700"] = round(avg(ph.relative_humidity(q[700], t[700], 700)), 2)
    out["rh600"] = round(avg(ph.relative_humidity(q[600], t[600], 600)), 2)
    out["rh500"] = round(avg(ph.relative_humidity(q[500], t[500], 500)), 2)
    out["lapse_700_500"] = round(avg(ph.lapse_rate(t[700], t[500], hgt[700], hgt[500])), 2)
    n = ph.brunt_vaisala(t[700], t[500], 700, 500, hgt[700], hgt[500])
    fr = ph.froude(np.hypot(u[600], v[600]), n, RIDGE_RELIEF_M)
    out["froude"] = round(float(np.median(fr[on])), 2)
    out["tcwv_mm"] = round(avg(sfc["total_column_water_vapour"]), 1)
    levs = [700, 600, 500, 400, 300]
    out["ivt_above_700"] = round(avg([ph.integrated_vapour_transport(q.loc[ti, levs], u.loc[ti, levs], v.loc[ti, levs], levs) for ti in times]), 0)
    mslp = sfc["mean_sea_level_pressure"] / 100.0
    out["mslp_hpa"] = round(avg(mslp), 1)
    out["mslp_change_hpa"] = round(float(mslp.iloc[last] - mslp.iloc[first]), 1)

    # snow-growth layer (about -12 to -18 C) and freezing level, from the profile above the surface
    up = [700, 600, 500, 400, 300]
    dgz_p, dgz_rh, dgz_z, frz = [], [], [], []
    for ti in times:
        tc = (t.loc[ti, up] - 273.15).to_numpy()
        p15 = ph.level_of(-15.0, tc, up)
        dgz_p.append(p15)
        if np.isfinite(p15):
            qi, ti_k = np.interp(-p15, [-x for x in up], q.loc[ti, up]), 258.15
            dgz_rh.append(float(ph.relative_humidity(qi, ti_k, p15)))
            dgz_z.append(float(np.interp(-p15, [-x for x in up], hgt.loc[ti, up])))
        else:
            dgz_rh.append(np.nan)
            dgz_z.append(np.nan)
        full = [850, 700, 600, 500]
        frz.append(ph.level_of(0.0, (t.loc[ti, full] - 273.15).to_numpy(), hgt.loc[ti, full].to_numpy()))
    wavg = lambda a: float(np.nansum(np.asarray(a) * wn) / np.sum(wn[np.isfinite(a)])) if np.isfinite(np.asarray(a)[on]).any() else np.nan
    out["dgz_pressure_hpa"] = round(wavg(dgz_p), 0)
    out["dgz_height_m"] = round(wavg(dgz_z), 0)
    out["dgz_rh"] = round(wavg(dgz_rh), 2)
    out["freezing_level_m"] = round(wavg(frz), 0) if np.isfinite(wavg(frz)) else np.nan    # NaN = below 850 mb throughout

    # large-scale pattern at the six-hourly time with the most weight (ties: the middle one)
    peak_t = times[np.flatnonzero(w == w.max())[len(np.flatnonzero(w == w.max())) // 2]]
    c = store.chunk_of(peak_t)
    k = int((peak_t - store.chunk_times(c)[0]) / wb2.STEP)
    z500 = store.load("geopotential", c)[k, wb2.LEVELS.index(500)] / ph.G
    u300 = store.load("u_component_of_wind", c)[k, wb2.LEVELS.index(300)]
    v300 = store.load("v_component_of_wind", c)[k, wb2.LEVELS.index(300)]
    dist, bearing = _closed_low(z500, store)
    out["closed_low_km"], out["closed_low_bearing"] = (round(dist), round(bearing)) if np.isfinite(dist) else (np.nan, np.nan)
    out["trough_lon"] = _trough_lon(z500, store)
    out["jet_speed"], out["jet_lat_offset"], out["spd300_overhead"] = (round(x, 1) for x in _jet(u300, v300, store))
    il, ia = store.nearest(LAT, LON)
    out["z500_m"] = round(float(z500[il, ia]))
    return out


def build_all() -> dict:
    out = data_dir() / "upper"
    out.mkdir(parents=True, exist_ok=True)
    daily = daily_flow()
    daily.to_parquet(out / "daily_flow.parquet")
    events = pd.read_parquet(data_dir() / "catalog" / "events.parquet")
    sn = pd.read_parquet(data_dir() / "obs" / "snotel_daily.parquet")
    from ..catalog.events import daily_gain as gain
    g = gain(sn[sn["site"] == "vail"].set_index("date")["swe"]).clip(lower=0)
    rows = [f for _, e in events[events["top"]].iterrows() if (f := event_features(e, g)) is not None]
    feats = pd.DataFrame(rows)
    feats.to_parquet(out / "event_features.parquet", index=False)
    return {"days": len(daily), "first_day": str(daily.index.min().date()), "last_day": str(daily.index.max().date()), "events": len(feats)}
