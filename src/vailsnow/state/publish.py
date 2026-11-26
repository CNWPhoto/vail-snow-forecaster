"""Write the mountain state as files a browser game can read.

    grid.json                      the shared grid (written once, with the terrain texture)
    terrain.webp                   what never changes: terrain class, groomed share, zone
    state/latest.json              the newest hour: weather now, zone summary, conditions line
    state/YYYY/MM/DD/HH/           each hour's state.json and its three textures, kept for a few days
    state/index.json               which hours are on the site
    state/tomorrow.json            the same for tomorrow morning, from the median forecast
    days.json, days/<id>/          notable past days rebuilt from the record

`latest.json` is the only file that is rewritten in place. It names the textures of its own hour
inside the hourly folders, which are never rewritten, so a game that reads `latest.json` and
then the textures can never end up with images from two different hours.

Textures are 8-bit RGB with no alpha channel (browsers multiply colour by alpha on load, which
would corrupt the data), saved as lossless WebP: every pixel decodes to exactly the value written,
at about two thirds the size of PNG. Each channel's scale is in `ENCODING` and is repeated in every
JSON file. Shares and the wind effect are stored in coarse steps (16 and 15 levels), and fresh depth in steps of 0.4 in: finer steps
would say more than the model knows and double the download.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from ..resort import Resort, state_dir
from . import engine, grid

MPH_PER_MS = 2.236936
FRESH_MAX_IN = 51.0           # 0.2 in per channel value, stored in steps of two (0.4 in)
DENSITY_MAX = 408.0           # 1.6 kg per cubic metre per step, stored in steps of four
WIND_STEP, WIND_SPAN = 16, 112  # the wind effect is stored as 128 + 16 * (a whole number from -7 to 7)
EXT = "webp"
ENCODING = {
    "note": "Lossless WebP, 8-bit RGB, no alpha. value = channel / 255 * max unless stated. Load with no colour-space conversion and no vertical flip. "
            "Every texture covers the whole grid box; the history texture has half as many pixels each way, so sample all of them by u, v and not by pixel.",
    "surface": {"r": {"field": "fresh_in", "max": FRESH_MAX_IN, "what": "depth of the fresh layer, inches"},
                "g": {"field": "tracked_open", "max": 1.0, "what": "share of that layer already skied, on open ground (0 untouched, 1 fully tracked)"},
                "b": {"field": "tracked_trees", "max": 1.0, "what": "the same under trees; pick g or b with the game's own canopy mask"}},
    "character": {"r": {"field": "density_kg_m3", "max": DENSITY_MAX, "what": "density of the fresh layer; 0 where there is none"},
                  "g": {"field": "wind", "what": "(channel - 128) / 112: -1 scoured, 0 untouched, +1 loaded to twice what fell"},
                  "b": {"field": "crust", "what": "0 none, 128 sun crust, 255 melt-freeze crust"}},
    "history": {"r": {"field": "new_24h_in", "max": 51.0, "what": "new snow in the last 24 hours, inches"},
                "g": {"field": "new_72h_in", "max": 102.0, "what": "last 72 hours"},
                "b": {"field": "new_7d_in", "max": 153.0, "what": "last 7 days"}},
    "terrain": {"r": {"field": "class", "what": "terrain class index * 40 (see grid.json classes)"},
                "g": {"field": "groomed", "max": 1.0, "what": "share of the cell's open ground groomed nightly (assumed: green and blue runs)"},
                "b": {"field": "zone", "what": "zone index * 10 (see grid.json zones; 0 = no zone)"}},
}
COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
DISCLAIMER = "A modelled estimate built from public weather data. Not an official snow report, not avalanche advice, not affiliated with Vail Resorts."


def _u8(a: np.ndarray, top: float, step: int = 1) -> np.ndarray:
    """0 to `top` as 0 to 255, optionally in steps of `step` channel values."""
    v = np.clip(np.nan_to_num(a, nan=0.0) / top * 255.0, 0, 255)
    return (np.round(v / step) * step).astype(np.uint8)


def _image(path: Path, r: np.ndarray, g: np.ndarray, b: np.ndarray) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([r, g, b]), "RGB").save(path, "WEBP", lossless=True, quality=100, method=6)
    return path.stat().st_size


def textures(s: engine.State, folder: Path, suffix: str = "") -> dict[str, int]:
    """The three state textures for one hour. Returns their sizes in bytes."""
    t_open, t_trees = engine.tracked(s)
    crust = np.where(s.crust == engine.CRUST_SUN, 128, np.where(s.crust == engine.CRUST_MELT, 255, 0)).astype(np.uint8)
    coarse = s.history.shape[1:]                      # the totals are kept on a coarser grid; the texture is stored at that size
    wind = (128 + WIND_STEP * np.round(engine.wind_effect(s) * (WIND_SPAN / WIND_STEP))).astype(np.uint8)
    return {
        "surface": _image(folder / f"surface{suffix}.{EXT}", _u8(s.fresh, FRESH_MAX_IN, 2), _u8(t_open, 1.0, 17), _u8(t_trees, 1.0, 17)),
        "character": _image(folder / f"character{suffix}.{EXT}", _u8(engine.density(s), DENSITY_MAX, 4), wind, crust),
        "history": _image(folder / f"history{suffix}.{EXT}", *(_u8(engine.new_snow(s, h, coarse), top) for h, top in ((24, 51.0), (72, 102.0), (168, 153.0)))),
    }


def static_files(resort: Resort, layers: dict, out: Path | None = None) -> dict:
    """grid.json and the terrain texture: written whenever the static layers are rebuilt."""
    out = out or state_dir()
    out.mkdir(parents=True, exist_ok=True)
    g = grid.grid_json(resort)
    g["encoding"] = ENCODING
    g["terrain"] = f"terrain.{EXT}"
    (out / "grid.json").write_text(json.dumps(g, indent=1))
    size = _image(out / f"terrain.{EXT}", (layers["class"] * 40).astype(np.uint8), _u8(layers["groomed"], 1.0), (layers["zone"] * 10).astype(np.uint8))
    return {"grid": str(out / "grid.json"), "terrain_bytes": size}


# ---------------------------------------------------------------------------
# Words and numbers
# ---------------------------------------------------------------------------

def compass(deg: float) -> str:
    return COMPASS[int((deg % 360.0) / 22.5 + 0.5) % 16]


def intensity(rate_in_hr: float, classes: dict) -> str:
    name = "none"
    for k, lo in sorted(classes.items(), key=lambda kv: kv[1]):
        if rate_in_hr >= lo:
            name = k
    return name


def _f(c: float | None) -> int | None:
    return None if c is None or not np.isfinite(c) else int(round(c * 1.8 + 32))


def _mph(ms: float | None) -> int | None:
    return None if ms is None or not np.isfinite(ms) else int(round(ms * MPH_PER_MS))


def _iso(t: pd.Timestamp) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age(generated: pd.Timestamp, since) -> int | None:
    return None if since is None else max(0, int(round((generated - pd.Timestamp(since)) / pd.Timedelta(minutes=1))))


def weather_block(now_info: dict, next_info: dict | None, resort: Resort, ground: engine.Ground, generated: pd.Timestamp,
                  gauge_last: pd.Timestamp | None, forecast: bool = False) -> dict:
    """Current conditions for the game's sky, particles and fog, with a next-hour value beside each
    so it can blend instead of jumping. `stale_min` is how old the information behind a value is:
    since the sensor's last reading for measured values, since the model run was issued for the rest."""
    cfg = resort.state
    tz = cfg["operations"]["timezone"]
    nx = next_info or now_info
    run, lead = now_info.get("model_run"), now_info.get("model_lead_h")
    model = f"HRRR run of {pd.Timestamp(run):%H:%M} UTC, {lead} h ahead" if run is not None else None
    model_age = _age(generated, run)
    gauge_age = None if forecast else _age(generated, gauge_last)
    measured = now_info["temp_measured"] and not forecast

    def wind(info: dict, where: str) -> dict:
        w = info[where]
        return {"speed_mph": _mph(w["speed_ms"]), "gust_mph": _mph(max(w["gust_ms"], w["speed_ms"]) if w["gust_ms"] is not None and w["speed_ms"] is not None else w["gust_ms"]),
                "from_deg": None if w["from_deg"] is None else int(round(w["from_deg"])), "from": None if w["from_deg"] is None else compass(w["from_deg"])}

    local = pd.Timestamp(now_info["time"], tz="UTC").tz_convert(tz)
    rise, sset = engine.sun_times(local.tz_localize(None).normalize(), ground.lat, ground.lon)
    to_local = lambda t: pd.Timestamp(t, tz="UTC").tz_convert(tz).isoformat(timespec="minutes")
    vis = lambda info: None if info.get("vis_m") is None else round(min(info["vis_m"] / 1609.344, 30.0), 1)
    cloud = lambda info: None if info.get("cloud") is None else int(round(100 * info["cloud"]))
    water_source = now_info["sources"]["water"]
    return {
        "valid": _iso(now_info["time"]), "next_valid": _iso(nx["time"]), "generated": _iso(generated), "forecast": forecast,
        "snowfall": {"rate_in_hr": round(now_info["snow_in_hr"], 2), "class": intensity(now_info["snow_in_hr"], cfg["intensity"]),
                     "next_rate_in_hr": round(nx["snow_in_hr"], 2), "next_class": intensity(nx["snow_in_hr"], cfg["intensity"]),
                     "rain_in_hr": round(now_info["rain_in_hr"], 2), "where": "mid-mountain", "source": water_source,
                     "stale_min": model_age if "gauge" not in water_source else gauge_age},
        "wind": {"ridge": {**wind(now_info, "ridge"), "next": wind(nx, "ridge")}, "mid": {**wind(now_info, "mid"), "next": wind(nx, "mid")},
                 "source": model or now_info["sources"]["wind"], "stale_min": model_age,
                 "note": "Ridge wind is the model's free-air wind corrected to ridge level; no on-mountain wind sensor is public."},
        "temp_f": {"mid": _f(now_info["temp_c"]["mid"]), "ridge": _f(now_info["temp_c"]["ridge"]), "base": _f(now_info["temp_c"]["base"]),
                   "next": {k: _f(nx["temp_c"][k]) for k in ("mid", "ridge", "base")},
                   "source": now_info["sources"]["temp"] if not forecast else (model or "forecast"), "stale_min": gauge_age if measured else model_age},
        "cloud_pct": {"now": cloud(now_info), "next": cloud(nx), "source": model, "stale_min": model_age},
        "visibility_mi": {"now": vis(now_info), "next": vis(nx), "source": model, "stale_min": model_age},
        "sun": {"sunrise": to_local(rise), "sunset": to_local(sset), "timezone": tz},
    }


