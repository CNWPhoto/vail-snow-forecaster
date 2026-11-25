"""Layer 6, part one: settlement. How deep the new snow is the next morning, not how much fell.

Two layers are stepped hour by hour through a 24-hour window:

    new snow    arrives at its as-fallen density (water / snow-to-liquid ratio from layer 4) and
                densifies quickly at first, faster when warm (destructive metamorphism)
    old pack    compacts under its own weight and faster under the weight of the new snow

The rate laws have the form given by Anderson (1976) and used in the SNOW-17 model. The two rate
constants are fitted here to the SNOTEL depth sensors, leaving one season out at a time, so the
layer is scored against a real measurement on the mountain: the 24-hour change in snow depth
at seven sites, given the water that fell.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..obs import snotel
from ..resort import Resort, data_dir, load_resort
from . import slr
from .snowlevel import KELVIN, at_elevation

CM_PER_IN = 2.54
RHO_SETTLED = 0.09            # g/cm3: above this, new snow has lost its fragile crystal forms and slows down.
#                               Anderson's value is 0.15; 0.09 fits the SNOTEL two-day record better (see two_day_check)
NEW_RATES = (0.01, 0.02, 0.04, 0.06, 0.09, 0.13, 0.18)  # per hour at 0 C
OLD_RATES = (0.0, 0.01, 0.02, 0.04, 0.07, 0.11, 0.16)   # per hour per cm of water load at 0 C, before the density term
MAX_DAILY_DROP_IN = 8.0       # a larger 24-hour fall in depth is a sensor fault, not settling
MAX_GAIN_PER_IN_WATER = 30.0  # nor can a day end with more than 30 in of snow per inch of water, plus slack
SELF_RATES = (0.0, 0.0004, 0.0008, 0.0012, 0.0016, 0.002, 0.0025, 0.003)   # per hour at 0 C: the pack's slow background settling
WET_IN = 0.1


def simulate(water_in: np.ndarray, temp_c: np.ndarray, ratio: np.ndarray, depth0_in: np.ndarray, rho0: np.ndarray,
             new_rate: float, old_rate: float, self_rate: float = 0.0, sun: np.ndarray | float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Step the two layers through the window.

    water_in, temp_c: (cases, hours) hourly water and air temperature. ratio: as-fallen
    snow-to-liquid ratio per case. depth0_in, rho0: depth and density (g/cm3) of the old pack.
    `self_rate` is the pack's background settling, fitted on dry days. `sun` scales the
    new-snow rate (1 = shaded or cloudy). Returns (new-snow depth, old-pack depth) at the end,
    in inches."""
    water_in, temp_c = np.atleast_2d(water_in).astype(float), np.atleast_2d(temp_c).astype(float)
    n, hours = water_in.shape
    ratio = np.asarray(ratio, float)
    ratio = ratio if ratio.ndim == 2 else np.repeat(np.broadcast_to(ratio, (n,))[:, None], hours, axis=1)   # may vary by hour
    new_water, new_depth = np.zeros(n), np.zeros(n)
    old_depth = np.array(np.broadcast_to(np.asarray(depth0_in, float), (n,)), dtype=float)
    old_water = old_depth * np.broadcast_to(np.asarray(rho0, float), (n,))
    for h in range(hours):
        t = np.minimum(temp_c[:, h], 0.0)
        w = water_in[:, h]
        new_water, new_depth = new_water + w, new_depth + w * ratio[:, h]
        with np.errstate(divide="ignore", invalid="ignore"):
            rho_new = np.where(new_depth > 0, new_water / new_depth, 0.0)
            rho_old = np.where(old_depth > 0, old_water / old_depth, 0.0)
        slow = np.where(rho_new > RHO_SETTLED, np.exp(-46.0 * (rho_new - RHO_SETTLED)), 1.0)
        new_depth = new_depth * np.exp(-new_rate * sun * np.exp(0.10 * t) * slow)
        load_cm = (new_water + 0.5 * old_water) * CM_PER_IN          # the new snow plus half the pack's own weight
        old_depth = old_depth * np.exp(-(old_rate * load_cm * np.exp(-21.0 * rho_old) + self_rate) * np.exp(0.08 * t))
    return new_depth, old_depth


