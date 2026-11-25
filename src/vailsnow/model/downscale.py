"""Layer 2: spreading the mid-mountain amount across the terrain.

Three ingredients are compared and combined, and each is tested the only way the data allows:
by predicting what the six neighbouring SNOTEL sites and the valley observers received, given
what fell at the Vail gauge.

    PRISM       the long-term average pattern (does not change with the weather)
    SB          the linear orographic theory, run each day with that day's wind and stability;
                only its departure from its own average is used, so PRISM sets the mean
    HRRR        the weather model's own pattern for the day, raw and as a departure from its
                own average pattern

The hindcast result (see PHASE3_SUMMARY.md): PRISM carries nearly all the skill, the HRRR
departure adds a little and moves the right way with the flow, and the orographic theory adds
nothing at these sites. The forecast therefore uses PRISM times the damped HRRR departure.

Site-specific regime ratios from the storm catalogue are scored too, as a ceiling: they use
each site's own history, which no ski zone has.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from ..fetch import fetch_3dep
from ..grids import prism
from ..resort import Resort, cache_dir, data_dir, load_resort
from . import orographic as og
from .table import MM_PER_IN, sites

HALF_WIDTH_M = 120_000.0
CELL_M = 500.0
TAUS = (500.0, 1000.0, 2000.0)
BETAS = (0.25, 0.5, 1.0)
ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
WET_IN = 0.1
HRRR_FLOOR_IN = 0.05        # below this at the target, the model's site-to-target ratio is noise
ANOMALY_LIMIT = 2.0         # the day's departure from the average pattern is capped at a factor of two


def regional_terrain(resort: Resort) -> tuple[np.ndarray, float, float]:
    """Elevation on a 500 m grid, 240 km across, centred on the target gauge.
    Returns (height with row 0 north, west edge, north edge) in the resort's projection."""
    target = next(s for s in resort.snotel if s.get("target"))
    x, y = Transformer.from_crs(4326, resort.epsg, always_xy=True).transform(target["lon"], target["lat"])
    x, y = round(x / CELL_M) * CELL_M, round(y / CELL_M) * CELL_M
    bounds = (x - HALF_WIDTH_M, y - HALF_WIDTH_M, x + HALF_WIDTH_M, y + HALF_WIDTH_M)
    path = fetch_3dep(bounds, CELL_M, resort.epsg, cache_dir() / "dem" / f"{resort.id}_wide_{int(CELL_M)}m.tif")
    with rasterio.open(path) as ds:
        h = ds.read(1).astype(float)
    h[h < -1000] = np.nan
    h = np.where(np.isnan(h), np.nanmean(h), h)
    return h, bounds[0], bounds[3]


def grid_positions(resort: Resort, west: float, north: float) -> dict[str, tuple[float, float]]:
    """Fractional (row, col) of each site on the regional grid."""
    tr = Transformer.from_crs(4326, resort.epsg, always_xy=True)
    out = {}
    for sid, (lat, lon, _) in sites(resort).items():
        x, y = tr.transform(lon, lat)
        out[sid] = ((north - y) / CELL_M - 0.5, (x - west) / CELL_M - 0.5)
    return out


def orographic_at_sites(days: pd.DataFrame, terrain: og.Terrain, pos: dict, tau: float) -> pd.DataFrame:
    """Orographic rate (mm/h) at every site for every day, from that day's forecast wind and air mass."""
    ids = list(pos)
    rows, cols = np.array([pos[i][0] for i in ids]), np.array([pos[i][1] for i in ids])
    out = np.full((len(days), len(ids)), np.nan)
    for n, (u, v, t700, lapse) in enumerate(zip(days["u600"], days["v600"], days["t700"], days["lapse"])):
        if not np.isfinite([u, v, t700, lapse]).all():
            continue
        p = og.parameters(t700, lapse)
        field = terrain.precipitation(u, v, p["cw"], p["hw"], p["nm"], tau_c=tau, tau_f=tau)
        out[n] = og.sample(field, rows, cols)
    return pd.DataFrame(out, index=days.index, columns=ids)


def sb_ratio(oro: pd.DataFrame, background: pd.Series, target: str, beta: float) -> pd.DataFrame:
    """Each site's total rate over the target's, with a floor so a strong lee never gives zero."""
    bg = background.clip(lower=0.05)
    total = oro.mul(beta).add(bg, axis=0)
    total = total.where(total.ge(0.1 * bg, axis=0), 0.1 * bg, axis=0)
    return total.div(total[target], axis=0)


