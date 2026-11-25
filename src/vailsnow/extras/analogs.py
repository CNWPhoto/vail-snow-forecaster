"""Analog finder: "this setup most resembles ...".

A forecast day is compared with every past winter forecast day since October 2018 on six
numbers: the wind aloft (two components), temperature and humidity near ridge level, water in
the column and the forecast amount. The closest days are named, with the storm each belonged to
in the Phase 2 catalogue and what the gauge measured.

How much to lean on them is checked the plain way: forecast each held-out day with the average
of its closest past days and compare the miss with the model's.
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import pandas as pd

from ..model import final, precip
from ..resort import data_dir


def storm_label(start: pd.Timestamp, end: pd.Timestamp) -> str:
    """'Feb 5-9, 2020', 'Jan 30 - Feb 2, 2014' or 'Feb 6, 2020'."""
    if start == end:
        return f"{start:%b} {start.day}, {start.year}"
    if start.month == end.month:
        return f"{start:%b} {start.day}-{end.day}, {end.year}"
    return f"{start:%b} {start.day} - {end:%b} {end.day}, {end.year}"


def storm_of(date: pd.Timestamp, events: pd.DataFrame | None = None) -> dict | None:
    """The catalogued storm a day belongs to, if any."""
    if events is None:
        path = data_dir() / "catalog" / "events.parquet"
        if not path.exists():
            return None
        events = pd.read_parquet(path)
    day = pd.Timestamp(date).normalize()
    hit = events[(pd.to_datetime(events["start"]) <= day) & (pd.to_datetime(events["end"]) >= day)]
    if hit.empty:
        return None
    e = hit.iloc[0]
    start, end = pd.Timestamp(e["start"]), pd.Timestamp(e["end"])
    water = e["prec_in"] if np.isfinite(e.get("prec_in", np.nan)) else e["swe_in"]
    out = {"label": storm_label(start, end), "start": start.date().isoformat(), "end": end.date().isoformat(), "days": int(e["days"]), "water_in": round(float(water), 2)}
    if np.isfinite(e.get("depth_gain_in", np.nan)):
        out["depth_gain_in"] = round(float(e["depth_gain_in"]), 1)
    return out


def usual_distances() -> tuple[float, float]:
    """How far a winter day's best match usually is: the median and the 85th percentile of the
    distance from each library day to its closest day more than a week away. A new forecast's
    best match is called close, fair or loose against these."""
    path = final.final_dir() / "analog_distances.json"
    if path.exists():
        d = json.loads(path.read_text())
        return d["median"], d["p85"]
    lib = pd.read_parquet(final.final_dir() / "analog_library.parquet").dropna(subset=list(final.ANALOG_FEATURES))
    lib = lib[lib["start"].dt.month.isin(precip.WINTER)]
    x = lib[list(final.ANALOG_FEATURES)].to_numpy(float)
    z = (x - x.mean(axis=0)) / x.std(axis=0)
    d = np.sqrt(((z[:, None, :] - z[None, :, :]) ** 2).sum(axis=2))
    t = lib["start"].to_numpy()
    d[np.abs(t[:, None] - t[None, :]) <= np.timedelta64(7, "D")] = np.inf
    nearest = d.min(axis=1)
    out = {"median": round(float(np.median(nearest)), 3), "p85": round(float(np.quantile(nearest, 0.85)), 3), "days": int(len(lib))}
    path.write_text(json.dumps(out))
    return out["median"], out["p85"]


def find(features: dict | pd.Series, k: int = 3, not_near: pd.Timestamp | None = None, min_obs: float = 0.0) -> list[dict]:
    """The k closest past days, each with its storm and how good a match it is."""
    out = final.analogs(pd.Series(features, dtype=float), k=k, min_obs=min_obs, not_near=not_near)
    path = data_dir() / "catalog" / "events.parquet"
    events = pd.read_parquet(path) if path.exists() else None
    median, p85 = usual_distances()
    extended_path = final.final_dir() / "extended.joblib"
    fit = joblib.load(extended_path).get("conversion") if extended_path.exists() else None
    for a in out:
        ratio = float(np.interp(a.pop("t700") - 273.15, fit["t700_c"], fit["ratio"])) if fit else 14.0
        a["snow_in"] = round(a["water_in"] * ratio, 1)             # the gauge's water at the usual ratio for that temperature aloft
        a["storm"] = None if events is None else storm_of(pd.Timestamp(a["date"]), events)
        a["match"] = "close" if a["distance"] <= median else "fair" if a["distance"] <= p85 else "loose"
    return out


def sentence(found: list[dict]) -> str | None:
    """One line for the top match, saying honestly how close it is."""
    if not found:
        return None
    a = found[0]
    day = pd.Timestamp(a["date"])
    name = f"{day:%b} {day.day}, {day.year}"
    lead = {"close": "This setup closely resembles", "fair": "This setup most resembles", "loose": "Nothing on record is close. The nearest day is"}[a["match"]]
    s = f"{lead} {name}"
    if a["storm"] and a["storm"]["days"] > 1:
        s += f", part of the {a['storm']['label']} storm ({a['storm']['water_in']:g} in of water over {a['storm']['days']} days)"
    return s + f". That day brought about {a['snow_in']:g} in of snow at mid-mountain ({a['water_in']:g} in of water at the gauge)."


def for_date(date: str, k: int = 5) -> dict:
    """Analogs for a past day's own forecast, looked up in the library (the day itself and its week are left out)."""
    lib = pd.read_parquet(final.final_dir() / "analog_library.parquet")
    day = pd.Timestamp(date).normalize()
    row = lib[lib["start"].dt.normalize() == day]
    if row.empty or row[list(final.ANALOG_FEATURES)].isna().any(axis=None):
        raise ValueError(f"no forecast features on file for {day.date()} (the library runs from October 2018)")
    r = row.iloc[0]
    found = find(r[list(final.ANALOG_FEATURES)], k=k, not_near=r["start"])
    return {"date": day.date().isoformat(), "measured_in": round(float(r["obs"]), 2), "storm": storm_of(day), "analogs": found, "resembles": sentence(found)}