def smooth_depth(depth: pd.Series) -> pd.Series:
    """Hourly depth with single-reading spikes removed (the sensor reads to an inch and glitches)."""
    d = depth.asfreq("h")
    local = d.rolling(25, center=True, min_periods=6).median()
    return d.where((d - local).abs() <= 8.0).rolling(5, center=True, min_periods=2).median()


def cases(resort: Resort, table: pd.DataFrame, first_wy: int = 2019) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """One row per site per 24-hour window with a usable depth record, plus the hourly water and
    temperature arrays (rows aligned with the table)."""
    hw = pd.read_parquet(data_dir() / "obs" / "snotel_hourly_water.parquet")
    fc = table[(table["tier"] == 24) & table["t_ref"].notna()].set_index("start")
    rows, water, temp = [], [], []
    for site in resort.snotel:
        h = snotel.load_hourly(site["triplet"], first_wy=first_wy)
        h.index = h.index + pd.Timedelta(hours=8)                    # station clock to UTC
        depth, swe = smooth_depth(h["depth"]), h["swe"].asfreq("h").rolling(5, center=True, min_periods=2).median()
        tobs = ((h["tobs"].asfreq("h") - 32.0) / 1.8).interpolate(limit=3)
        w = hw[hw["site"] == site["id"]].set_index("time_utc")["water_in"]
        elev = site["elev_ft"] / 3.280839895
        for start, f in fc.iterrows():
            hours = pd.date_range(start + pd.Timedelta(hours=1), start + pd.Timedelta(hours=24), freq="h")
            end = hours[-1]
            if start not in depth.index or end not in depth.index:
                continue
            d0, d1, s0 = depth.get(start), depth.get(end), swe.get(start)
            ww, tt = w.reindex(hours).to_numpy(), tobs.reindex(hours).to_numpy()
            if not np.isfinite([d0, d1, s0]).all() or np.isnan(ww).any() or np.isnan(tt).any() or d0 < 6 or s0 <= 0:
                continue
            if d1 - d0 < -MAX_DAILY_DROP_IN or d1 - d0 > MAX_GAIN_PER_IN_WATER * ww.sum() + 4.0:
                continue                                             # sensor fault
            t_site = float(at_elevation(f["t_ref"], f["t_per_km"], elev)) - KELVIN
            rows.append({"site": site["id"], "start": start, "season": int(f["season"]), "elev_m": elev, "water": float(ww.sum()),
                         "depth0": float(d0), "change": float(d1 - d0), "rho0": float(np.clip(s0 / d0, 0.08, 0.5)),
                         "temp_c": float(tt.mean()), "t_site_c": t_site,
                         "tw_site_c": float(at_elevation(f["tw_ref"], f["tw_per_km"], elev)) - KELVIN,
                         "t700_c": f["t700"] - KELVIN, "t500_c": f["t500"] - KELVIN, "spd600": f["spd600"], "rh700": f["rh700"],
                         "liquid": max(float(ww.sum()), 0.05)})
            water.append(ww)
            temp.append(tt)
    return pd.DataFrame(rows), np.array(water), np.array(temp)


