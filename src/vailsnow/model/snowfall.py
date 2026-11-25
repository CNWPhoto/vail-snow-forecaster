"""All four layers chained, scored where new snow is actually measured: the valley observers.

    inches of snow at a site = mid-mountain water (layer 1)
                               x site factor (layer 2: PRISM x damped HRRR departure)
                               x share falling as snow (layer 3: wet-bulb at the site's elevation)
                               x snow-to-liquid ratio (layer 4)

Every piece is fitted without the season being scored. Nothing on the ski terrain measures new
snow, so this is the only end-to-end snowfall test the data allows; the sites are 1,900 to
3,100 ft below mid-mountain.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..resort import Resort, data_dir, load_resort
from . import slr
from .downscale import ALPHAS, ANOMALY_LIMIT, HRRR_FLOOR_IN, prism_ratios
from .snowlevel import KELVIN, at_elevation, snow_fraction

SITE_DAMPING = 0.5


def hindcast(resort: Resort | None = None) -> pd.DataFrame:
    resort = resort or load_resort()
    out = data_dir() / "model"
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    h = pd.read_parquet(out / "hindcast_tier24.parquet").set_index("start")
    table = pd.read_parquet(out / "table.parquet")
    table = table[table["season"] <= h["season"].max()]
    slr_cases = slr.cases(resort, table)
    valley = pd.read_parquet(data_dir() / "obs" / "valley_daily.parquet")
    valley = valley[~valley["multi_day"] & valley["snow"].notna()]
    pr = prism_ratios(resort)
    elev = {s["id"]: s["elev_ft"] / 3.280839895 for s in resort.valley}
    rows = []
    for sid in elev:
        v = valley[valley["site"] == sid].set_index("date")
        obs = pd.DataFrame({"snow": v["snow"].reindex(h.index.normalize() + pd.Timedelta(days=1)).to_numpy(),
                            "liquid": v["pcpn"].reindex(h.index.normalize() + pd.Timedelta(days=1)).to_numpy()}, index=h.index)
        d = h.join(obs).dropna(subset=["snow", "t_ref"])
        d["site"], d["elev_m"] = sid, elev[sid]
        d["ratio_today"] = (d[f"qpf_at_{sid}"] / d[f"qpf_at_{target}"]).where(d[f"qpf_at_{target}"] >= HRRR_FLOOR_IN)
        rows.append(d.reset_index())
    d = pd.concat(rows, ignore_index=True)
    d["t_site_c"] = at_elevation(d["t_ref"], d["t_per_km"], d["elev_m"]) - KELVIN
    d["tw_site_c"] = at_elevation(d["tw_ref"], d["tw_per_km"], d["elev_m"]) - KELVIN
    d["t700_c"], d["t500_c"] = d["t700"] - KELVIN, d["t500"] - KELVIN
    d["snow_share"] = snow_fraction(d["tw_site_c"])
    d["kuchera"] = np.clip(slr.kuchera(np.maximum.reduce([d["t_site_c"] + KELVIN, d["t700"], d["t500"]])), *slr.SLR_RANGE)
    for col in ("water", "water_low", "water_high", "slr_model", "snow_model", "snow_low", "snow_high"):
        d[col] = np.nan
    for season in sorted(d["season"].unique()):
        te, tr = d["season"] == season, d["season"] != season
        ok = tr & (d[f"qpf_at_{target}"] >= HRRR_FLOOR_IN)
        own = d[ok].groupby("site").apply(lambda g: g[[f"qpf_at_{s}" for s in [g.name]]].to_numpy().sum() / g[f"qpf_at_{target}"].sum())
        departure = (d.loc[te, "ratio_today"] / d.loc[te, "site"].map(own)).clip(1 / ANOMALY_LIMIT, ANOMALY_LIMIT).fillna(1.0)
        factor = d.loc[te, "site"].map(pr) * departure ** SITE_DAMPING
        for src, dst in (("mean", "water"), ("q10", "water_low"), ("q90", "water_high")):
            d.loc[te, dst] = d.loc[te, src] * factor
        model = slr.fit(slr_cases[slr_cases["season"] != season])
        feats = d.loc[te].assign(liquid=d.loc[te, "water"].clip(lower=0.05))
        d.loc[te, "slr_model"] = slr.predict(model, feats)
    for src, dst in (("water", "snow_model"), ("water_low", "snow_low"), ("water_high", "snow_high")):
        d[dst] = d[src] * d["snow_share"] * d["slr_model"]
    d["snow_hrrr_kuchera"] = d.apply(lambda r: r[f"qpf_at_{r['site']}"], axis=1) * d["snow_share"] * d["kuchera"]
    d["snow_nbm"] = d.apply(lambda r: r.get(f"nbm_snow_at_{r['site']}", np.nan), axis=1)
    d["snow_nws"] = d.apply(lambda r: r.get(f"ndfd_snow_at_{r['site']}", np.nan), axis=1)
    keep = ["start", "site", "season", "regime", "snow", "liquid", "water", "snow_share", "slr_model", "snow_model", "snow_low", "snow_high",
            "snow_hrrr_kuchera", "snow_nbm", "snow_nws"]
    d = d[keep]
    d.to_parquet(out / "snowfall_hindcast.parquet")
    return d


def score(d: pd.DataFrame) -> dict:
    methods = {"snow_model": "this model", "snow_hrrr_kuchera": "raw HRRR x Kuchera", "snow_nbm": "National Blend (NBM)", "snow_nws": "NWS official"}

    def block(g: pd.DataFrame, cols: list[str]) -> dict:
        return {"n": int(len(g)), "mean_obs_in": round(float(g["snow"].mean()), 2),
                **{methods[c]: {"mae": round(float((g[c] - g["snow"]).abs().mean()), 3), "bias": round(float((g[c] - g["snow"]).mean()), 3)} for c in cols}}

    own = ["snow_model", "snow_hrrr_kuchera"]
    common = d.dropna(subset=["snow_nbm", "snow_nws"])
    result = {"all_seasons": block(d, own), "with_nbm_and_nws": block(common, list(methods)),
              "by_site": {s: block(g, list(methods)) for s, g in common.groupby("site")},
              "snow_days_3in_plus": block(common[common["snow"] >= 3.0], list(methods)),
              "forecast_3in_plus": {methods[c]: {"n": int((common[c] >= 3).sum()),
                                                 "mean_obs_in": round(float(common.loc[common[c] >= 3, "snow"].mean()), 2),
                                                 "mean_forecast_in": round(float(common.loc[common[c] >= 3, c].mean()), 2)} for c in methods},
              "inside_10_90": round(float(((d["snow"] >= d["snow_low"] - 1e-9) & (d["snow"] <= d["snow_high"] + 1e-9)).mean()), 3)}
    wet = d[d["snow_high"] >= 1.0]
    result["inside_10_90_when_snow_possible"] = round(float(((wet["snow"] >= wet["snow_low"]) & (wet["snow"] <= wet["snow_high"])).mean()), 3)
    (data_dir() / "model" / "scores_snowfall.json").write_text(json.dumps(result, indent=1))
    return result
