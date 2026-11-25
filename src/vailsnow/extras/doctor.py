"""Check a resort file and say what is ready and what is missing, step by step.

Adding a mountain means supplying a file in `resorts/` with a boundary name, zones and a
station list, then running the build steps in order. This walks the same order and reports
where it stops, so a new resort can be brought up without reading the code.
"""
from __future__ import annotations

from ..fcst import hrrr
from ..resort import ROOT, Resort, data_dir, load_resort, web_data_dir


def check(resort_id: str | None = None) -> list[dict]:
    rows: list[dict] = []

    def add(step: str, item: str, ok: bool | None, note: str = "") -> None:
        rows.append({"step": step, "item": item, "ok": ok, "note": note})

    try:
        r: Resort = load_resort(resort_id)
    except Exception as e:
        add("config", "resort file loads", False, f"{type(e).__name__}: {e}")
        return rows
    add("config", "resort file loads", True, f"{r.name}: {len(r.zones)} zones, {len(r.snotel)} SNOTEL, {len(r.valley)} valley sites")
    targets = [s for s in r.snotel if s.get("target")]
    add("config", "exactly one SNOTEL site marked target", len(targets) == 1, ", ".join(s["name"] for s in targets) or "none marked")
    add("config", "every zone lists seed trails", all(z.trails for z in r.zones), ", ".join(z.id for z in r.zones if not z.trails))
    outside = []
    for s in list(r.snotel) + list(r.valley) + ([r.wind_check] if r.wind_check else []):
        try:
            hrrr.window_cell(s["lat"], s["lon"])
        except ValueError:
            outside.append(s.get("name", s.get("id", "?")))
    add("config", "all stations inside the stored HRRR window", not outside,
        ", ".join(outside) + (": widen ROWS and COLS in fcst/hrrr.py and refetch" if outside else ""))
    for key, why in (("view", "map centre and headline zone"), ("alerts", "powder-alert thresholds"), ("lift_wind", "lift-hold thresholds"), ("wind_check", "ridge station for the gust check")):
        add("config", f"[{key}] section", getattr(r, key) is not None, "" if getattr(r, key) is not None else f"optional: {why}; defaults are used")
    if r.view and r.view.get("headline_zone"):
        add("config", "headline zone exists", any(z.id == r.view["headline_zone"] for z in r.zones), r.view["headline_zone"])

    for key in ("heights", "canopy", "regional_dem", "osm"):
        p = r.source_path(key)
        add("terrain inputs", f"{key}: {r.sources[key]}", p.exists(), "" if p.exists() else f"not found at {p}")
    t = r.out_dir()
    for name in ("terrain_10m.tif", "sx_300m.tif", "zones_10m.tif", "zones.geojson", "lifts.geojson"):
        add("terrain build", name, (t / name).exists(), "" if (t / name).exists() else "run: vailsnow terrain")
    d = data_dir()
    for path, cmd in (("obs/snotel_daily.parquet", "catalog"), ("obs/valley_daily.parquet", "catalog"), ("obs/snotel_hourly_water.parquet", "table"),
                      ("model/table.parquet", "hrrr-fetch, baseline-fetch, table"), ("model/scores_precip.json", "hindcast"),
                      ("model/final/precip_24.joblib", "train"), ("model/final/precip_12.joblib", "train"), ("model/final/gust.json", "wind-check")):
        add("history and models", path, (d / path).exists(), "" if (d / path).exists() else f"run: vailsnow {cmd}")
    w = web_data_dir()
    add("live", "a forecast has been built", (w / "runs.json").exists(), "" if (w / "runs.json").exists() else "run: vailsnow forecast")
    add("live", "scorecard written", (w / "scorecard.json").exists(), "" if (w / "scorecard.json").exists() else "run: vailsnow score")
    return rows


def report(resort_id: str | None = None) -> str:
    rows = check(resort_id)
    lines, step = [], None
    for r in rows:
        if r["step"] != step:
            step = r["step"]
            lines.append(f"\n{step}")
        mark = "ok     " if r["ok"] else "MISSING" if r["ok"] is False else "       "
        lines.append(f"  {mark}  {r['item']}" + (f"   ({r['note']})" if r["note"] else ""))
    missing = sum(r["ok"] is False for r in rows)
    lines.append(f"\n{missing} item(s) missing." if missing else "\nEverything is in place.")
    return "\n".join(lines).lstrip("\n") + f"\n(data folder: {data_dir().relative_to(ROOT) if data_dir().is_relative_to(ROOT) else data_dir()})"