def skill(k: int = 5) -> dict:
    """Forecast each winter day with the average of its k closest days from other seasons."""
    lib = pd.read_parquet(final.final_dir() / "analog_library.parquet").dropna(subset=list(final.ANALOG_FEATURES) + ["obs"])
    lib = lib[lib["start"].dt.month.isin(precip.WINTER)].reset_index(drop=True)
    season = (lib["start"].dt.year + (lib["start"].dt.month >= 10)).to_numpy()
    x = lib[list(final.ANALOG_FEATURES)].to_numpy(float)
    z = (x - x.mean(axis=0)) / x.std(axis=0)
    obs = lib["obs"].to_numpy(float)
    guess, spread = np.full(len(lib), np.nan), np.full(len(lib), np.nan)
    for s in np.unique(season):
        test, pool = season == s, season != s
        d = np.sqrt(((z[test][:, None, :] - z[pool][None, :, :]) ** 2).sum(axis=2))
        near = np.argsort(d, axis=1)[:, :k]
        guess[test], spread[test] = obs[pool][near].mean(axis=1), obs[pool][near].max(axis=1) - obs[pool][near].min(axis=1)
    out = {"k": k, "days": int(len(lib)), "analog_mean_mae": round(float(np.abs(guess - obs).mean()), 3), "always_average_mae": round(float(np.abs(obs.mean() - obs).mean()), 3)}
    h = pd.read_parquet(data_dir() / "model" / "hindcast_tier24.parquet").set_index("start")
    both = lib.assign(guess=guess).set_index("start")[["obs", "guess"]].join(h[["q50", "qpf_3x3"]], how="inner").dropna(subset=["q50"])
    out.update({"same_days": int(len(both)), "analog_mean_mae_same_days": round(float((both["guess"] - both["obs"]).abs().mean()), 3),
                "model_mae_same_days": round(float((both["q50"] - both["obs"]).abs().mean()), 3),
                "raw_hrrr_mae_same_days": round(float((both["qpf_3x3"] - both["obs"]).abs().mean()), 3)})
    big = both[both["obs"] >= 0.5]
    out["big_days"] = {"n": int(len(big)), "analog_mean_mae": round(float((big["guess"] - big["obs"]).abs().mean()), 2), "model_mae": round(float((big["q50"] - big["obs"]).abs().mean()), 2)}
    (data_dir() / "model" / "scores_analog.json").write_text(json.dumps(out, indent=1))
    return out