def zone_summary(s: engine.State, ground: engine.Ground, resort: Resort, lift: engine.Lifts) -> list[dict]:
    L = ground.layers
    share = L["tree_share"]
    t_open, t_trees = engine.tracked(s)
    dens = engine.density(s)
    n24, n72, n7 = (engine.new_snow(s, h) for h in (24, 72, 168))
    wind = engine.wind_effect(s)
    out = []
    for z in resort.zones:
        sel = L["zone"] == z.index
        if not sel.any():
            continue
        w_open, w_trees = (1.0 - share[sel]), share[sel]
        mean = lambda a: float(np.mean(a[sel]))
        has = np.isfinite(dens[sel])
        out.append({
            "id": z.id, "name": z.name, "group": z.group, "open": bool(ground.opens[sel].min() <= lift.day),
            "fresh_untracked_in": round(float(np.mean(w_open * s.open[sel] + w_trees * s.trees[sel])), 1),
            "fresh_layer_in": round(mean(s.fresh), 1),
            "untracked_open_in": round(float((w_open * s.open[sel]).sum() / max(w_open.sum(), 1e-6)), 1),
            "untracked_trees_in": round(float((w_trees * s.trees[sel]).sum() / max(w_trees.sum(), 1e-6)), 1),
            "best_stash_in": round(float(np.quantile(np.maximum(s.open[sel], s.trees[sel]), 0.95)), 1),
            "tracked_open": round(float((w_open * t_open[sel]).sum() / max(w_open.sum(), 1e-6)), 2),
            "tracked_trees": round(float((w_trees * t_trees[sel]).sum() / max(w_trees.sum(), 1e-6)), 2),
            "density_kg_m3": int(round(float(np.mean(dens[sel][has])))) if has.any() else None,
            "new_24h_in": round(mean(n24), 1), "new_72h_in": round(mean(n72), 1), "new_7d_in": round(mean(n7), 1),
            "hours_since_snow": int(min(np.median(s.hours_since[sel]), 999)),
            "wind": round(mean(wind), 2), "crust_share": round(float((s.crust[sel] > 0).mean()), 2)})
    return out