def prism_ratios(resort: Resort) -> dict[str, float]:
    """Winter normal at each site over the winter normal at the target."""
    total, tr, _ = prism.winter_normal(resort)
    pts = sites(resort)
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    val = {}
    for sid, (lat, lon, _) in pts.items():
        col, row = ~tr * (lon, lat)
        val[sid] = float(og.sample(total.astype(float), np.array([row - 0.5]), np.array([col - 0.5]))[0])
    return {sid: v / val[target] for sid, v in val.items()}


def cases(resort: Resort, table: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(days, long table). `days` holds one row per 24-hour window with the forecast inputs;
    the long table has one row per site per day with what fell there and at the target."""
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    t = table[(table["tier"] == 24) & table["obs"].notna() & table["u600"].notna()].set_index("start")
    valley = pd.read_parquet(data_dir() / "obs" / "valley_daily.parquet")
    valley = valley[~valley["multi_day"] & valley["pcpn"].notna()]
    rows = []
    for sid in sites(resort):
        if sid == target:
            continue
        if f"obs_at_{sid}" in t:
            obs = t[f"obs_at_{sid}"]
        else:                                   # a morning report dated D covers the window that started 12z on D-1
            v = valley[valley["site"] == sid].set_index("date")["pcpn"]
            obs = pd.Series(v.reindex(t.index.normalize() + pd.Timedelta(days=1)).to_numpy(), index=t.index)
        part = pd.DataFrame({"site": sid, "obs": obs, "target": t["obs"], "season": t["season"], "regime": t["regime"],
                             "hrrr_site": t[f"qpf_at_{sid}"], "hrrr_target": t[f"qpf_at_{target}"]})
        rows.append(part.dropna(subset=["obs"]).reset_index())
    return t, pd.concat(rows, ignore_index=True)


def _mae(obs: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - obs)))


def evaluate(resort: Resort | None = None) -> dict:
    """Leave-one-season-out comparison of the ways to spread the target amount to other sites."""
    resort = resort or load_resort()
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    table = pd.read_parquet(data_dir() / "model" / "table.parquet")
    table = table[table["season"] <= 2026]
    days, long = cases(resort, table)
    long = long[(long["target"] >= WET_IN) | (long["obs"] >= WET_IN)].reset_index(drop=True)
    height, west, north = regional_terrain(resort)
    terrain, pos = og.Terrain(height, CELL_M), grid_positions(resort, west, north)
    background = days["qpf_area"] * MM_PER_IN / 24.0
    oro = {tau: orographic_at_sites(days, terrain, pos, tau) for tau in TAUS}
    pr = prism_ratios(resort)
    long["prism"] = long["target"] * long["site"].map(pr)
    long["same"] = long["target"]
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (long["hrrr_site"] / long["hrrr_target"]).where(long["hrrr_target"] >= HRRR_FLOOR_IN)
    long["hrrr_pattern"] = long["target"] * ratio.clip(0.1, 10.0).fillna(long["site"].map(pr))

    def sb_column(tau: float, beta: float) -> np.ndarray:
        r = sb_ratio(oro[tau], background, target, beta)
        return np.array([r.at[s, sid] for s, sid in zip(long["start"], long["site"])])

    raw = {(tau, beta): sb_column(tau, beta) for tau in TAUS for beta in BETAS}
    for name in ("site_ratio", "regime_ratio", "prism_sb", "prism_hrrr"):
        long[name] = np.nan
    chosen = []
    for season in sorted(long["season"].unique()):
        te, tr = (long["season"] == season).to_numpy(), (long["season"] != season).to_numpy()
        train = long[tr]
        # ceilings that use each site's own history
        by_site = train.groupby("site")[["obs", "target"]].sum()
        site_ratio = by_site["obs"] / by_site["target"]
        long.loc[te, "site_ratio"] = long.loc[te, "target"] * long.loc[te, "site"].map(site_ratio)
        by_reg = train.groupby(["site", "regime"])[["obs", "target"]].sum()
        reg_ratio = (by_reg["obs"] / by_reg["target"]).where(by_reg["target"] >= 3.0)
        keys = list(zip(long.loc[te, "site"], long.loc[te, "regime"]))
        rr = pd.Series([reg_ratio.get(k, np.nan) for k in keys], index=long.index[te]).fillna(long.loc[te, "site"].map(site_ratio))
        long.loc[te, "regime_ratio"] = long.loc[te, "target"] * rr
        # the physics: pick the delay, strength and damping on the training seasons only
        best = None
        for (tau, beta), col in raw.items():
            mean_ratio = pd.Series(col[tr] * train["target"].to_numpy()).groupby(train["site"].to_numpy()).sum() / by_site["target"]
            anomaly = col / long["site"].map(mean_ratio).to_numpy()
            for alpha in ALPHAS:
                pred = long["prism"].to_numpy() * anomaly ** alpha
                score = _mae(long["obs"].to_numpy()[tr], pred[tr])
                if best is None or score < best[0]:
                    best = (score, tau, beta, alpha, pred)
        long.loc[te, "prism_sb"] = best[4][te]
        chosen.append({"season": int(season), "tau_s": best[1], "strength": best[2], "damping": best[3]})
        # PRISM times the weather model's pattern for the day, after removing that model's own
        # average ratio at each site (so its standing biases drop out) and damping what is left
        ok = train["hrrr_target"] >= HRRR_FLOOR_IN
        own = train[ok].groupby("site")["hrrr_site"].sum() / train[ok].groupby("site")["hrrr_target"].sum()
        anomaly = (ratio / long["site"].map(own)).clip(1 / ANOMALY_LIMIT, ANOMALY_LIMIT).fillna(1.0).to_numpy()
        a = min(ALPHAS, key=lambda a: _mae(long["obs"].to_numpy()[tr], (long["prism"].to_numpy() * anomaly ** a)[tr]))
        long.loc[te, "prism_hrrr"] = (long["prism"].to_numpy() * anomaly ** a)[te]
        chosen[-1]["hrrr_damping"] = a

    methods = {"same": "same as Vail", "prism": "PRISM pattern", "prism_sb": "PRISM x orographic theory",
               "hrrr_pattern": "HRRR pattern for the day", "prism_hrrr": "PRISM x HRRR departure from its average", "hrrr_site": "raw HRRR at the site",
               "site_ratio": "site's own long-term ratio (ceiling)", "regime_ratio": "site's own ratio by regime (ceiling)"}
    result = {"days": int(long["start"].nunique()), "site_days": int(len(long)), "prism_ratio": {k: round(v, 3) for k, v in pr.items()},
              "chosen": chosen, "overall": {}, "by_site": {}, "by_regime": {}, "regime_pattern": {}}
    for col, label in methods.items():
        result["overall"][label] = {"mae": round(_mae(long["obs"], long[col]), 4), "bias": round(float((long[col] - long["obs"]).mean()), 4)}
    for sid, g in long.groupby("site"):
        result["by_site"][sid] = {"n": int(len(g)), "mean_obs": round(float(g["obs"].mean()), 3),
                                  **{label: round(_mae(g["obs"], g[col]), 4) for col, label in methods.items()}}
    for reg, g in long.groupby("regime"):
        if len(g) >= 150:
            result["by_regime"][reg] = {"n": int(len(g)), **{label: round(_mae(g["obs"], g[col]), 4) for col, label in methods.items()}}
    # does each method reproduce how the site-to-Vail ratio changes with the flow?
    for sid, g in long.groupby("site"):
        rows = {}
        for reg, gg in g.groupby("regime"):
            if gg["target"].sum() >= 5.0:
                rows[reg] = {"observed": round(float(gg["obs"].sum() / gg["target"].sum()), 2),
                             **{label: round(float(gg[col].sum() / gg["target"].sum()), 2)
                                for col, label in methods.items() if col in ("prism", "prism_sb", "hrrr_pattern", "prism_hrrr")}}
        result["regime_pattern"][sid] = rows
    seen = long.groupby(["site", "regime"])[["obs", "target"]].sum()
    seen = seen[seen["target"] >= 5.0]
    rel_obs = seen["obs"] / seen["target"]
    rel_obs = rel_obs / rel_obs.groupby(level=0).transform("mean")
    for col, label in methods.items():
        g = long.groupby(["site", "regime"])[[col, "target"]].sum().loc[seen.index]
        rel = g[col] / g["target"]
        rel = rel / rel.groupby(level=0).transform("mean")
        result["overall"][label]["regime_shift_error"] = round(float(np.average(np.abs(np.log(rel / rel_obs)), weights=seen["target"])), 3)
    out = data_dir() / "model"
    long.to_parquet(out / "downscale_cases.parquet")
    (out / "scores_downscale.json").write_text(json.dumps(result, indent=1))
    return result
