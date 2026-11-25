"""Numbers behind STORM_CLIMATOLOGY.md: how snowfall at Vail and its neighbours depends on flow regime."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from ..resort import data_dir
from . import regimes as rg
from .events import daily_gain

DIRECTION_BINS = 191.25 + 22.5 * np.arange(9)             # edges of eight 22.5 degree bins, SSW through N
BIN_NAMES = ["SSW", "SW", "WSW", "W", "WNW", "NW", "NNW", "N"]


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records"))


def direction_table(gains: pd.DataFrame, daily: pd.DataFrame, pairs: list[tuple[str, str]], min_speed: float = rg.WEAK_MS) -> pd.DataFrame:
    """Ratio of totals for site pairs in 22.5 degree bins of 600 mb wind direction, all winter days."""
    d = daily[(daily["spd600_vec"] >= min_speed) & daily.index.month.isin(rg.WINTER)]
    ang = np.where(d["dir600"] < 191.25, d["dir600"] + 360.0, d["dir600"])
    which = pd.Series(pd.cut(ang, DIRECTION_BINS, labels=BIN_NAMES, right=False), index=d.index, name="bin")
    g = gains.join(which, how="inner").dropna(subset=["bin"])
    rows = []
    for name in BIN_NAMES:
        sub = g[g["bin"] == name]
        row = {"from": name, "days": len(sub)}
        for a, b in pairs:
            pair = sub[[a, b]].dropna()
            if len(pair) >= 40 and pair[b].sum() > 0:
                r = rg.ratio_with_interval(pair[a], pair[b], rg.water_year(pair.index))
                row[f"{a}/{b}"], row[f"{a}/{b}_lo"], row[f"{a}/{b}_hi"], row[f"{a}/{b}_days"] = r["ratio"], r["lo"], r["hi"], len(pair)
        rows.append(row)
    return pd.DataFrame(rows)


def direction_bin(direction: pd.Series | np.ndarray) -> pd.Categorical:
    """22.5 degree compass bin (SSW through N) for wind directions; other directions are left empty."""
    d = np.asarray(direction, dtype=float)
    return pd.cut(np.where(d < 191.25, d + 360.0, d), DIRECTION_BINS, labels=BIN_NAMES, right=False)


def direction_yield(gains: pd.DataFrame, daily: pd.DataFrame, valley: pd.DataFrame, site: str = "vail") -> pd.DataFrame:
    """For each 22.5 degree bin of daily 600 mb wind direction: share of winter days, share of the
    site's winter water, water per day, odds of a snow day, and the town observer's snow-to-liquid ratio."""
    d = daily[daily.index.month.isin(rg.WINTER)].copy()
    d["bin"] = np.where(d["spd600_vec"] >= rg.WEAK_MS, direction_bin(d["dir600"]).astype(object), "weak flow")
    d["bin"] = d["bin"].where(d["bin"].notna(), "other")
    g = d.join(gains[site].rename("gain"), how="inner").dropna(subset=["gain"])
    v = valley[(valley["site"] == "vail_coop") & ~valley["multi_day"]].set_index("date")[["pcpn", "snow"]]
    v.index = v.index - pd.Timedelta(days=1)
    g = g.join(v)
    rows = []
    for name in BIN_NAMES + ["weak flow", "other"]:
        sub = g[g["bin"] == name]
        if len(sub) == 0:
            continue
        wet = sub[(sub["pcpn"] >= 0.1) & (sub["snow"] > 0)]
        rows.append({"from": name, "days": len(sub), "day_share": round(len(sub) / len(g), 3), "water_share": round(float(sub["gain"].sum() / g["gain"].sum()), 3),
                     "in_per_day": round(float(sub["gain"].mean()), 3), "snow_day_odds": round(float((sub["gain"] >= 0.2).mean()), 2),
                     "big_day_odds": round(float((sub["gain"] >= 0.7).mean()), 3), "t700_c": round(float(sub["t700"].mean()), 1),
                     "town_snow_to_liquid": round(float(wet["snow"].sum() / wet["pcpn"].sum()), 1) if len(wet) >= 20 else None})
    return pd.DataFrame(rows)


def mountain_vs_valley(snotel: pd.DataFrame, valley: pd.DataFrame, regimes: pd.Series, site: str = "vail_coop") -> pd.DataFrame:
    """Mid-mountain gauge precipitation over a valley observer's, and that observer's snow-to-liquid ratio, by regime."""
    m = daily_gain(snotel[snotel["site"] == "vail"].set_index("date")["prec"]).clip(lower=0)
    v = valley[(valley["site"] == site) & ~valley["multi_day"]].set_index("date")[["pcpn", "snow"]]
    v.index = v.index - pd.Timedelta(days=1)            # a morning report mostly covers the day before
    d = pd.DataFrame({"mtn": m}).join(v, how="inner").dropna(subset=["mtn", "pcpn"])
    d = d[d.index.month.isin(rg.WINTER)].join(regimes, how="inner")
    rows = []
    for name, sub in [("all days", d)] + [(r, d[d["regime"] == r]) for r in rg.ORDER]:
        if len(sub) < 60 or sub["pcpn"].sum() <= 0:
            continue
        wet = sub[(sub["pcpn"] >= 0.1) & sub["snow"].notna() & (sub["snow"] > 0)]
        rows.append({"regime": name, "days": len(sub), **rg.ratio_with_interval(sub["mtn"], sub["pcpn"], rg.water_year(sub.index)),
                     "snow_days": len(wet), "valley_snow_to_liquid": round(float(wet["snow"].sum() / wet["pcpn"].sum()), 1) if len(wet) >= 20 else None})
    return pd.DataFrame(rows)


def all_days() -> dict:
    """Everything that needs only the daily flow table: no storm selection anywhere."""
    d = data_dir()
    snotel = pd.read_parquet(d / "obs" / "snotel_daily.parquet")
    valley = pd.read_parquet(d / "obs" / "valley_daily.parquet")
    daily = pd.read_parquet(d / "upper" / "daily_flow.parquet")
    regimes = rg.daily_regimes(daily)
    gains = rg.site_gains(snotel)
    sites = [c for c in gains.columns]
    win = daily[daily.index.month.isin(rg.WINTER)].join(regimes)
    temps = win.groupby("regime")[["t700", "t600", "spd600"]].mean().round(1).reindex([r for r in rg.ORDER if r in set(win["regime"])])
    snowy = gains["vail"].reindex(win.index)
    return {
        "period": [str(daily.index.min().date()), str(daily.index.max().date())],
        "regime_shares": _records(rg.regime_shares(gains, regimes, sites)),
        "regime_weather": _records(temps.reset_index()),
        "snow_day_odds": _records(win.assign(snow=snowy >= 0.2).dropna(subset=["snow"]).groupby("regime")["snow"].agg(["mean", "size"]).round(3).reset_index()),
        "direction_yield": _records(direction_yield(gains, daily, valley)),
        "site_ratios": _records(rg.by_regime_site_ratios(gains, regimes)),
        "direction_bins": _records(direction_table(gains, daily, [("vail", "mccoy_park"), ("vail", "beaver_creek"), ("vail", "copper"), ("copper", "mccoy_park")])),
        "valley_east_west": _records(rg.valley_east_west(valley, regimes)),
        "mountain_vs_town": _records(mountain_vs_valley(snotel, valley, regimes)),
    }


def storm_table() -> pd.DataFrame:
    """Top storms with their upper-air features and regime."""
    d = data_dir()
    ev = pd.read_parquet(d / "catalog" / "events.parquet")
    ft = pd.read_parquet(d / "upper" / "event_features.parquet")
    t = ev.merge(ft, on="event_id", how="inner")
    t["regime"] = [rg.event_regime(r) for _, r in t.iterrows()]
    t["cluster"] = rg.kmeans_check(t).to_numpy()
    t["jet_overhead"] = t["spd300_overhead"] >= 50.0
    return t


def storms() -> dict:
    """Regime make-up of the top storms and what each regime looks like."""
    t = storm_table()
    t.to_parquet(data_dir() / "catalog" / "top_storms.parquet", index=False)
    t.to_csv(data_dir() / "catalog" / "top_storms.csv", index=False)
    num = ["swe_in", "max_2day_swe_in", "days", "spd600", "t700_c", "t600_c", "tcwv_mm", "ivt_above_700", "rh600", "dgz_rh",
           "dgz_height_m", "lapse_700_500", "froude", "jet_speed", "spd300_overhead"]
    by = t.groupby("regime")
    summary = by[num].mean().round(2)
    summary.insert(0, "storms", by.size())
    summary.insert(1, "share_of_top_storm_water", (by["swe_in"].sum() / t["swe_in"].sum()).round(3))
    summary["jet_overhead_share"] = by["jet_overhead"].mean().round(2)
    summary = summary.reindex([r for r in rg.ORDER if r in summary.index]).reset_index()
    months = pd.crosstab(t["regime"], t["start"].dt.month)
    cross = pd.crosstab(t["regime"], t["cluster"])
    pairs = {}
    for other in ("copper", "mccoy_park", "beaver_creek"):
        col = f"swe_{other}"
        rows = []
        for name, sub in t.dropna(subset=[col]).groupby("regime"):
            if len(sub) >= 5:
                rows.append({"regime": name, "storms": len(sub), **rg.ratio_with_interval(sub["swe_in"], sub[col], sub["water_year"].to_numpy())})
        pairs[other] = rows
    # does the jet overhead under northwest flow go with bigger storms?
    nw = t[t["regime"] == "northwest"]
    jet = {"with_jet": {"storms": int(nw["jet_overhead"].sum()), "mean_swe_in": round(float(nw.loc[nw["jet_overhead"], "swe_in"].mean()), 2),
                        "mean_2day_in": round(float(nw.loc[nw["jet_overhead"], "max_2day_swe_in"].mean()), 2)},
           "without": {"storms": int((~nw["jet_overhead"]).sum()), "mean_swe_in": round(float(nw.loc[~nw["jet_overhead"], "swe_in"].mean()), 2),
                       "mean_2day_in": round(float(nw.loc[~nw["jet_overhead"], "max_2day_swe_in"].mean()), 2)}}
    corr = t[num].corr(method="spearman")["swe_in"].drop("swe_in").round(2).to_dict()
    t["bin"] = direction_bin(t["dir600"])
    fine = t.groupby("bin", observed=True).agg(storms=("swe_in", "size"), water_in=("swe_in", "sum"), mean_in=("swe_in", "mean"),
                                               turn_deg=("turn600_deg", "median")).round(2).reset_index()
    return {"storms": len(t), "summary": _records(summary), "months": json.loads(months.to_json()), "kmeans_crosstab": json.loads(cross.to_json()),
            "storm_ratios_selected_on_vail": pairs, "northwest_jet": jet, "rank_correlation_with_storm_water": corr,
            "by_direction_bin": _records(fine), "median_turn_deg": float(t["turn600_deg"].median()),
            "share_backing": round(float((t["turn600_deg"] < -20).mean()), 2), "share_veering": round(float((t["turn600_deg"] > 20).mean()), 2)}


def snodas_patterns(min_gain: float = 0.5) -> dict:
    """How SNODAS spreads each top storm across the zones, relative to its own value at the SNOTEL site,
    and how well it matched the SNOTEL sites."""
    import rasterio
    from pyproj import Transformer

    from ..grids import snodas
    from ..resort import load_resort
    resort = load_resort()
    t = pd.read_parquet(data_dir() / "catalog" / "top_storms.parquet")
    t = t[t["start"] >= snodas.FIRST]
    tdir = resort.out_dir()
    zs = rasterio.open(tdir / "zones_10m.tif")
    zone, band = zs.read(1), zs.read(2)
    to_utm = Transformer.from_crs(4326, zs.crs, always_xy=True)
    sn = next(p for p in resort.points if p["id"] == "snotel_842")
    r0, c0 = zs.index(*to_utm.transform(sn["lon"], sn["lat"]))
    upper = next(b.index for b in resort.bands if b.id == "upper")
    rows, fidelity = [], []
    for _, e in t.iterrows():
        field, west, north = snodas.event_change(resort, e["start"], e["end"])
        g = snodas.on_zone_grid(field, west, north, zs)
        at_site = float(g[r0, c0])
        for s in resort.snotel:
            obs = e["swe_in"] if s.get("target") else e.get(f"swe_{s['id']}")
            p = next((q for q in [dict(lat=39.61765, lon=-106.38019, id="vail"), dict(lat=39.48917, lon=-106.17154, id="copper"),
                                  dict(lat=39.59871, lon=-106.51113, id="beaver_creek"), dict(lat=39.60231, lon=-106.544, id="mccoy_park")] if q["id"] == s["id"]), None)
            if p and pd.notna(obs):
                fidelity.append({"site": s["id"], "event_id": e["event_id"], "observed": float(obs),
                                 "snodas": snodas.sample(field, resort, e["end"] + pd.Timedelta(days=1), p["lat"], p["lon"])})
        if at_site < min_gain:
            continue
        row = {"event_id": e["event_id"], "regime": e["regime"], "snodas_at_snotel": round(at_site, 2), "observed": e["swe_in"]}
        for z in resort.zones:
            row[z.id] = float(np.nanmean(g[zone == z.index]))
            sel = (zone == z.index) & (band == upper)
            row[f"{z.id}_upper"] = float(np.nanmean(g[sel])) if sel.sum() > 50 else np.nan
        rows.append(row)
    zs.close()
    p = pd.DataFrame(rows)
    p.to_parquet(data_dir() / "catalog" / "snodas_zone_gains.parquet", index=False)
    f = pd.DataFrame(fidelity)
    fid = [{"site": s, "storms": len(d), "snodas_over_observed": round(float(d["snodas"].sum() / d["observed"].sum()), 2),
            "correlation": round(float(d[["snodas", "observed"]].corr().iloc[0, 1]), 2)} for s, d in f.groupby("site")]
    ids = [z.id for z in resort.zones]
    out = {"storms_used": len(p), "fidelity": fid, "zones_vs_snotel_cell": {}, "upper_band_vs_snotel_cell": {}}
    for name, sub in [("all", p)] + [(r, p[p["regime"] == r]) for r in rg.ORDER if (p["regime"] == r).sum() >= 5]:
        out["zones_vs_snotel_cell"][name] = {"storms": len(sub), **{z: round(float(sub[z].sum() / sub["snodas_at_snotel"].sum()), 2) for z in ids}}
        out["upper_band_vs_snotel_cell"][name] = {z: round(float(sub[f"{z}_upper"].sum() / sub.loc[sub[f"{z}_upper"].notna(), "snodas_at_snotel"].sum()), 2)
                                                  for z in ids if sub[f"{z}_upper"].notna().sum() >= 5}
    return out


def build() -> dict:
    out = {"all_days": all_days(), "storms": storms(), "snodas": snodas_patterns()}
    (data_dir() / "catalog" / "climatology.json").write_text(json.dumps(out, indent=1))
    return out
