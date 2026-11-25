"""Lift wind-hold risk from forecast ridge-top gusts.

Three steps, and only the first can be checked:

1. Ridge gust. HRRR's surface gust at the model's own ridge cell is taken as the peak gust at an
   exposed ridge-top point, and past misses turn it into odds. Both are checked against a public
   airport station on a 12,075 ft summit 20 km away (the resort file names it), with each season
   judged by misses from the other seasons. (A least-squares blend with the 600 mb wind had a
   smaller hourly error but missed half the strong days the raw gust caught, so it is not used.)
2. Each lift's share of that gust: how high its upper half sits, how open and how unsheltered
   that ground is for the day's wind direction, and how far across the line the wind blows.
   These factors are assumptions. No anemometer on the lifts is public.
3. The chance the gust at the lift reaches a hold threshold. The thresholds are rules of thumb
   set in the resort file, not the resort's operating rules. There is no public record of
   holds, so the final answer cannot be scored.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from scipy.ndimage import uniform_filter

from ..fcst import gust, hrrr
from ..model import exposure
from ..obs import asos
from ..resort import Resort, data_dir, load_resort

MPH = 2.23694
THRESHOLDS_MPH = (30, 40, 50)
HEIGHT_FLOOR = 0.6            # the lowest lift on the hill is given this share of the ridge gust (assumption)
SHELTER_FLOOR = 0.5           # a lift whose upper half is fully wooded and sheltered keeps this share (assumption)
ALONG_LINE = 0.8              # wind blowing straight along the line counts for this share of a crosswind (assumption)
POSSIBLE, LIKELY = 0.2, 0.5   # chance of reaching the threshold that earns each word
TREE_REACH_M = 150.0          # tree cover is averaged over a square this wide around the line
SURFACE = ("platter", "t-bar", "magic_carpet", "drag_lift", "rope_tow")


def paths() -> tuple:
    m = data_dir() / "model"
    return m / "wind_cases.parquet", m / "scores_wind.json", m / "final" / "gust.json"


def open_hours(resort: Resort, day: int) -> np.ndarray:
    """Forecast hours of a 12z run whose valid time falls inside lift hours on day 1, 2 or 3.
    12z is 5 am MST, so forecast hour h is valid at 5 am + h: 9 am to 4 pm is hours 4 to 11."""
    first, last = (resort.lift_wind or {}).get("open_hours_mst", [8.5, 16.0])
    return np.arange(int(np.ceil(first - 5.0)), int(np.floor(last - 5.0)) + 1) + 24 * (day - 1)


def run_series(run: pd.Timestamp, r: int, c: int) -> pd.DataFrame | None:
    """Hourly HRRR gust and 600 mb wind at one cell of a 12z run."""
    g, d = gust.load(run), hrrr.load_run(run)
    if g is None or d is None or any(k not in d for k in ("u700", "v700", "u500", "v500")):
        return None
    hours, field = g
    upper = {int(h): i for i, h in enumerate(d["h_u700"])}
    rows = []
    for i, h in enumerate(hours):
        j = upper.get(int(h))
        if j is None:
            continue
        u, v = 0.5 * (d["u700"][j, r, c] + d["u500"][j, r, c]), 0.5 * (d["v700"][j, r, c] + d["v500"][j, r, c])
        rows.append({"h": int(h), "gust": float(field[i, r, c]), "spd600": float(np.hypot(u, v)), "dir600": float(np.degrees(np.arctan2(-u, -v)) % 360)})
    return pd.DataFrame(rows).dropna() if rows else None


# ---------------------------------------------------------------------------
# Step 1: the ridge gust, fitted and checked at the station
# ---------------------------------------------------------------------------

def cases(resort: Resort) -> pd.DataFrame:
    """One row per forecast hour (first 36 h of every winter 12z run) with what the station measured."""
    w = resort.wind_check
    obs = asos.hourly_peak(asos.load(w["station"]))
    r, c = hrrr.window_cell(w["lat"], w["lon"])
    frames = []
    for run in hrrr.season_runs(hours=(12,), months=hrrr.SEASON, last=str(pd.Timestamp.now().normalize().date())):
        s = run_series(run, r, c) if gust.path(run).exists() else None
        if s is not None:
            s = s[s["h"] <= 36].assign(run=run)
            s["time"] = run + pd.to_timedelta(s["h"], unit="h")
            frames.append(s)
    t = pd.concat(frames, ignore_index=True).join(obs, on="time").dropna(subset=["peak_ms"])
    t["season"] = t["time"].dt.year + (t["time"].dt.month >= 10).astype(int)
    t.to_parquet(paths()[0])
    return t


RATIO_FROM_MS = 9.0            # misses are measured on days the forecast peak was at least this (20 mph): the days that matter
LEVELS = np.linspace(0.025, 0.975, 39)


def day_peaks(c: pd.DataFrame, resort: Resort, col: str) -> pd.DataFrame:
    """Strongest forecast and measured gust inside lift hours, per run and day."""
    rows = []
    for day in (1, 2):
        hours = open_hours(resort, day)
        part = c[c["h"].isin(hours)]
        g = part.groupby("run").agg(fc=(col, "max"), obs=("peak_ms", "max"), n=("h", "size"), season=("season", "first"))
        rows.append(g[g["n"] >= len(hours) - 1].assign(day=day))
    return pd.concat(rows).reset_index()


def ratios(p: pd.DataFrame) -> list[float]:
    """Quantiles of measured peak over forecast peak: how far, and which way, the forecast misses."""
    p = p[p["fc"] >= RATIO_FROM_MS]
    return [round(float(v), 3) for v in np.quantile((p["obs"] / p["fc"]).to_numpy(float), LEVELS)]


def evaluate(resort: Resort | None = None) -> dict:
    resort = resort or load_resort()
    c = cases(resort)
    k600 = float((c["spd600"] * c["peak_ms"]).sum() / (c["spd600"] ** 2).sum())
    c["scaled600"] = k600 * c["spd600"]

    def err(col: str, g: pd.DataFrame) -> dict:
        return {"mae_mph": round(float((g[col] - g["peak_ms"]).abs().mean()) * MPH, 1), "bias_mph": round(float((g[col] - g["peak_ms"]).mean()) * MPH, 1),
                "r": round(float(np.corrcoef(g[col], g["peak_ms"])[0, 1]), 2)}

    labels = {"gust": "HRRR surface gust, as issued (used)", "scaled600": "600 mb wind, scaled"}
    out = {"station": resort.wind_check, "hours": int(len(c)), "seasons": [int(c["season"].min()), int(c["season"].max())],
           "model_terrain_m": float(hrrr.terrain()[hrrr.window_cell(resort.wind_check["lat"], resort.wind_check["lon"])]),
           "hourly": {lab: err(col, c) for col, lab in labels.items()},
           "sustained_share_of_600mb": round(float((c["spd600"] * c["wind_ms"]).sum() / (c["spd600"] ** 2).sum()), 2),
           "gust_share_of_600mb": round(k600, 2), "lift_hours": {}, "odds_check": {}}
    peaks = {col: day_peaks(c, resort, col) for col in labels}
    for day in (1, 2):
        block = {}
        for col, lab in labels.items():
            p = peaks[col][peaks[col]["day"] == day].rename(columns={"fc": col, "obs": "peak_ms"})
            e = err(col, p)
            e["n"] = int(len(p))
            for thr in THRESHOLDS_MPH:
                o, f = p["peak_ms"] * MPH >= thr, p[col] * MPH >= thr
                e[f"{thr}mph"] = {"days_measured": int(o.sum()), "caught": int((o & f).sum()), "false_alarms": int((f & ~o).sum())}
            block[lab] = e
        out["lift_hours"][f"day{day}"] = block
    # are the odds honest? each season's days are given odds from the other seasons' misses
    p = peaks["gust"].copy()
    for thr in THRESHOLDS_MPH:
        p[f"p{thr}"] = np.nan
        for season in sorted(p["season"].unique()):
            test = p["season"] == season
            rq = ratios(p[~test])
            p.loc[test, f"p{thr}"] = [chance_at_least(f, thr / MPH, rq) for f in p.loc[test, "fc"]]
        hit = p["obs"] * MPH >= thr
        bins = {"low (under 20%)": p[f"p{thr}"] < POSSIBLE, "possible (20 to 50%)": (p[f"p{thr}"] >= POSSIBLE) & (p[f"p{thr}"] < LIKELY), "likely (50% or more)": p[f"p{thr}"] >= LIKELY}
        out["odds_check"][f"{thr}mph"] = {name: {"days": int(m.sum()), "reached_pct": None if not m.any() else round(100 * float(hit[m].mean()))} for name, m in bins.items()}
    rq = ratios(p)
    out["peak_ratio_10_50_90"] = [rq[3], rq[19], rq[35]]
    cases_path, scores_path, final_path = paths()
    scores_path.write_text(json.dumps(out, indent=1))
    final_path.write_text(json.dumps({"ratio_quantiles": rq, "days": int(len(p)), "station": resort.wind_check["station"]}, indent=1))
    return out


def chance_at_least(forecast_ms: float, threshold_ms: float, ratio_quantiles: list[float]) -> float:
    """Chance the measured peak reaches a threshold given the forecast peak, from past forecast misses."""
    if forecast_ms <= 0:
        return 0.0
    return float(np.mean(np.asarray(ratio_quantiles) * forecast_ms >= threshold_ms))


# ---------------------------------------------------------------------------
# Step 2: each lift's exposure
# ---------------------------------------------------------------------------

def lift_table(resort: Resort) -> list[dict]:
    """Geometry and exposure of every named lift. `shelter[k]` is the share of the ridge gust the
    upper half of the line keeps when the wind blows from the k-th of 16 compass sectors."""
    d = resort.out_dir()
    geo = json.loads((d / "lifts.geojson").read_text())
    with rasterio.open(d / "terrain_10m.tif") as ds:
        names = list(ds.descriptions)
        elev, canopy = ds.read(names.index("elevation") + 1).astype(float), np.nan_to_num(ds.read(names.index("canopy_cover") + 1).astype(float))
        tr, crs = ds.transform, ds.crs
    # a lift line is a cut through the trees, so tree cover is taken from the ground around it
    canopy = uniform_filter(canopy, size=int(round(TREE_REACH_M / abs(tr.a))) | 1, mode="nearest")
    with rasterio.open(d / "zones_10m.tif") as ds:
        zone = ds.read(1)
    with rasterio.open(d / "sx_300m.tif") as ds:
        sx = ds.read().astype(float) / 100.0
    inside = zone > 0
    low, high = float(np.nanmin(elev[inside])), float(np.nanmax(elev[inside]))
    to_xy = Transformer.from_crs(4326, crs, always_xy=True)
    by_zone = {name: z.name for z in resort.zones for name in z.lifts}
    out = []
    for f in geo["features"]:
        name, kind = f["properties"].get("name"), f["properties"].get("kind")
        if not name or kind == "magic_carpet":
            continue
        xs, ys = to_xy.transform(*np.array(f["geometry"]["coordinates"]).T[:2])
        dist = np.r_[0.0, np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))]
        s = np.arange(0.0, dist[-1], 10.0)
        px, py = np.interp(s, dist, xs), np.interp(s, dist, ys)
        col, row = np.floor((px - tr.c) / tr.a).astype(int), np.floor((py - tr.f) / tr.e).astype(int)
        ok = (row >= 0) & (row < elev.shape[0]) & (col >= 0) & (col < elev.shape[1])
        row, col = row[ok], col[ok]
        z = elev[row, col]
        if len(z) < 3 or not np.isfinite(z).any():
            continue
        upper = z >= np.nanmedian(z)
        top, bottom = (0, -1) if z[0] > z[-1] else (-1, 0)
        bearing = float(np.degrees(np.arctan2(px[ok][top] - px[ok][bottom], py[ok][top] - py[ok][bottom])) % 360)
        open_ground = 1.0 - canopy[row[upper], col[upper]]
        unsheltered = (exposure.exposure_score(np.nan_to_num(sx[:, row[upper], col[upper]])) + 1.0) / 2.0      # 0 sheltered ... 1 exposed
        shelter = SHELTER_FLOOR + (1.0 - SHELTER_FLOOR) * (unsheltered * open_ground[None, :]).mean(axis=1)
        z_upper = float(np.nanmean(z[upper]))
        out.append({"name": name, "kind": kind, "zone": by_zone.get(name), "top_ft": round(float(np.nanmax(z)) * 3.28084, -1), "base_ft": round(float(np.nanmin(z)) * 3.28084, -1),
                    "bearing": round(bearing), "length_m": round(float(dist[-1])),
                    "height": round(HEIGHT_FLOOR + (1.0 - HEIGHT_FLOOR) * float(np.clip((z_upper - low) / (high - low), 0, 1)), 3),
                    "open_share": round(float(open_ground.mean()), 2), "shelter": [round(float(v), 3) for v in shelter]})
    return out


def lift_share(lift: dict, wind_from: float) -> float:
    """Share of the ridge gust felt on the lift for a wind direction (step 2, all assumptions)."""
    cross = abs(np.sin(np.radians(wind_from - lift["bearing"])))
    return float(lift["height"] * lift["shelter"][exposure.nearest_direction(wind_from)] * (ALONG_LINE + (1.0 - ALONG_LINE) * cross))


def thresholds(resort: Resort, kind: str) -> tuple[float, float]:
    cfg = resort.lift_wind or {}
    key = "surface" if kind in SURFACE else kind
    possible, likely = cfg.get(key, cfg.get("chair_lift", [35, 45]))
    return float(possible), float(likely)


# ---------------------------------------------------------------------------
# Step 3: the forecast
# ---------------------------------------------------------------------------

def word(chance_possible: float, chance_likely: float) -> str:
    """low, possible or likely: a hold is `likely` when the gust more likely than not reaches the
    upper threshold, `possible` when there is a real chance it reaches the lower one."""
    if chance_likely >= LIKELY:
        return "likely"
    return "possible" if chance_possible >= POSSIBLE or chance_likely >= POSSIBLE else "low"


def forecast(resort: Resort, run: pd.Timestamp, gfs_day3: dict | None = None) -> dict:
    """Wind-hold risk for every lift during lift hours on day 1 and day 2 of a 12z run."""
    final = json.loads(paths()[2].read_text())
    target = next(s for s in resort.snotel if s.get("target"))
    r0, c0 = hrrr.window_cell(target["lat"], target["lon"])
    ter = hrrr.terrain()
    win = ter[r0 - 2: r0 + 3, c0 - 2: c0 + 3]                         # the model's own ridge: its highest cell near the mountain
    i, j = np.unravel_index(int(np.argmax(win)), win.shape)
    gust.fetch(run)
    s = run_series(run, r0 - 2 + int(i), c0 - 2 + int(j))
    if s is None:
        return {"available": False, "days": [], "lifts": []}
    s["ridge"] = s["gust"]
    from ..model.zones import ridge_direction                       # the wind at ridge level is backed from the 600 mb direction
    s["dir600"] = [ridge_direction(v) for v in s["dir600"]]
    lifts = lift_table(resort)
    rq = final["ratio_quantiles"]
    days = []
    for day in (1, 2):
        part = s[s["h"].isin(open_hours(resort, day))]
        if len(part) < 4:
            continue
        peak = part.loc[part["ridge"].idxmax()]
        days.append({"day": day, "date": (run + pd.Timedelta(days=day - 1)).isoformat(), "ridge_gust_mph": round(float(peak["ridge"]) * MPH),
                     "ridge_gust_range_mph": [round(float(peak["ridge"]) * MPH * rq[3]), round(float(peak["ridge"]) * MPH * rq[35])],
                     "wind_from_deg": round(float(peak["dir600"])), "peak_hour": int(peak["h"]),
                     "hourly_mph": [round(float(v) * MPH) for v in part["ridge"]], "hours": [int(h) for h in part["h"]]})
    rows = []
    for lift in lifts:
        possible, likely = thresholds(resort, lift["kind"])
        row = {"name": lift["name"], "kind": lift["kind"], "zone": lift["zone"], "top_ft": lift["top_ft"], "days": []}
        for d in days:
            part = s[s["h"].isin(d["hours"])]
            felt = part["ridge"].to_numpy() * np.array([lift_share(lift, w) for w in part["dir600"]])
            k = int(np.argmax(felt))
            p1, p2 = chance_at_least(felt[k], possible / MPH, rq), chance_at_least(felt[k], likely / MPH, rq)
            row["days"].append({"day": d["day"], "gust_mph": round(float(felt[k]) * MPH), "risk": word(p1, p2), "chance_pct": round(100 * p1)})
        rows.append(row)
    order = {"likely": 0, "possible": 1, "low": 2}
    rows.sort(key=lambda x: (min((order[d["risk"]] for d in x["days"]), default=3), -max((d["gust_mph"] for d in x["days"]), default=0)))
    return {"available": True, "days": [{k: v for k, v in d.items() if k != "hours"} for d in days], "lifts": rows,
            "thresholds_mph": {k: v for k, v in (resort.lift_wind or {}).items() if k != "open_hours_mst"}}