def density_word(kg_m3: float | None) -> str:
    if kg_m3 is None:
        return ""
    return "blower" if kg_m3 < 60 else "light" if kg_m3 < 85 else "medium-weight" if kg_m3 < 120 else "dense" if kg_m3 < 170 else "heavy"


def conditions_line(zones: list[dict], weather: dict, forecast: bool = False) -> str:
    """One or two plain sentences on what the mountain is like, built only from the numbers."""
    live = [z for z in zones if z["open"]] or zones
    n24 = float(np.median([z["new_24h_in"] for z in zones]))
    n72 = float(np.median([z["new_72h_in"] for z in zones]))
    hours = int(np.median([z["hours_since_snow"] for z in zones]))
    dens = [z["density_kg_m3"] for z in zones if z["density_kg_m3"] is not None]
    word = density_word(float(np.median(dens))) if dens else ""
    rate, cls = weather["snowfall"]["rate_in_hr"], weather["snowfall"]["class"]
    half = lambda v: f"{round(v * 2) / 2:g}"
    parts = []
    if cls in ("moderate", "heavy", "dumping"):
        lead = {"moderate": "Snowing steadily", "heavy": "Snowing hard", "dumping": "Dumping"}[cls]
        parts.append(f"{lead}: about {half(rate)} in an hour at mid-mountain, {half(n24)} in new in the last 24 hours" + (f", {word}" if word else "") + ".")
    elif cls in ("flurries", "light"):
        parts.append(f"Light snow falling, {half(n24)} in new in the last 24 hours." if n24 >= 0.5 else "Light snow falling, nothing much adding up yet.")
    elif n24 >= 1.0:
        parts.append(f"{half(n24)} in of new snow in the last 24 hours" + (f", {word}" if word else "") + ", and it has stopped.")
    elif n24 >= 0.3:
        parts.append("Under an inch of new snow in the last 24 hours" + (f"; {half(n72)} in over the last three days." if n72 >= 2.0 else "."))
    elif n72 >= 2.0:
        parts.append(f"No new snow in the last 24 hours; {half(n72)} in fell in the last three days.")
    elif hours >= 999:
        parts.append("No recent snowfall on record.")
    else:
        days = hours / 24.0
        parts.append(f"No new snow for {days:.0f} days." if days >= 2 else "No new snow in the last day.")
    best = max(live, key=lambda z: z["untracked_trees_in"])
    open_left = float(np.median([z["untracked_open_in"] for z in live]))
    if open_left >= 3.0:
        parts.append(f"Untracked snow is still easy to find in the open, about {half(open_left)} in.")
    elif best["untracked_trees_in"] >= 1.5:
        parts.append(f"The open runs are skied off; the best leftovers are in the trees in {best['name']}, about {half(best['untracked_trees_in'])} in.")
    else:
        parts.append("Fresh snow is used up: groomed corduroy and packed snow everywhere.")
    ridge = weather["wind"]["ridge"]
    if ridge["speed_mph"] is not None and (ridge["speed_mph"] >= 25 or (ridge["gust_mph"] or 0) >= 40):
        parts.append(f"Windy on the ridges, {ridge['from']} {ridge['speed_mph']} mph.")
    crusted = float(np.mean([z["crust_share"] for z in zones]))
    if crusted >= 0.3 and n24 < 1.0:
        parts.append("A crust has formed on sun-exposed slopes.")
    return ("Tomorrow morning, by the median forecast: " if forecast else "") + " ".join(parts)