def hindcast(resort: Resort | None = None) -> tuple[pd.DataFrame, dict]:
    """Leave-one-season-out test: given the water that fell, predict the 24-hour depth change."""
    resort = resort or load_resort()
    out = data_dir() / "model"
    table = pd.read_parquet(out / "table.parquet")
    table = table[table["season"] <= 2026]
    c, water, temp = cases(resort, table)
    slr_cases = slr.cases(resort, table)
    c["ratio"] = np.nan
    for season in sorted(c["season"].unique()):
        te = (c["season"] == season).to_numpy()
        c.loc[te, "ratio"] = slr.predict(slr.fit(slr_cases[slr_cases["season"] != season]), c[te])
    obs = c["change"].to_numpy()
    wet = (c["water"] >= WET_IN).to_numpy()
    args = (water, temp, c["ratio"].to_numpy(), c["depth0"].to_numpy(), c["rho0"].to_numpy())
    depth0 = c["depth0"].to_numpy()
    background = {k: sum(simulate(*args, 0.0, 0.0, k)) - depth0 for k in SELF_RATES}       # no new-snow settling yet
    c["settled"], c["flat_ratio"], c["no_settling"], c["new_snow_settled"] = np.nan, np.nan, c["water"] * c["ratio"], np.nan
    chosen = []
    for season in sorted(c["season"].unique()):
        te, tr = (c["season"] == season).to_numpy(), (c["season"] != season).to_numpy()
        # 1. background settling of the pack, from dry days in the training seasons
        k = min(SELF_RATES, key=lambda k: abs((background[k][tr & ~wet] - obs[tr & ~wet]).mean()))
        # 2. new-snow settling and compaction under load, from snow days
        best, best_err = None, np.inf
        for a in NEW_RATES:
            for b in OLD_RATES:
                new, old = simulate(water[tr & wet], temp[tr & wet], *(x[tr & wet] for x in args[2:]), a, b, k)
                err = np.abs(new + old - depth0[tr & wet] - obs[tr & wet]).mean()
                if err < best_err:
                    best, best_err = (a, b), err
        new, old = simulate(water[te], temp[te], *(x[te] for x in args[2:]), best[0], best[1], k)
        c.loc[te, "settled"], c.loc[te, "new_snow_settled"] = new + old - depth0[te], new
        c.loc[te, "flat_ratio"] = c.loc[te, "water"] * np.median(obs[tr & wet] / c["water"].to_numpy()[tr & wet])
        chosen.append({"season": int(season), "new_rate": best[0], "old_rate": best[1], "self_rate": k})
    w = c[wet]
    methods = {"settled": "this model (ratio, then settling)", "no_settling": "ratio only, no settling", "flat_ratio": "one fixed depth-per-water ratio"}

    def block(g: pd.DataFrame) -> dict:
        return {"n": int(len(g)), "mean_change_in": round(float(g["change"].mean()), 2),
                **{label: {"mae": round(float((g[col] - g["change"]).abs().mean()), 2), "bias": round(float((g[col] - g["change"]).mean()), 2)}
                   for col, label in methods.items()}}

    result = {"chosen": chosen, "snow_days": block(w), "big_days_1in_water": block(w[w["water"] >= 1.0]),
              "by_site": {s: block(g) for s, g in w.groupby("site")},
              "new_snow_left_next_morning": round(float(w["new_snow_settled"].sum() / w["no_settling"].sum()), 2),
              "dry_days": {"n": int((~wet).sum()), "mean_change_in": round(float(obs[~wet].mean()), 2),
                           "model_mean_in": round(float(c.loc[~wet, "settled"].mean()), 2)}}
    c.to_parquet(out / "settle_cases.parquet")
    (out / "scores_settle.json").write_text(json.dumps(result, indent=1))
    return c, result


def two_day_check() -> dict:
    """Does the settling carry on correctly into a second, dry day? Snow days of 0.3 in of water
    or more that were followed by a dry day at the same site, scored on the two-day depth change."""
    c = pd.read_parquet(data_dir() / "model" / "settle_cases.parquet").sort_values(["site", "start"])
    nxt = c.groupby("site").shift(-1)
    ok = (c["water"] >= 0.3) & (nxt["water"] < 0.05) & ((nxt["start"] - c["start"]) == pd.Timedelta(days=1))
    a, b = c[ok], nxt[ok]
    new_rate, old_rate, self_rate = fitted_rates()
    water = np.repeat((a["water"] / 24.0).to_numpy()[:, None], 24, axis=1)
    t1, t2 = (np.repeat(x["temp_c"].to_numpy()[:, None], 24, axis=1) for x in (a, b))
    args = (a["ratio"].to_numpy(), a["depth0"].to_numpy(), a["rho0"].to_numpy(), new_rate, old_rate, self_rate)
    n1, o1 = simulate(water, t1, *args)
    n2, o2 = simulate(np.concatenate([water, np.zeros_like(water)], axis=1), np.concatenate([t1, t2], axis=1), *args)
    fallen = float((a["water"] * a["ratio"]).sum())
    e1 = n1 + o1 - a["depth0"].to_numpy() - a["change"].to_numpy()
    e2 = n2 + o2 - a["depth0"].to_numpy() - (a["change"] + b["change"]).to_numpy()
    return {"pairs": int(len(a)), "observed_gain_day1_in": round(float(a["change"].mean()), 2),
            "observed_gain_after_day2_in": round(float((a["change"] + b["change"]).mean()), 2),
            "day1": {"mae": round(float(np.abs(e1).mean()), 2), "bias": round(float(e1.mean()), 2)},
            "day2": {"mae": round(float(np.abs(e2).mean()), 2), "bias": round(float(e2.mean()), 2)},
            "new_snow_left_day1": round(float(n1.sum()) / fallen, 2), "new_snow_left_day2": round(float(n2.sum()) / fallen, 2)}


