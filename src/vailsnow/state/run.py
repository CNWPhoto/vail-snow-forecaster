"""Run the mountain state: live every hour, forward to tomorrow morning, or back through a past day.

Live. The gauge's newest day or so is provisional: its checked daily total has not arrived yet.
So each run starts from a saved checkpoint (the state at the last hour with a checked gauge value),
steps forward hour by hour to the present with the best data now on hand, and saves a new
checkpoint when the checked record has moved on. Hours first run with a provisional amount are
therefore run again once the checked amount is in, and a late or skipped scheduled run changes
nothing: the next one covers the same hours. With no checkpoint (a first run, or a lost cache)
the state is spun up from ten days back.

Total depth is carried for the scorecard only. It is eased toward the mid-mountain depth sensor
every hour, slowly, so the model's own drift between readings is still visible when it is scored.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from ..model import settle
from ..resort import Resort, cache_dir, load_resort, state_dir
from . import drivers, engine, grid, publish, score

NUDGE_HOURS = 72.0            # total depth closes this share of its gap to the sensor each hour: 1 / NUDGE_HOURS
SCORE_HOUR_UTC = 12           # 5 am Mountain Standard Time: when the state is set beside the sensor
KEEP_MODEL_DAYS = 4           # model runs older than this are dropped from the state's own cache


def now_utc() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def checkpoint_path(resort: Resort) -> Path:
    return cache_dir() / "state_carry" / f"{resort.id}_checkpoint.npz"


def setup(resort: Resort) -> tuple[dict, engine.Ground]:
    layers = grid.shelter_gauge(grid.load(resort), grid.gauge_cell(resort))
    return layers, engine.ground(layers, resort.state, {z.index: z.group for z in resort.zones})


def base_pattern(resort: Resort, layers: dict, when: pd.Timestamp, sensor_in: float) -> np.ndarray:
    """A first guess at total depth everywhere, for a cold start: NOAA's 1 km snow model (SNODAS)
    for the shape, made to agree with the depth sensor at the gauge. If that model cannot be had,
    depth is taken to follow the long-term precipitation map and fall away below the gauge."""
    target = next(s for s in resort.snotel if s.get("target"))
    try:
        from ..grids import snodas
        from ..live import depth as live_depth
        date, model = snodas.latest(resort, when)
        _, _, west, north = snodas.window(resort, date)
        s = grid.spec(resort)
        x = s["west"] + (np.arange(s["width"]) + 0.5) * s["cell_m"]
        y = s["north"] - (np.arange(s["height"]) + 0.5) * s["cell_m"]
        lon, lat = Transformer.from_crs(resort.epsg, 4326, always_xy=True).transform(*np.meshgrid(x, y))
        at_gauge = float(live_depth.on_cells(model, west, north, np.array([target["lat"]]), np.array([target["lon"]]))[0])
        field, _ = live_depth.tie_to_sensor(live_depth.on_cells(model, west, north, lat, lon), at_gauge, sensor_in)
        return np.clip(np.nan_to_num(field, nan=0.0), 0.0, None).astype(np.float32)
    except Exception:
        gauge_elev = target["elev_ft"] / 3.280839895
        taper = np.clip(1.0 + (layers["elev"] - gauge_elev) / 600.0, 0.15, 1.6)
        return (max(sensor_in, 0.0) * layers["prism"] * taper).astype(np.float32)


def cold_start(resort: Resort, ground: engine.Ground, rec: dict, start: pd.Timestamp) -> engine.State:
    depth = rec["depth"].reindex(pd.date_range(start - pd.Timedelta(hours=12), start, freq="h")).dropna()
    sensor = float(depth.iloc[-1]) if len(depth) else 20.0
    swe = rec["swe"].get(start, np.nan)
    density = float(np.clip(swe / sensor, 0.15, 0.45)) if np.isfinite(swe) and sensor >= 6 else 0.28
    return engine.empty(start, ground.shape, ground.history_block, base_pattern(resort, ground.layers, start, sensor), density)


def nudge_depth(s: engine.State, rec: dict, gauge_rc: tuple[int, int]) -> dict | None:
    """Ease total depth toward the sensor at the gauge. Returns what the model had before the nudge."""
    obs = rec["depth"].get(s.time, np.nan)
    if not np.isfinite(obs):
        return None
    r, c = gauge_rc
    model = float(s.base[r, c] + s.fresh[r, c])
    if model > 1.0 and obs > 1.0:
        gain = 1.0 + (float(np.clip(obs / model, 0.5, 2.0)) - 1.0) / NUDGE_HOURS
        s.base *= np.float32(gain)
        s.base_w *= np.float32(gain)
    elif obs > 1.0:
        s.base += np.float32(obs / NUDGE_HOURS)
        s.base_w += np.float32(0.28 * obs / NUDGE_HOURS)
    return {"model_depth_in": round(model, 1), "sensor_depth_in": round(float(obs), 1)}


def advance(s: engine.State, until: pd.Timestamp, weather: drivers.Weather, ground: engine.Ground, config: dict, rates, rec: dict,
            each=None) -> tuple[engine.State, dict | None]:
    """Step to `until`. `each(state, info, depth_check)` is called after every hour. Returns the state and the last hour's numbers."""
    info = None
    while s.time < until:
        f, info = weather.forcing(s.time + pd.Timedelta(hours=1))
        s = engine.step(s, f, ground, config, rates)
        check = nudge_depth(s, rec, weather.gauge_rc)
        if each is not None:
            each(s, info, check)
    return s, info


