"""The training and scoring table: one row per forecast window, with what each model said
when it was issued and what the gauge then measured.

Windows start at 00z or 12z (5 pm and 5 am Mountain Standard Time). A `tier` is the number of
hours from the forecast being issued to the end of the window it covers:

    12   the next 12 hours           HRRR 00z and 12z runs
    24   the next 24 hours           HRRR 12z run
    36   hours 12 to 36              HRRR 00z run
    48   hours 24 to 48              HRRR 12z run (runs reach 48 h from December 2020), GFS
    72   hours 48 to 72              GFS only
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..catalog.regimes import sector
from ..fcst import archive, grib, hrrr
from ..obs import hourly
from ..resort import Resort, data_dir, load_resort
from ..upper import physics as ph

MM_PER_IN = 25.4
# tier -> (run hour, first forecast hour, last forecast hour)
HRRR_TIERS = {12: ((0, 12), 1, 12), 24: ((12,), 1, 24), 36: ((0,), 13, 36), 48: ((12,), 25, 48)}
COARSE_TIERS = {24: (0, 24), 48: (24, 48), 72: (48, 72)}      # 12z run: window in forecast hours


def sites(resort: Resort) -> dict[str, tuple[float, float, float]]:
    """id -> (lat, lon, elevation m) for every SNOTEL and valley site."""
    out = {}
    for s in list(resort.snotel) + list(resort.valley):
        out[s["id"]] = (s["lat"], s["lon"], s["elev_ft"] / 3.280839895)
    return out


def elevation_matched(terrain: np.ndarray, r: int, c: int, elev: float, reach: int = 2) -> tuple[int, int]:
    """The cell within `reach` of (r, c) whose model terrain is closest to the site's real elevation."""
    win = terrain[r - reach: r + reach + 1, c - reach: c + reach + 1]
    i, j = np.unravel_index(int(np.argmin(np.abs(win - elev))), win.shape)
    return r - reach + int(i), c - reach + int(j)


def _take(d: dict, name: str, first: int, last: int) -> np.ndarray | None:
    """Forecast hours first..last of a field, or None unless every hour is there."""
    if name not in d:
        return None
    h = d[f"h_{name}"]
    idx = np.nonzero((h >= first) & (h <= last))[0]
    if len(idx) != last - first + 1:
        return None
    a = d[name][idx]
    return None if np.isnan(a).all() else a


def hrrr_window(d: dict, first: int, last: int, cells: dict, target: str, terrain: np.ndarray, elev_cell: tuple[int, int]) -> dict | None:
    """Features of one HRRR run over forecast hours first..last."""
    ap = _take(d, "apcp", first, last)
    if ap is None:
        return None
    ap = ap / MM_PER_IN
    r, c = cells[target]
    total = ap.sum(axis=0)
    near = ap[:, r - 1: r + 2, c - 1: c + 2].mean(axis=(1, 2))
    f = {
        "qpf_site": float(total[r, c]), "qpf_3x3": float(near.sum()),
        "qpf_5x5": float(total[r - 2: r + 3, c - 2: c + 3].mean()), "qpf_max5": float(total[r - 2: r + 3, c - 2: c + 3].max()),
        "qpf_elev": float(total[elev_cell]), "qpf_area": float(total.mean()),
        "wet_hours": int((near >= 0.004).sum()),
    }
    six = np.convolve(near, np.ones(6), mode="valid") if len(near) >= 6 else np.array([near.sum()])
    f["peak6_share"] = float(six.max() / near.sum()) if near.sum() > 0.02 else np.nan
    for sid, (rr, cc) in cells.items():
        f[f"qpf_at_{sid}"] = float(total[rr - 1: rr + 2, cc - 1: cc + 2].mean())
    we = _take(d, "weasd", first, last)
    f["hrrr_snow_water"] = float(we[:, r - 1: r + 2, c - 1: c + 2].mean(axis=(1, 2)).sum() / MM_PER_IN) if we is not None else np.nan

    fields = {k: _take(d, k, first, last) for k in ("u700", "v700", "u500", "v500", "t700", "t500", "td700", "pwat", "t2", "td2")}
    if any(v is None for v in fields.values()):
        return f
    area = {k: np.nanmean(v, axis=(1, 2)) for k, v in fields.items() if k not in ("t2", "td2")}
    u6, v6 = 0.5 * (area["u700"] + area["u500"]), 0.5 * (area["v700"] + area["v500"])
    direction, vec, steady = ph.vector_mean_direction(u6, v6)
    wet = ap.mean(axis=(1, 2))
    f.update({
        "u600": float(u6.mean()), "v600": float(v6.mean()), "dir600": direction, "spd600": float(np.hypot(u6, v6).mean()),
        "spd600_vec": vec, "steady600": steady, "spd500": float(np.hypot(area["u500"], area["v500"]).mean()),
        "t700": float(area["t700"].mean()), "t500": float(area["t500"].mean()),
        "lapse": float((area["t700"] - area["t500"]).mean()),
        "rh700": float(np.clip(ph.saturation_vapour_pressure(area["td700"]) / ph.saturation_vapour_pressure(area["t700"]), 0, 1.2).mean()),
        "pwat": float(area["pwat"].mean()),
    })
    f["dir600_wet"] = ph.vector_mean_direction(u6, v6, wet)[0] if wet.sum() > 0.01 else direction
    t2, td2 = fields["t2"][:, r, c], fields["td2"][:, r, c]
    tw = ph.wet_bulb(t2, td2, ph.standard_pressure(terrain[r, c]))
    w = near if near.sum() > 0.02 else np.ones_like(near)
    f.update({"t2_site": float(t2.mean()), "tw_site": float(tw.mean()), "tw_site_wet": float((tw * w).sum() / w.sum()),
              "t2_min": float(t2.min()), "model_elev": float(terrain[r, c])})
    f.update(surface_profile(fields["t2"], fields["td2"], terrain, r, c, w))
    return f