def fitted_rates() -> tuple[float, float, float]:
    """The rate constants to use in a forecast: the set chosen most often across the folds."""
    chosen = json.loads((data_dir() / "model" / "scores_settle.json").read_text())["chosen"]
    sets = pd.Series([(c["new_rate"], c["old_rate"], c["self_rate"]) for c in chosen])
    return sets.mode().iat[0]


def mountain_end_to_end(site: str = "vail") -> dict:
    """The only end-to-end snow test on the hill: forecast water (layer 1), ratio (layer 4) and
    settling, against the 24-hour change the mid-mountain depth sensor recorded. The National
    Blend's snowfall is as-fallen, so it is shown raw and with this model's settling share."""
    out = data_dir() / "model"
    c = pd.read_parquet(out / "settle_cases.parquet")
    c = c[c["site"] == site].set_index("start")
    h = pd.read_parquet(out / "hindcast_tier24.parquet").set_index("start")
    d = c.join(h[["mean", "q10", "q90", "qpf_3x3", "nbm_snow_in", "ndfd_snow_in"]], how="inner").dropna(subset=["mean"])
    new_rate, old_rate, self_rate = fitted_rates()

    def settled(water: pd.Series) -> np.ndarray:
        w = np.repeat((water / 24.0).to_numpy()[:, None], 24, axis=1)
        t = np.repeat(d["t_site_c"].to_numpy()[:, None], 24, axis=1)
        new, old = simulate(w, t, d["ratio"].to_numpy(), d["depth0"].to_numpy(), d["rho0"].to_numpy(), new_rate, old_rate, self_rate)
        return new + old - d["depth0"].to_numpy()

    d["model"], d["low"], d["high"] = settled(d["mean"]), settled(d["q10"]), settled(d["q90"])
    d["raw_hrrr"] = settled(d["qpf_3x3"])
    share = json.loads((out / "scores_settle.json").read_text())["new_snow_left_next_morning"]
    background = settled(d["mean"] * 0.0)
    d["nbm_raw"], d["nbm_settled"] = d["nbm_snow_in"], d["nbm_snow_in"] * share + background
    d["nws_settled"] = d["ndfd_snow_in"] * share + background
    labels = {"model": "this model", "raw_hrrr": "raw HRRR water, same ratio and settling", "nbm_raw": "National Blend snowfall as issued",
              "nbm_settled": "National Blend, settled", "nws_settled": "NWS official, settled"}

    def block(g: pd.DataFrame) -> dict:
        return {"n": int(len(g)), "mean_change_in": round(float(g["change"].mean()), 2),
                **{lab: {"mae": round(float((g[col] - g["change"]).abs().mean()), 2), "bias": round(float((g[col] - g["change"]).mean()), 2)}
                   for col, lab in labels.items() if g[col].notna().all()}}

    both = d.dropna(subset=["nbm_snow_in", "ndfd_snow_in"])
    return {"all_days": block(d), "with_nbm_and_nws": block(both), "gain_3in_plus": block(both[both["change"] >= 3.0]),
            "inside_10_90": round(float(((d["change"] >= d["low"] - 0.5) & (d["change"] <= d["high"] + 0.5)).mean()), 3)}