def _score_line(s: engine.State, rec: dict, gauge_rc: tuple[int, int], check: dict | None) -> dict | None:
    """The state beside the sensor at 5 am: total depth, and new snow against the sensor's 24-hour rise."""
    if check is None:
        return None
    r, c = gauge_rc
    before = rec["depth"].get(s.time - pd.Timedelta(hours=24), np.nan)
    hours = pd.date_range(s.time - pd.Timedelta(hours=23), s.time, freq="h")
    water = rec["water"]["water_in"].reindex(hours)
    return {"time": publish._iso(s.time), **check,
            "model_new_24h_in": round(float(engine.new_snow(s, 24)[r, c]), 1),
            "model_fresh_layer_in": round(float(s.fresh[r, c]), 1),
            "sensor_change_24h_in": None if not np.isfinite(before) else round(check["sensor_depth_in"] - float(before), 1),
            "gauge_water_24h_in": None if water.isna().any() else round(float(water.sum()), 2)}


def prune_model_cache(now: pd.Timestamp) -> None:
    """On a scheduled runner (CI is set), drop weather-model files too old to be stepped through
    again, so the job's cache stays small. On this computer nothing is removed: the same folder
    holds the forecast's training archive."""
    if not os.environ.get("CI"):
        return
    for folder in ("hrrr_state", "hrrr_zarr"):
        for p in (cache_dir() / folder).glob("*/*.npz"):
            try:
                run = pd.Timestamp(f"{p.stem[:8]} {p.stem[9:11]}:00")
            except ValueError:
                continue
            if now - run > pd.Timedelta(days=KEEP_MODEL_DAYS):
                p.unlink()


def live(resort: Resort | None = None, now: pd.Timestamp | None = None, write: bool = True) -> dict:
    """Bring the state up to the present, publish it, and publish tomorrow morning's preview."""
    from ..extras import scorecard
    resort = resort or load_resort()
    cfg = resort.state
    now = pd.Timestamp(now) if now is not None else now_utc()
    hour = now.floor("h")
    layers, ground = setup(resort)
    rec = scorecard.gauge_record(resort, max_age_h=0.5)
    rates = settle.fitted_rates()
    model = drivers.Model(resort, now)
    weather = drivers.Weather(resort, layers, rec, model, now=hour, forecast=drivers.published_forecast())

    checked = rec["water"].index[~rec["water"]["provisional"]]
    final_until = min(checked[-1], hour) if len(checked) else None
    path = checkpoint_path(resort)
    started = "checkpoint"
    s = None
    if path.exists():
        s = engine.load(path)
        if s.shape != ground.shape or s.time > hour or hour - s.time > pd.Timedelta(days=float(cfg["spin_up_days"])):
            s = None
    if s is None:
        started = "cold start"
        s = cold_start(resort, ground, rec, hour - pd.Timedelta(days=float(cfg["spin_up_days"])))
        model.prefetch(s.time, hour)
    first = s.time
    scores: list[dict] = []
    saved = {"at": None}

    def each(state: engine.State, info: dict, check: dict | None) -> None:
        if final_until is not None and state.time <= final_until and state.time.hour == SCORE_HOUR_UTC:
            scores.append(_score_line(state, rec, weather.gauge_rc, check))
        if write and final_until is not None and state.time == final_until and state.time > first:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part.npz")
            engine.save(state, tmp)
            tmp.replace(path)
            saved["at"] = state.time

    s, info = advance(s, hour, weather, ground, cfg, rates, rec, each)
    if info is None:                                                    # the checkpoint was already at this hour
        _, info = weather.forcing(hour)
    _, next_info = weather.forcing(hour + pd.Timedelta(hours=1))
    block = publish.weather_block(info, next_info, resort, ground, now, rec["last"])
    result = {"time": str(hour), "started_from": f"{started} at {first}", "hours_stepped": int((hour - first) / pd.Timedelta(hours=1)),
              "checkpoint_saved": None if saved["at"] is None else str(saved["at"]), "checked_until": None if final_until is None else str(final_until)}
    if write:
        if not (state_dir() / "grid.json").exists():
            publish.static_files(resort, layers)
        result["latest"] = publish.publish_hour(s, ground, resort, block, now)
        result["scored"] = score.record(scores)

    # tomorrow morning at first chair, from the median forecast
    tz = cfg["operations"]["timezone"]
    local = pd.Timestamp(hour, tz="UTC").tz_convert(tz)
    opens = cfg["operations"]["hours"][0]
    target = (local.normalize() + pd.Timedelta(days=1) + pd.Timedelta(hours=opens)).tz_convert("UTC").tz_localize(None).ceil("h")
    try:
        ahead, ahead_info = advance(s.copy(), target, weather, ground, cfg, rates, rec)
        _, after = weather.forcing(target + pd.Timedelta(hours=1))
        ahead_block = publish.weather_block(ahead_info, after, resort, ground, now, rec["last"], forecast=True)
        if write:
            result["tomorrow"] = publish.publish_tomorrow(ahead, ground, resort, ahead_block, now)
    except Exception as e:                                              # the preview must never stop the live state
        result["tomorrow"] = f"not built: {type(e).__name__}: {e}"
    if write:
        prune_model_cache(now)
    return result


