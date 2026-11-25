"""Layer 3: snow level and rain/snow split, from wet-bulb temperature.

Falling snow survives until the wet-bulb temperature of the air is a little above freezing,
so wet-bulb is a better guide than plain temperature, especially in dry air. The forecast's
near-surface wet-bulb is fitted against elevation around the mountain, which gives a wet-bulb
temperature for any height and so a snow level.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..resort import Resort, data_dir
from .table import REF_ELEV

ALL_SNOW_C = 0.0        # wet-bulb at or below this: all snow
ALL_RAIN_C = 2.0        # wet-bulb at or above this: all rain
LEVEL_C = 0.5           # wet-bulb that defines the reported snow level
KELVIN = 273.15


def at_elevation(ref: float | np.ndarray, per_km: float | np.ndarray, elev_m: float | np.ndarray):
    """Value of a fitted profile at an elevation."""
    return np.asarray(ref, float) + np.asarray(per_km, float) * (np.asarray(elev_m, float) - REF_ELEV) / 1000.0


def snow_fraction(tw_c, all_snow: float = ALL_SNOW_C, all_rain: float = ALL_RAIN_C):
    """Share of precipitation falling as snow at a wet-bulb temperature (deg C): 1 when cold,
    0 when warm, a straight ramp between."""
    return np.clip((all_rain - np.asarray(tw_c, float)) / (all_rain - all_snow), 0.0, 1.0)


def snow_level_m(tw_ref_k, tw_per_km, level_c: float = LEVEL_C, floor: float = 1500.0, ceiling: float = 4500.0):
    """Elevation where the wet-bulb profile crosses `level_c`. When the profile does not cool
    with height (an inversion) no level can be defined and NaN is returned."""
    ref, slope = np.asarray(tw_ref_k, float) - KELVIN, np.asarray(tw_per_km, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = REF_ELEV + (level_c - ref) / slope * 1000.0
    z = np.where(slope < -0.5, z, np.nan)
    return np.clip(z, floor, ceiling)


def phase_cases(resort: Resort, table: pd.DataFrame, min_liquid: float = 0.1) -> pd.DataFrame:
    """Days at valley observer sites where it clearly snowed or clearly rained, matched to the
    forecast issued the morning before. A report dated D covers roughly 14z D-1 to 14z D, so it
    is matched to the 24-hour forecast window starting 12z on D-1."""
    valley = pd.read_parquet(data_dir() / "obs" / "valley_daily.parquet")
    valley = valley[~valley["multi_day"] & (valley["pcpn"] >= min_liquid) & valley["snow"].notna()]
    fc = table[table["tier"] == 24].set_index("start")
    elev = {s["id"]: s["elev_ft"] / 3.280839895 for s in resort.valley}
    rows = []
    for site, date, liquid, snow in zip(valley["site"], valley["date"], valley["pcpn"], valley["snow"]):
        start = date - pd.Timedelta(hours=12)
        if start not in fc.index or not np.isfinite(fc.at[start, "tw_ref"]):
            continue
        f = fc.loc[start]
        if snow == 0:
            kind = "rain"
        elif snow >= max(0.5, 4.0 * liquid):         # at least 4:1, so mostly snow
            kind = "snow"
        else:
            kind = "mixed"
        rows.append({"site": site, "date": date, "season": f["season"], "liquid": liquid, "snow": snow, "kind": kind,
                     "elev_m": elev[site],
                     "tw_c": float(at_elevation(f["tw_ref"], f["tw_per_km"], elev[site]) - KELVIN),
                     "tw_max_c": float(at_elevation(f["tw_ref_max"], f["tw_per_km"], elev[site]) - KELVIN),
                     "t_c": float(at_elevation(f["t_ref"], f["t_per_km"], elev[site]) - KELVIN),
                     "snow_level_m": float(snow_level_m(f["tw_ref"], f["tw_per_km"])),
                     "nbm_snow_level_m": f.get(f"nbm_snow_level_at_{site}", np.nan)})
    return pd.DataFrame(rows)


def score_phase(cases: pd.DataFrame) -> dict:
    """How well each rule separates clear snow days from clear rain days."""
    c = cases[cases["kind"].isin(["snow", "rain"])]
    y = (c["kind"] == "snow").to_numpy(float)
    out = {"snow_days": int(y.sum()), "rain_days": int((1 - y).sum())}
    if len(c) == 0:
        return out
    rules = {
        "wet-bulb ramp": snow_fraction(c["tw_c"]),
        "temperature ramp (0 to 2 C)": snow_fraction(c["t_c"]),
        "always snow": np.ones(len(c)),
    }
    if c["nbm_snow_level_m"].notna().any():
        rules["NBM snow level"] = np.where(c["nbm_snow_level_m"].notna(), (c["elev_m"] > c["nbm_snow_level_m"]).astype(float), np.nan)
    for name, p in rules.items():
        ok = np.isfinite(p)
        if ok.sum() == 0:
            continue
        hit = ((p[ok] >= 0.5) == (y[ok] == 1))
        out[name] = {"brier": float(np.mean((p[ok] - y[ok]) ** 2)), "correct": float(hit.mean()), "n": int(ok.sum()),
                     "rain_called_rain": float(hit[y[ok] == 0].mean()) if (y[ok] == 0).any() else float("nan"),
                     "snow_called_snow": float(hit[y[ok] == 1].mean()) if (y[ok] == 1).any() else float("nan")}
    return out
