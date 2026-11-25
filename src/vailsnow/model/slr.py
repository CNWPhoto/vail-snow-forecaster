"""Layer 4: snow-to-liquid ratio (how many inches of snow one inch of water makes).

Trained on local manual measurements only: the Vail COOP station and CoCoRaHS observers in the
valley, who report new snow and its water content each morning. Inputs are the temperature at
the site's elevation, the temperature and humidity aloft and the wind, all from the forecast
issued the morning before. Compared against the Kuchera rule, a flat climatological value and,
where it exists, the National Blend's ratio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ..resort import Resort, data_dir
from .snowlevel import KELVIN, at_elevation

MIN_SNOW_IN = 2.0           # below this the ruler error dominates the ratio
MIN_LIQUID_IN = 0.11
SLR_RANGE = (3.0, 35.0)
FEATURES = ("t_site_c", "tw_site_c", "t700_c", "t500_c", "spd600", "rh700", "liquid", "elev_m")
SETTINGS = dict(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=15, l2_regularization=1.0,
                early_stopping=False, random_state=0)


def kuchera(tmax_k):
    """Kuchera ratio from the warmest temperature in the column below 500 mb (kelvin)."""
    t = np.asarray(tmax_k, float)
    return np.where(t > 271.16, 12.0 + (271.16 - t), 12.0 + 2.0 * (271.16 - t))


def cases(resort: Resort, table: pd.DataFrame) -> pd.DataFrame:
    """One row per usable morning report, with the forecast issued 24 hours before it."""
    valley = pd.read_parquet(data_dir() / "obs" / "valley_daily.parquet")
    v = valley[~valley["multi_day"] & (valley["snow"] >= MIN_SNOW_IN) & (valley["pcpn"] >= MIN_LIQUID_IN)]
    fc = table[table["tier"] == 24].set_index("start")
    elev = {s["id"]: s["elev_ft"] / 3.280839895 for s in resort.valley}
    rows = []
    for site, date, liquid, snow in zip(v["site"], v["date"], v["pcpn"], v["snow"]):
        start = date - pd.Timedelta(hours=12)
        if start not in fc.index or not np.isfinite(fc.at[start, "t_ref"]):
            continue
        f = fc.loc[start]
        slr = snow / liquid
        if not SLR_RANGE[0] <= slr <= SLR_RANGE[1]:
            continue
        t_site = float(at_elevation(f["t_ref"], f["t_per_km"], elev[site]))
        rows.append({"site": site, "date": date, "season": int(f["season"]), "snow": snow, "liquid": liquid, "slr": slr,
                     "elev_m": elev[site], "t_site_c": t_site - KELVIN,
                     "tw_site_c": float(at_elevation(f["tw_ref"], f["tw_per_km"], elev[site])) - KELVIN,
                     "t700_c": f["t700"] - KELVIN, "t500_c": f["t500"] - KELVIN, "spd600": f["spd600"], "rh700": f["rh700"],
                     "dir600": f["dir600"], "kuchera": float(kuchera(max(t_site, f["t700"], f["t500"]))),
                     "nbm_slr": f.get(f"nbm_slr_at_{site}", np.nan), "regime": f["regime"]})
    return pd.DataFrame(rows)


def fit(train: pd.DataFrame) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(loss="absolute_error", **SETTINGS).fit(train[list(FEATURES)].to_numpy(float), train["slr"].to_numpy(float))


def predict(model: HistGradientBoostingRegressor, rows: pd.DataFrame) -> np.ndarray:
    return np.clip(model.predict(rows[list(FEATURES)].to_numpy(float)), *SLR_RANGE)


def hindcast(c: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-season-out predictions, with the baselines alongside."""
    parts = []
    for season in sorted(c["season"].unique()):
        test, train = c[c["season"] == season], c[c["season"] != season]
        if len(train) < 100:
            continue
        p = test.copy()
        p["model"] = predict(fit(train), test)
        p["climatology"] = train["slr"].median()
        slope, intercept = np.polyfit(train["t700_c"], train["slr"], 1)
        p["linear_t700"] = np.clip(intercept + slope * test["t700_c"], *SLR_RANGE)
        parts.append(p)
    out = pd.concat(parts)
    out["kuchera"] = np.clip(out["kuchera"], *SLR_RANGE)
    out["flat_10"], out["flat_15"] = 10.0, 15.0
    return out


def score(h: pd.DataFrame) -> pd.DataFrame:
    """Error in the ratio, and in inches of snow when the ratio is applied to the measured water."""
    rows = []
    for name in ("model", "linear_t700", "climatology", "kuchera", "flat_10", "flat_15", "nbm_slr"):
        ok = h[name].notna()
        if ok.sum() == 0:
            continue
        g = h[ok]
        rows.append({"method": name, "n": int(ok.sum()), "slr_mae": float((g[name] - g["slr"]).abs().mean()),
                     "slr_bias": float((g[name] - g["slr"]).mean()),
                     "snow_mae_in": float((g[name] * g["liquid"] - g["snow"]).abs().mean()),
                     "snow_bias_in": float((g[name] * g["liquid"] - g["snow"]).mean())})
    return pd.DataFrame(rows)