# ---------------------------------------------------------------------------
# Past days
# ---------------------------------------------------------------------------

def past(resort: Resort, when: pd.Timestamp, spin_up_days: float | None = None, fetch: bool = True) -> tuple[engine.State, engine.Ground, dict, dict, dict]:
    """The state at a past hour (UTC), rebuilt from the gauge record and the model runs issued at the time.
    Returns the state, the ground, that hour's numbers, the next hour's, and the gauge record."""
    when = pd.Timestamp(when).floor("h")
    cfg = resort.state
    start = when - pd.Timedelta(days=float(spin_up_days or cfg["spin_up_days"]))
    layers, ground = setup(resort)
    # the survival rules follow the calendar of the season in the resort file; a past day is run as if every zone were open
    ground.opens[:] = 0
    past_cfg = {**cfg, "operations": {**cfg["operations"], "season": [str((start - pd.Timedelta(days=30)).date()), str((when + pd.Timedelta(days=30)).date())],
                                      "extended_from": str((start - pd.Timedelta(days=30)).date()), "holidays": []}}
    rec = drivers.historic_record(resort, start, when)
    model = drivers.Model(resort, now_utc(), fetch=fetch)
    weather = drivers.Weather(resort, layers, rec, model, now=None, daily_flow=drivers.daily_flow())
    s = cold_start(resort, ground, rec, start)
    model.prefetch(start, when + pd.Timedelta(hours=1))
    s, info = advance(s, when, weather, ground, past_cfg, settle.fitted_rates(), rec)
    _, next_info = weather.forcing(when + pd.Timedelta(hours=1))
    return s, ground, info, next_info, rec


def days(resort: Resort | None = None, only: str | None = None, fetch: bool = True) -> list[dict]:
    """Rebuild the notable past days named in the resort file and write the manifest the game's day picker reads."""
    resort = resort or load_resort()
    out = state_dir()
    layers, _ = setup(resort)
    if not (out / "grid.json").exists():
        publish.static_files(resort, layers)
    now = now_utc()
    for day in resort.state.get("days", []):
        if only and day["id"] != only:
            continue
        when = pd.Timestamp(day["time"])
        s, ground, info, next_info, rec = past(resort, when, spin_up_days=day.get("spin_up_days"), fetch=fetch)
        block = publish.weather_block(info, next_info, resort, ground, now, None)
        for part in ("snowfall", "wind", "temp_f", "cloud_pct", "visibility_mi"):          # a rebuilt day is not "stale": the age of the data means nothing here
            block[part]["stale_min"] = None
        extra = {"day": {"id": day["id"], "kind": day["kind"], "note": day.get("note")}}
        publish.publish_day(s, ground, resort, block, now, day["id"], day["label"], extra)
    manifest = []
    for day in resort.state.get("days", []):
        path = out / "days" / day["id"] / "state.json"
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        manifest.append({"id": day["id"], "label": day["label"], "kind": day["kind"], "time": doc["time"], "local": doc["local"], "state": f"days/{day['id']}/state.json",
                         "new_24h_in": doc["mountain"]["new_24h_in"], "new_72h_in": doc["mountain"]["new_72h_in"],
                         "snowfall": doc["weather"]["snowfall"]["class"], "ridge_wind_mph": doc["weather"]["wind"]["ridge"]["speed_mph"],
                         "cloud_pct": doc["weather"]["cloud_pct"]["now"], "conditions": doc["conditions"], "note": day.get("note")})
    publish._write(out / "days.json", {"schema": grid.SCHEMA, "resort": resort.id, "generated": publish._iso(now), "about": publish.DISCLAIMER, "days": manifest})
    return manifest