REF_ELEV = 3000.0
PROFILE_REACH = 10           # cells either side of the site: about 30 km


def fit_line(z: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares line through `values` (hours, cells) against elevation `z` (cells).
    Returns the value at the reference elevation and the change per kilometre, per hour."""
    zc = (z - REF_ELEV) / 1000.0
    zm = zc.mean()
    slope = ((zc - zm) * (values - values.mean(axis=1, keepdims=True))).sum(axis=1) / ((zc - zm) ** 2).sum()
    return values.mean(axis=1) - slope * zm, slope


def surface_profile(t2: np.ndarray, td2: np.ndarray, terrain: np.ndarray, r: int, c: int, weights: np.ndarray) -> dict:
    """How near-surface temperature and wet-bulb temperature change with elevation around the
    mountain, from the forecast's own cells (valley floors to ridge tops within about 30 km).
    This lets any elevation be given a temperature without trusting a single coarse cell."""
    sl = (slice(None), slice(max(r - PROFILE_REACH, 0), r + PROFILE_REACH + 1), slice(max(c - PROFILE_REACH, 0), c + PROFILE_REACH + 1))
    z = terrain[sl[1:]].ravel()
    t = t2[sl].reshape(len(t2), -1)
    tw = ph.wet_bulb(t, td2[sl].reshape(len(t2), -1), ph.standard_pressure(z)[None, :])
    w = weights / weights.sum()
    t_ref, t_slope = fit_line(z, t)
    tw_ref, tw_slope = fit_line(z, tw)
    return {"t_ref": float((t_ref * w).sum()), "t_per_km": float((t_slope * w).sum()),
            "tw_ref": float((tw_ref * w).sum()), "tw_per_km": float((tw_slope * w).sum()),
            "tw_ref_max": float(tw_ref.max()), "tw_ref_min": float(tw_ref.min())}


def hrrr_rows(resort: Resort) -> pd.DataFrame:
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    pts = sites(resort)
    cells = {k: hrrr.window_cell(la, lo) for k, (la, lo, _) in pts.items()}
    terrain = hrrr.terrain()
    elev_cell = elevation_matched(terrain, *cells[target], pts[target][2])
    rows = []
    for run in hrrr.season_runs(last=str(pd.Timestamp.now().normalize().date())):
        d = hrrr.load_run(run)
        if d is None:
            continue
        for tier, (run_hours, first, last) in HRRR_TIERS.items():
            if run.hour not in run_hours:
                continue
            f = hrrr_window(d, first, last, cells, target, terrain, elev_cell)
            if f is not None:
                rows.append({"tier": tier, "run": run, "start": run + pd.Timedelta(hours=first - 1), "hours": last - first + 1, **f})
    return pd.DataFrame(rows)


def _sum_steps(d: dict, prefix: str, steps: range, r: int, c: int, scale: float) -> float:
    keys = [f"{prefix}_{s:03d}" for s in steps]
    if any(k not in d for k in keys):
        return np.nan
    return float(sum(d[k][r, c] for k in keys) * scale)


def baseline_rows(resort: Resort) -> pd.DataFrame:
    """NBM, NWS official and GFS forecasts for the same windows, at the grid cell nearest each site."""
    pts = sites(resort)
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    rows = []
    for model in ("nbm", "ndfd"):
        qpf, snow = ("apcp", "asnow") if model == "nbm" else ("qpf", "snow")
        for run in archive.runs(model, last=str(pd.Timestamp.now().normalize().date())):
            d = archive.load(model, run)
            if d is None:
                continue
            windows = {12: (0, 12)} if run.hour == 0 else {12: (0, 12), **COARSE_TIERS}
            for tier, (a, b) in windows.items():
                row = {"tier": tier, "start": run + pd.Timedelta(hours=a)}
                for sid, (la, lo, _) in pts.items():
                    r, c = grib.nearest(d["lat"], d["lon"], la, lo)
                    row[f"{model}_qpf_at_{sid}"] = _sum_steps(d, qpf, range(a + 6, b + 1, 6), r, c, 1 / MM_PER_IN)
                    row[f"{model}_snow_at_{sid}"] = _sum_steps(d, snow, range(a + 6, b + 1, 6), r, c, 39.3701)
                    if model == "nbm":
                        for key, name in (("snowlr", "nbm_slr_at"), ("snowlvl", "nbm_snow_level_at")):
                            k = f"{key}_{a + 12:03d}"
                            row[f"{name}_{sid}"] = float(d[k][r, c]) if k in d else np.nan
                    if sid == target:
                        row[f"{model}_qpf"] = row[f"{model}_qpf_at_{sid}"]
                        row[f"{model}_snow_in"] = row[f"{model}_snow_at_{sid}"]
                        if model == "nbm":
                            mid = a + 12
                            row["nbm_slr"] = float(d[f"snowlr_{mid:03d}"][r, c]) if f"snowlr_{mid:03d}" in d else np.nan
                            row["nbm_snow_level_m"] = float(d[f"snowlvl_{mid:03d}"][r, c]) if f"snowlvl_{mid:03d}" in d else np.nan
                rows.append(row)
    out = pd.DataFrame(rows)
    out = out.groupby(["tier", "start"], as_index=False).first() if len(out) else out

    grows = []
    for run in archive.runs("gfs", last=str(pd.Timestamp.now().normalize().date())):
        d = archive.load("gfs", run)
        if d is None:
            continue
        la, lo, _ = pts[target]
        r, c = grib.nearest(d["lat"], d["lon"], la, lo)
        acc = {0: 0.0, **{h: float(d[f"apcp_{h:03d}"][r, c]) if f"apcp_{h:03d}" in d else np.nan for h in (24, 48, 72)}}
        accm = {0: 0.0, **{h: float(d[f"apcp_{h:03d}"].mean()) if f"apcp_{h:03d}" in d else np.nan for h in (24, 48, 72)}}
        for tier, (a, b) in COARSE_TIERS.items():
            row = {"tier": tier, "start": run + pd.Timedelta(hours=a),
                   "gfs_qpf": (acc[b] - acc[a]) / MM_PER_IN, "gfs_qpf_area": (accm[b] - accm[a]) / MM_PER_IN}
            mid = a + 12
            if f"u600_{mid:03d}" in d:
                u, v = float(d[f"u600_{mid:03d}"][r, c]), float(d[f"v600_{mid:03d}"][r, c])
                row.update({"gfs_u600": u, "gfs_v600": v, "gfs_spd600": float(np.hypot(u, v)),
                            "gfs_dir600": float(np.degrees(np.arctan2(-u, -v)) % 360),
                            "gfs_t600": float(d[f"t600_{mid:03d}"][r, c]), "gfs_rh600": float(d[f"rh600_{mid:03d}"][r, c]),
                            "gfs_t700": float(d[f"t700_{mid:03d}"][r, c]), "gfs_pwat": float(d[f"pwat_{mid:03d}"][r, c])})
            grows.append(row)
    g = pd.DataFrame(grows)
    erows = []
    for run in archive.runs("gefs", last=str(pd.Timestamp.now().normalize().date())):
        d = archive.load("gefs", run)
        if d is None:
            continue
        la, lo, _ = pts[target]
        r, c = grib.nearest(d["lat"], d["lon"], la, lo)
        for tier, (a, b) in COARSE_TIERS.items():
            steps = range(a + 6, b + 1, 6)
            mean = _sum_steps(d, "mean", steps, r, c, 1 / MM_PER_IN)
            keys = [f"spread_{s:03d}" for s in steps]
            # six-hour spreads added in quadrature: a floor on the 24-hour spread (it ignores persistence)
            spread = float(np.sqrt(sum(d[k][r, c] ** 2 for k in keys))) / MM_PER_IN if all(k in d for k in keys) else np.nan
            erows.append({"tier": tier, "start": run + pd.Timedelta(hours=a), "gefs_mean": mean, "gefs_spread": spread})
    for extra in (g, pd.DataFrame(erows)):
        if len(extra):
            out = out.merge(extra, on=["tier", "start"], how="outer") if len(out) else extra
    return out


def observed(resort: Resort, starts: pd.DataFrame) -> pd.DataFrame:
    """Gauge totals for each window, at every SNOTEL site."""
    hw = pd.read_parquet(data_dir() / "obs" / "snotel_hourly_water.parquet")
    target = next(s["id"] for s in resort.snotel if s.get("target"))
    out = starts[["start", "hours"]].drop_duplicates().reset_index(drop=True)
    for sid, g in hw.groupby("site"):
        g = g.set_index("time_utc")
        for hours, part in out.groupby("hours"):
            w = hourly.window_sum(g, pd.DatetimeIndex(part["start"]), int(hours))
            out.loc[part.index, f"obs_at_{sid}"] = w["obs_in"].to_numpy()
            if sid == target:
                out.loc[part.index, "obs"] = w["obs_in"].to_numpy()
                out.loc[part.index, "obs_spread"] = w["spread_in"].to_numpy()
    return out


def build(resort: Resort | None = None) -> pd.DataFrame:
    resort = resort or load_resort()
    h = hrrr_rows(resort)
    b = baseline_rows(resort)
    keys = pd.concat([h[["tier", "start"]], b[["tier", "start"]]]).drop_duplicates()
    keys["hours"] = np.where(keys["tier"] == 12, 12, 24)
    t = keys.merge(h.drop(columns="hours"), on=["tier", "start"], how="left").merge(b, on=["tier", "start"], how="left")
    t = t.merge(observed(resort, t), on=["start", "hours"], how="left")
    t["season"] = t["start"].dt.year + (t["start"].dt.month >= 10).astype(int)
    # the previous HRRR run's view of the same 24 hours (issued 12 hours earlier), and how far the two disagree
    prev = t.loc[t["tier"] == 36, ["start", "qpf_3x3", "qpf_elev"]].rename(columns={"qpf_3x3": "prev_qpf_3x3", "qpf_elev": "prev_qpf_elev"})
    prev["tier"] = 24
    t = t.merge(prev, on=["tier", "start"], how="left")
    t["run_gap"] = (t["qpf_3x3"] - t["prev_qpf_3x3"]).abs()
    # the regime of each window, taken from the shortest-lead HRRR forecast that covers it
    short = h[h["tier"].isin([12, 24])].sort_values("tier")
    ref = short.drop_duplicates("start").set_index("start")
    flow = {s: sector(d, v) for s, d, v in zip(ref.index, ref.get("dir600", np.nan), ref.get("spd600_vec", np.nan))}
    day_start = t["start"].where(t["hours"] == 24, t["start"])
    t["regime"] = [flow.get(s, "unknown") for s in day_start]
    t = t.sort_values(["tier", "start"]).reset_index(drop=True)
    out = data_dir() / "model"
    out.mkdir(parents=True, exist_ok=True)
    t.to_parquet(out / "table.parquet")
    return t
