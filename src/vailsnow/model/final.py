"""Models fitted on every finished season, for the live forecast. (Scores always come from
`hindcast.py`, where each season is predicted by models that never saw it.)"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd

from ..resort import Resort, data_dir, load_resort
from . import precip, slr, zones
from .downscale import HRRR_FLOOR_IN
from .hindcast import LAST_SEASON, PRIMARY

TIERS = (24, 48, 72)                 # day 1, day 2, day 3 of a 12z run
EVENING_TIERS = (12, 36)             # a 00z run: tonight (5 pm to 5 am) and tomorrow (5 am to 5 am)
ANALOG_FEATURES = ("u600", "v600", "t700", "rh700", "pwat", "qpf_3x3")


DRY_IN = 0.005               # a forecast input below this counts as dry


def all_dry(tier: int, row) -> bool:
    """True when every precipitation input for the window is dry: HRRR at and around the gauge, GFS and the ensemble mean."""
    def dry(name: str, limit: float = DRY_IN) -> bool:
        v = row.get(name, np.nan)
        return not np.isfinite(v) or v < limit
    near = True if tier == 72 else (np.isfinite(row.get("qpf_3x3", np.nan)) and row["qpf_3x3"] < 0.002 and dry("qpf_max5"))
    return bool(near and dry("gfs_qpf") and dry("gefs_mean"))


def dry_record(tier: int) -> dict | None:
    """What the gauge did on held-out days when every model was dry: the 75th and 90th percentile and the mean.
    The fitted quantiles run high on these days (the stated 90th percentile was exceeded half as often as
    it should be), so the live forecast caps them at what actually happened."""
    path = data_dir() / "model" / f"hindcast_tier{tier}.parquet"
    if not path.exists():
        return None
    h = pd.read_parquet(path).dropna(subset=["obs"])
    sel = h[[all_dry(tier, r) for _, r in h.iterrows()]]
    if len(sel) < 100:
        return None
    return {"n": int(len(sel)), "q75": float(sel["obs"].quantile(0.75)), "q90": float(sel["obs"].quantile(0.9)), "mean": float(sel["obs"].mean()),
            "wet_share": float((sel["obs"] >= 0.095).mean())}


def final_dir():
    d = data_dir() / "model" / "final"
    d.mkdir(parents=True, exist_ok=True)
    return d


def train(resort: Resort | None = None) -> dict:
    resort = resort or load_resort()
    out = final_dir()
    table = pd.read_parquet(data_dir() / "model" / "table.parquet")
    table = table[table["season"] <= LAST_SEASON]
    scores = json.loads((data_dir() / "model" / "scores_precip.json").read_text())[PRIMARY]
    report = {}
    for tier in TIERS + EVENING_TIERS:
        t = precip.usable(table, tier)
        cols = precip.features_for(tier, t, PRIMARY)
        models = precip.fit(t, cols)
        models["stretch"] = {float(k): v for k, v in (scores[str(tier)].get("stretch") or {}).items()}
        models["dry"] = dry_record(tier)
        joblib.dump(models, out / f"precip_{tier}.joblib")
        report[tier] = {"rows": int(len(t)), "inputs": cols, "dry_days": models["dry"]}
    joblib.dump(slr.fit(slr.cases(resort, table)), out / "slr.joblib")
    # HRRR's own average pattern around the gauge, for the day's departure from it
    starts, fields = zones.day_fields(resort)
    use = fields[:, zones.REACH, zones.REACH] >= HRRR_FLOOR_IN
    np.save(out / "hrrr_average_pattern.npy", fields[use].sum(axis=0) / fields[use, zones.REACH, zones.REACH].sum())
    # past days to compare a new forecast with
    t24 = table[(table["tier"] == 24) & table["obs"].notna() & table["u600"].notna()]
    keep = ["start", "obs", "regime", "dir600", "spd600", *ANALOG_FEATURES]
    lib = t24[keep].copy()
    valley = pd.read_parquet(data_dir() / "obs" / "valley_daily.parquet")
    town_site = (resort.view or {}).get("town_site") or (resort.valley[0]["id"] if resort.valley else None)
    town = valley[(valley["site"] == town_site) & ~valley["multi_day"]].set_index("date")["snow"]
    lib["town_snow_in"] = town.reindex(lib["start"].dt.normalize() + pd.Timedelta(days=1)).to_numpy()
    lib.to_parquet(out / "analog_library.parquet")
    (out / "report.json").write_text(json.dumps(report, indent=1))
    return report


def load(tier: int) -> dict:
    return joblib.load(final_dir() / f"precip_{tier}.joblib")


def predict(tier: int, row: pd.DataFrame) -> dict[str, float]:
    """Mean and stretched quantiles for one window from the final model."""
    m = load(tier)
    p = precip.predict(m, row).iloc[0]
    out = {"mean": float(p["mean"]), "q10": float(p["q10"]), "q25": float(p["q25"]), "q50": float(p["q50"])}
    for lv, name in ((0.75, "q75"), (0.9, "q90")):
        out[name] = float(p["q50"] + m["stretch"].get(lv, 1.0) * (p[name] - p["q50"]))
    out["q90"] = max(out["q90"], out["q75"])
    dry = m.get("dry")
    if dry and all_dry(tier, row.iloc[0]):                                # every model dry: no wetter than such days have been
        out.update({k: min(out[k], dry[k]) for k in ("q75", "q90", "mean")})
        out.update({k: min(out[k], out["q75"]) for k in ("q10", "q25", "q50")})
    out["mean"] = float(np.clip(out["mean"], out["q10"], out["q90"]))      # the headline number stays inside its own range
    return out


def analogs(row: pd.Series, k: int = 3, min_obs: float = 0.0, not_near: pd.Timestamp | None = None) -> list[dict]:
    """The past winter forecast days most like this one in wind, temperature, moisture and forecast
    amount. Days within a week of `not_near` are left out, so a replayed forecast cannot match itself."""
    lib = pd.read_parquet(final_dir() / "analog_library.parquet").dropna(subset=list(ANALOG_FEATURES))
    lib = lib[lib["start"].dt.month.isin(precip.WINTER)]
    if not_near is not None:
        lib = lib[(lib["start"] - not_near).abs() > pd.Timedelta(days=7)]
    x = lib[list(ANALOG_FEATURES)].to_numpy(float)
    mu, sd = x.mean(axis=0), x.std(axis=0)
    q = (np.array([row[c] for c in ANALOG_FEATURES], float) - mu) / sd
    d = np.sqrt((((x - mu) / sd - q) ** 2).sum(axis=1))
    lib = lib.assign(distance=d)
    lib = lib[lib["obs"] >= min_obs].sort_values("distance")
    picked, out = [], []
    for _, r in lib.iterrows():                      # skip neighbours from the same storm
        if all(abs((r["start"] - p).days) > 3 for p in picked):
            picked.append(r["start"])
            out.append({"date": r["start"].date().isoformat(), "water_in": round(float(r["obs"]), 2),
                        "town_snow_in": None if pd.isna(r["town_snow_in"]) else round(float(r["town_snow_in"]), 1),
                        "wind_from_deg": round(float(r["dir600"])), "regime": r["regime"], "distance": round(float(r["distance"]), 2), "t700": float(r["t700"])})
        if len(out) == k:
            break
    return out
