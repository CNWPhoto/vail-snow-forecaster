"""Base depth: an estimate of snow on the ground across the mountain, right now.

Two public sources, and what each is trusted for:

- The SNOTEL depth sensor at the mid-mountain gauge reads to the inch, every hour. It is the
  only measurement on the mountain, and the estimate is made to agree with it there.
- SNODAS, NOAA's daily 1 km snow model, takes in stations and satellite and gives the shape:
  how depth varies from place to place. Its cells are a kilometre wide, so within a cell depth
  is adjusted for elevation using the model's own change of depth with height across the area.

What is not in it: the resort's own measured base (its site's terms rule out automated
reading), snowmaking, grooming, skier compaction, wind drifts and sun on south faces at the
scale of a run. It is the natural snowpack as a 1 km model sees it, tied to one sensor.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter

from ..extras import scorecard
from ..grids import snodas
from ..model import orographic as og
from ..obs import snotel
from ..resort import Resort, data_dir, load_resort, web_data_dir
from . import contours, grid

EDGES_IN = (1.0, 6.0, 12.0, 24.0, 36.0, 48.0, 60.0, 80.0)
LABELS = ("1-6", "6-12", "12-24", "24-36", "36-48", "48-60", "60-80", "80+")
SMOOTH_M = 1000.0             # the scale SNODAS resolves
MIN_MODEL_IN = 12.0           # below this at the gauge the model is tied to the sensor by a shift, not a ratio:
                              # the sensor reads to the inch, and an inch or two is a large ratio on a thin pack
SCALE_LIMITS = (0.5, 2.0)


def on_cells(field: np.ndarray, west: float, north: float, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """A SNODAS window read at points, with straight-line blending between cell centres."""
    rows, cols = (north - lat) / snodas.CELL - 0.5, (lon - west) / snodas.CELL - 0.5
    return og.sample(np.nan_to_num(field.astype(float)), np.asarray(rows, float).ravel(), np.asarray(cols, float).ravel()).reshape(np.shape(lat))


def tie_to_sensor(model_field: np.ndarray, model_at_gauge: float, measured: float) -> tuple[np.ndarray, dict]:
    """Make the model agree with the sensor at the gauge: by a ratio when the model has a pack
    there to scale, by a shift when it has next to none."""
    if not np.isfinite(measured):
        return model_field, {"method": "model only: no sensor reading", "factor": 1.0}
    if model_at_gauge >= MIN_MODEL_IN:
        k = float(np.clip(measured / model_at_gauge, *SCALE_LIMITS))
        return model_field * k, {"method": "scaled", "factor": round(k, 2)}
    return np.maximum(model_field + (measured - model_at_gauge), 0.0), {"method": "shifted", "factor": round(measured - model_at_gauge, 1)}


def elevation_detail(coarse: np.ndarray, elev: np.ndarray, inside: np.ndarray, cell_m: float) -> tuple[np.ndarray, float]:
    """Depth with elevation detail finer than the model's cells: the model's own change of depth
    with height across the ski area (inches per 1,000 ft), applied to how far each cell sits above
    or below its kilometre-wide surroundings. Returns the field and that rate."""
    size = int(round(SMOOTH_M / cell_m)) | 1
    filled = np.where(np.isfinite(elev), elev, np.nanmean(elev))
    smooth = uniform_filter(filled, size=size, mode="nearest")
    x, y = smooth[inside], coarse[inside]
    slope = float(np.polyfit(x, y, 1)[0]) if np.ptp(x) > 50 and y.max() > 0 else 0.0
    slope = max(slope, 0.0)                                       # deeper with height, or no adjustment at all
    return np.maximum(coarse + slope * (filled - smooth), 0.0), slope * 304.8


def sensors(resort: Resort, max_age_h: float = 1.0) -> list[dict]:
    """The newest depth reading at every SNOTEL site in the resort file."""
    out = []
    now = scorecard.now_utc()
    wy = now.year + (now.month >= 10)
    for s in resort.snotel:
        try:
            h = snotel.load_hourly(s["triplet"], first_wy=wy, max_age_h=max_age_h)["depth"].dropna()
        except Exception:
            continue
        if h.empty:
            continue
        recent = h[h.index >= h.index[-1] - pd.Timedelta(hours=5)]
        out.append({"id": s["id"], "name": s["name"], "elev_ft": s["elev_ft"], "lat": s["lat"], "lon": s["lon"], "target": bool(s.get("target")),
                    "depth_in": round(float(recent.median()), 1), "time": (h.index[-1] + pd.Timedelta(hours=8)).isoformat()})
    return out


def build(resort: Resort | None = None, write: bool = True) -> dict:
    resort = resort or load_resort()
    now = scorecard.now_utc()
    g = grid.static(resort)
    inside = g["zone"] > 0
    date, model = snodas.latest(resort, now)
    _, _, west, north = snodas.window(resort, date)
    obs = sensors(resort)
    gauge = next((s for s in obs if s["target"]), None)
    target = scorecard.target(resort)
    at_gauge = float(on_cells(model, west, north, np.array([target["lat"]]), np.array([target["lon"]]))[0])
    coarse, tie = tie_to_sensor(on_cells(model, west, north, g["lat"], g["lon"]), at_gauge, gauge["depth_in"] if gauge else np.nan)
    cell_m = float(abs(g["x"][0, 1] - g["x"][0, 0]))
    field, per_1000ft = elevation_detail(coarse, g["elev"], inside, cell_m)
    field = np.where(inside, field, np.nan)

    # the other sensors in the region were not used to make the estimate: what the same recipe gives there
    checks = []
    for s in obs:
        if s["target"]:
            continue
        try:
            raw = float(on_cells(model, west, north, np.array([s["lat"]]), np.array([s["lon"]]))[0])
        except Exception:
            continue
        est = raw * tie["factor"] if tie["method"] == "scaled" else max(raw + tie["factor"], 0.0) if tie["method"] == "shifted" else raw
        checks.append({"name": s["name"], "elev_ft": s["elev_ft"], "measured_in": s["depth_in"], "estimate_in": round(est, 1), "model_in": round(raw, 1)})

    cards = []
    for z in resort.zones:
        sel = (g["zone"] == z.index)
        if sel.any():
            v = field[sel]
            cards.append({"id": z.id, "name": z.name, "depth_in": [round(float(np.nanmean(v)), 1), round(float(np.nanquantile(v, 0.1)), 1), round(float(np.nanquantile(v, 0.9)), 1)]})
    out = {"resort": resort.name, "updated": now.isoformat(timespec="minutes"), "model_date": date.date().isoformat(),
           "gauge": gauge, "model_at_gauge_in": round(at_gauge, 1), "tie": tie, "inches_per_1000ft": round(per_1000ft, 1),
           "zones": cards, "sensors": obs, "checks": checks, "bins": {"edges_in": list(EDGES_IN), "labels": list(LABELS)},
           "range_in": [round(float(np.nanmin(field)), 1), round(float(np.nanmax(field)), 1)]}
    past = data_dir() / "model" / "scores_depth.json"
    if past.exists():
        out["history_check"] = json.loads(past.read_text())
    if write:
        base = web_data_dir() / "depth"
        base.mkdir(parents=True, exist_ok=True)
        (base / "base.geojson").write_text(json.dumps(contours.bands(field, g["x"][0], g["y"][:, 0], resort.epsg, EDGES_IN, LABELS), separators=(",", ":")))
        step = 2
        thin_inside = grid.block_mean(np.where(inside, 1.0, np.nan), step)
        keep = np.isfinite(thin_inside).ravel()
        point = {"rows": thin_inside.shape[0], "cols": thin_inside.shape[1], "fit": grid.lonlat_to_cell(g, 1), "step": step, "scale": 10,
                 "cells": [int(v) for v in np.nonzero(keep)[0]], "depth": [int(v) for v in np.round(grid.block_mean(field, step).ravel()[keep] * 10)]}
        (base / "grid.json").write_text(json.dumps(point, separators=(",", ":")))
        (web_data_dir() / "depth.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


def history_check(resort: Resort | None = None, limit: int | None = None) -> dict:
    """How close SNODAS depth has been to the SNOTEL sensors here, on every snapshot already on
    disk (the Phase 2 storm dates). SNODAS takes these very sensors in, so this flatters it: it
    shows the model is not far off where it is told the answer, not how good it is between them."""
    resort = resort or load_resort()
    daily = pd.read_parquet(data_dir() / "obs" / "snotel_daily.parquet")
    dates = sorted(pd.Timestamp(p.stem.split("_")[1]) for p in snodas.tar_path(pd.Timestamp("2020-01-01")).parent.glob("SNODAS_*.tar"))
    dates = [d for d in dates if d >= pd.Timestamp("2010-10-01")][-(limit or 10 ** 6):]
    rows = []
    for date in dates:
        try:
            model = snodas.read_depth(resort, date)
        except Exception:
            continue
        _, _, west, north = snodas.window(resort, date)
        day = daily[daily["date"] == date].set_index("site")["depth"]
        for s in resort.snotel:
            if s["id"] in day and np.isfinite(day[s["id"]]):
                try:
                    est = float(on_cells(model, west, north, np.array([s["lat"]]), np.array([s["lon"]]))[0])
                except Exception:
                    continue
                rows.append({"site": s["id"], "target": bool(s.get("target")), "date": date, "measured": float(day[s["id"]]), "model": est})
    t = pd.DataFrame(rows)
    t = t[t["measured"] >= 6]

    def block(x: pd.DataFrame) -> dict:
        return {"n": int(len(x)), "typical_miss_in": round(float((x["model"] - x["measured"]).abs().mean()), 1), "bias_in": round(float((x["model"] - x["measured"]).mean()), 1),
                "typical_miss_pct": round(100 * float(((x["model"] - x["measured"]).abs() / x["measured"]).mean()))}

    out = {"dates": int(t["date"].nunique()), "first": str(t["date"].min().date()), "last": str(t["date"].max().date()),
           "gauge": block(t[t["target"]]), "other_sensors": block(t[~t["target"]])}
    # tied to the gauge by ratio, how far off are the other sensors? This is the part that tests the map's recipe.
    k = t[t["target"]].set_index("date").eval("measured / model").clip(*SCALE_LIMITS)
    o = t[~t["target"]].join(k.rename("k"), on="date").dropna(subset=["k"])
    o = o.assign(model=o["model"] * o["k"])
    out["other_sensors_after_tying_to_gauge"] = block(o)
    (data_dir() / "model" / "scores_depth.json").write_text(json.dumps(out, indent=1))
    return out