def state_json(s: engine.State, ground: engine.Ground, resort: Resort, weather: dict, kind: str, generated: pd.Timestamp,
               texture_paths: dict[str, str], label: str | None = None, extra: dict | None = None) -> dict:
    tz = resort.state["operations"]["timezone"]
    lift = engine.lifts(resort.state, s.time)
    zones = zone_summary(s, ground, resort, lift)
    out = {
        "schema": grid.SCHEMA, "resort": resort.id, "kind": kind, "label": label or {"live": "Live", "tomorrow": "Tomorrow"}.get(kind, kind),
        "time": _iso(s.time), "local": pd.Timestamp(s.time, tz="UTC").tz_convert(tz).isoformat(timespec="minutes"), "generated": _iso(generated),
        "grid": "grid.json", "textures": texture_paths, "encoding": {k: ENCODING[k] for k in ("note", "surface", "character", "history")},
        "weather": weather,
        "mountain": {"new_24h_in": round(float(np.median([z["new_24h_in"] for z in zones])), 1),
                     "new_72h_in": round(float(np.median([z["new_72h_in"] for z in zones])), 1),
                     "new_7d_in": round(float(np.median([z["new_7d_in"] for z in zones])), 1),
                     "hours_since_snow": int(np.median([z["hours_since_snow"] for z in zones])),
                     "lifts_turning": bool(lift.share > 0),
                     "last_groomed": None if s.last_groomed is None else _iso(s.last_groomed),
                     "coverage": "The game keeps a full base everywhere. Nothing here describes bare ground, thin cover or closed terrain; "
                                 "'open' on a zone only says whether skiers are tracking it out."},
        "zones": zones, "conditions": conditions_line(zones, weather, forecast=kind == "tomorrow"), "about": DISCLAIMER,
    }
    if extra:
        out.update(extra)
    return out


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    tmp.replace(path)


def hour_folder(t: pd.Timestamp) -> str:
    return f"state/{t:%Y/%m/%d/%H}"


def publish_hour(s: engine.State, ground: engine.Ground, resort: Resort, weather: dict, generated: pd.Timestamp, out: Path | None = None) -> dict:
    """Write one live hour into its own folder, point latest.json at it, and drop hours past the keep window."""
    out = out or state_dir()
    rel = hour_folder(s.time)
    sizes = textures(s, out / rel)
    doc = state_json(s, ground, resort, weather, "live", generated, {k: f"{rel}/{k}.{EXT}" for k in sizes})
    doc["bytes"] = sizes
    _write(out / rel / "state.json", doc)
    _write(out / "state" / "latest.json", doc)
    keep = pd.Timestamp(s.time) - pd.Timedelta(hours=float(resort.state["keep_hours"]))
    hours = []
    for p in sorted((out / "state").glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]/[0-9][0-9]")):
        t = pd.Timestamp("-".join(p.parts[-4:-1]) + f" {p.parts[-1]}:00")
        if t < keep or t > pd.Timestamp(s.time):
            shutil.rmtree(p)
        elif (p / "state.json").exists():
            hours.append({"time": _iso(t), "state": f"{hour_folder(t)}/state.json"})
    for p in sorted((out / "state").glob("[0-9]*/**"), reverse=True):                 # empty year, month and day folders
        if p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    _write(out / "state" / "index.json", {"schema": grid.SCHEMA, "latest": _iso(s.time), "hours": hours})
    return {"folder": rel, "bytes": sizes, "total_kb": round(sum(sizes.values()) / 1024), "conditions": doc["conditions"]}


def publish_tomorrow(s: engine.State, ground: engine.Ground, resort: Resort, weather: dict, generated: pd.Timestamp, out: Path | None = None) -> dict:
    out = out or state_dir()
    folder = out / "state" / "tomorrow"
    if folder.exists():
        shutil.rmtree(folder)
    suffix = f"_{s.time:%Y%m%d%H}_{generated:%d%H}"                                   # a new name each build, so a cached image is never stale
    sizes = textures(s, folder, suffix)
    doc = state_json(s, ground, resort, weather, "tomorrow", generated, {k: f"state/tomorrow/{k}{suffix}.{EXT}" for k in sizes})
    _write(out / "state" / "tomorrow.json", doc)
    return {"time": doc["time"], "conditions": doc["conditions"], "total_kb": round(sum(sizes.values()) / 1024)}


def publish_day(s: engine.State, ground: engine.Ground, resort: Resort, weather: dict, generated: pd.Timestamp, day_id: str, label: str,
                extra: dict | None = None, out: Path | None = None) -> dict:
    out = out or state_dir()
    rel = f"days/{day_id}"
    sizes = textures(s, out / rel)
    doc = state_json(s, ground, resort, weather, "day", generated, {k: f"{rel}/{k}.{EXT}" for k in sizes}, label=label, extra=extra)
    _write(out / rel / "state.json", doc)
    return doc
