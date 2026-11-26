"""The weather of each hour, for the state: observations first, the model in between.

What is measured on the mountain and may be used: the mid-mountain SNOTEL gauge (water, snow
depth and air temperature, hourly, about an hour behind). The resort's own stations are not
public data this project may read, so wind, cloud and visibility come from the HRRR model's
newest short-range forecast, with the ridge-level wind corrected the way the forecast does
(half the free-air speed, 35 degrees backed: measured at the Copper Mountain summit station).

How much water fell at the gauge in an hour, in order of trust:

    checked     the gauge's daily total, quality-controlled by NRCS, shared out by its hourly record
    provisional the last day or so has no checked total yet. The raw hourly gauge moves a tenth of
                an inch at a time and often late, so it is averaged with the model's short-range amount
    forecast    hours still to come: the published median forecast for the day, timed by the model

The gauge amount is then spread over the mountain exactly as the forecast spreads it: the
long-term PRISM ratio, the model's pattern for these hours (damped), rain or snow by wet-bulb
temperature at each cell's elevation, and the fitted snow-to-liquid ratio.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ..fcst import hrrr
from ..model import final, settle, slr, zones
from ..model import orographic as og
from ..model.downscale import ANOMALY_LIMIT, HRRR_FLOOR_IN
from ..model.snowfall import SITE_DAMPING
from ..model.snowlevel import KELVIN, at_elevation, snow_fraction
from ..model.table import MM_PER_IN, elevation_matched, surface_profile
from ..resort import Resort, cache_dir, data_dir, web_data_dir
from ..upper import physics as ph
from . import grid
from .engine import Forcing

EXTRA = (("surface", "GUST", "gust"), ("entire_atmosphere", "TCDC", "cloud"), ("surface", "VIS", "vis"),
         ("10m_above_ground", "UGRD", "u10"), ("10m_above_ground", "VGRD", "v10"))
HOURLY_REACH = 18             # every HRRR run forecasts at least this many hours
SYNOPTIC_REACH = 48           # the 00, 06, 12 and 18 UTC runs go this far
PUBLISH_LAG = pd.Timedelta(minutes=70)        # a run is never on the public copy sooner than this after its hour
PATTERN_HOURS = (-2, 3)       # the model's pattern is taken over the six hours around the one in hand
STD_LAPSE_K_PER_KM = -6.5
DEPTH_DAY_START = 12          # the depth sensor is read against the model over 24 hours from 12 UTC (5 am Mountain Standard Time)
DEPTH_MIN_WATER_IN = 0.2      # with less water than this in the day, the sensor (which reads to the inch) has nothing clear to say
DEPTH_FACTOR_LIMITS = (0.6, 1.4)
LONE_TICK_IN = 0.05           # a provisional gauge amount below this (one 0.1 in tick spread over three hours)...
MODEL_DRY_IN = 0.004          # ...is ignored when the model has less than this in every hour...
MODEL_DRY_REACH = 2           # ...from two hours before to two hours after
TYPICAL_WIND = (270.0, 12.6)  # 600 mb direction and speed used when no model covers the hour (ridge wind of 6.3 m/s, the storm-day median)


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------

def extra_path(run: pd.Timestamp) -> Path:
    return cache_dir() / "hrrr_state" / f"{run:%Y}" / f"{run:%Y%m%d_%H}z.npz"


def published(run: pd.Timestamp) -> bool:
    """True once the public copy holds the run. The check is made before anything is fetched,
    because the forecast's own fetcher marks a run it cannot find as absent for good."""
    root = f"{hrrr.BASE}/sfc/{run:%Y%m%d}/{run:%Y%m%d}_{run:%H}z_fcst.zarr"
    for level, var in (("surface", "APCP_1hr_acc_fcst"), ("entire_atmosphere_single_layer", "PWAT"), ("surface", "VIS")):
        try:
            r = hrrr.session().head(f"{root}/{level}/{var}/{level}/{var}/0.{hrrr.CHUNK_ROW}.{hrrr.CHUNK_COL}", timeout=30)
        except Exception:
            return False
        if r.status_code != 200:
            return False
    return True


def fetch_extra(run: pd.Timestamp) -> None:
    p = extra_path(run)
    if p.exists():
        return
    out = {}
    for level, var, name in EXTRA:
        got = hrrr.fetch_field(run, level, var)
        if got is not None:
            out[f"h_{name}"], out[name] = got[0], got[1].astype(np.float32)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part.npz")
    np.savez_compressed(tmp, **out)
    tmp.replace(p)


def load_extra(run: pd.Timestamp) -> dict:
    p = extra_path(run)
    if not p.exists():
        return {}
    with np.load(p) as z:
        out = {k: z[k] for k in z.files}
    for k, v in out.items():
        if not k.startswith("h_"):
            v[v < hrrr.FILL] = np.nan
    return out


class Model:
    """HRRR, one hour at a time, from the newest run that covers the hour."""

    def __init__(self, resort: Resort, now: pd.Timestamp | None = None, fetch: bool = True):
        self.now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC").tz_localize(None)
        self.fetch = fetch
        target = next(s for s in resort.snotel if s.get("target"))
        self.cell = hrrr.window_cell(target["lat"], target["lon"])
        self._terrain = None
        self.gauge_elev = target["elev_ft"] / 3.280839895
        self._runs: dict[pd.Timestamp, dict | None] = {}
        self._hours: dict[pd.Timestamp, dict | None] = {}

    @property
    def terrain(self) -> np.ndarray:
        if self._terrain is None:
            self._terrain = hrrr.terrain()
        return self._terrain

    def _download(self, run: pd.Timestamp) -> None:
        """Get a run's files onto disk if they are not there and the public copy has them."""
        if not self.fetch:
            return
        if not hrrr.run_path(run).exists() and hrrr.FIRST_RUN <= run and run + PUBLISH_LAG <= self.now and published(run):
            hrrr.fetch_run(run)
        if hrrr.run_path(run).exists() and not extra_path(run).exists():
            try:
                fetch_extra(run)
            except Exception:                   # cloud, visibility and gusts are extras: the state runs without them
                pass

    def prefetch(self, first: pd.Timestamp, last: pd.Timestamp, workers: int = 8) -> int:
        """Download every long run that covers the hours from `first` to `last`, several at a time.
        Stepping through ten days needs about forty runs; one after another that takes many minutes."""
        newest = min(pd.Timestamp(last), self.now - PUBLISH_LAG).floor("6h")
        runs = [r for r in pd.date_range((pd.Timestamp(first) - pd.Timedelta(hours=6)).floor("6h"), newest, freq="6h")
                if r >= hrrr.FIRST_RUN and not (hrrr.run_path(r).exists() and extra_path(r).exists())] if self.fetch else []
        if runs:
            with ThreadPoolExecutor(workers) as pool:
                list(pool.map(self._download, runs))
        return len(runs)

    def _load(self, run: pd.Timestamp) -> dict | None:
        if run in self._runs:
            return self._runs[run]
        d = None
        self._download(run)
        if hrrr.run_path(run).exists():
            d = hrrr.load_run(run)
            if d is not None and any(k not in d for k in ("apcp", "t2", "td2", "u700", "v700", "u500", "v500", "t700", "t500", "td700")):
                d = None
        if d is not None:
            d.update(load_extra(run))
        self._runs[run] = d
        return d

    def candidates(self, t: pd.Timestamp) -> list[pd.Timestamp]:
        """Runs that could cover the hour ending at `t`, newest first. Hourly runs are only worth
        asking for near the present; further back the four long runs a day are enough."""
        newest = min(t - pd.Timedelta(hours=1), (self.now - PUBLISH_LAG).floor("h"))
        out = []
        if self.now - t <= pd.Timedelta(hours=8):
            out += [newest - pd.Timedelta(hours=k) for k in range(6)]
        run = newest.floor("6h")
        while t - run <= pd.Timedelta(hours=SYNOPTIC_REACH):
            out.append(run)
            run -= pd.Timedelta(hours=6)
        seen, ordered = set(), []
        for r in sorted(out, reverse=True):
            reach = SYNOPTIC_REACH if r.hour % 6 == 0 else HOURLY_REACH
            if r not in seen and pd.Timedelta(hours=1) <= t - r <= pd.Timedelta(hours=reach):
                seen.add(r)
                ordered.append(r)
        return ordered

    def hour(self, t: pd.Timestamp, max_tries: int = 8) -> dict | None:
        """Model values for the hour ending at `t` (UTC), or None when no run on hand covers it."""
        t = pd.Timestamp(t)
        if t in self._hours:
            return self._hours[t]
        out = None
        for run in self.candidates(t)[:max_tries]:
            d = self._load(run)
            if d is not None:
                out = self._extract(d, run, int((t - run) / pd.Timedelta(hours=1)))
                if out is not None:
                    break
        self._hours[t] = out
        return out

    @staticmethod
    def _at(d: dict, name: str, lead: int) -> np.ndarray | None:
        if name not in d:
            return None
        i = np.nonzero(d[f"h_{name}"] == lead)[0]
        return d[name][i[0]] if len(i) and np.isfinite(d[name][i[0]]).any() else None

    def _extract(self, d: dict, run: pd.Timestamp, lead: int) -> dict | None:
        r, c = self.cell
        f = {k: self._at(d, k, lead) for k in ("apcp", "t2", "td2", "u700", "v700", "u500", "v500", "t700", "t500", "td700")}
        if any(v is None for v in f.values()):
            return None
        k = zones.REACH
        six = [self._at(d, "apcp", lead + j) for j in range(PATTERN_HOURS[0], PATTERN_HOURS[1] + 1)]
        six = [a for a in six if a is not None]
        pattern = sum(a[r - k: r + k + 1, c - k: c + k + 1] for a in six) / MM_PER_IN
        prof = surface_profile(f["t2"][None], f["td2"][None], self.terrain, r, c, np.ones(1))
        u6, v6 = 0.5 * float(np.nanmean(f["u700"]) + np.nanmean(f["u500"])), 0.5 * float(np.nanmean(f["v700"]) + np.nanmean(f["v500"]))
        t700 = float(np.nanmean(f["t700"]))
        out = {"run": run, "lead": lead, "water_in": float(f["apcp"][r - 1: r + 2, c - 1: c + 2].mean() / MM_PER_IN), "pattern": pattern,
               "t_ref": prof["t_ref"], "t_per_km": prof["t_per_km"], "tw_ref": prof["tw_ref"], "tw_per_km": prof["tw_per_km"],
               "t700": t700, "t500": float(np.nanmean(f["t500"])), "spd600": float(np.hypot(u6, v6)), "dir600": float(np.degrees(np.arctan2(-u6, -v6)) % 360.0),
               "rh700": float(np.clip(ph.saturation_vapour_pressure(np.nanmean(f["td700"])) / ph.saturation_vapour_pressure(t700), 0, 1.2))}
        er, ec = elevation_matched(self.terrain, r, c, self.gauge_elev)
        top = np.unravel_index(int(np.argmax(self.terrain[r - 2: r + 3, c - 2: c + 3])), (5, 5))       # the highest model cell nearby stands in for the ridge
        tr, tc = r - 2 + int(top[0]), c - 2 + int(top[1])
        gust, cloud, vis, u10, v10 = (self._at(d, n, lead) for n in ("gust", "cloud", "vis", "u10", "v10"))
        if gust is not None:
            out["gust_mid"], out["gust_ridge"] = float(gust[er, ec]), float(gust[tr, tc])
        if cloud is not None:
            out["cloud"] = float(np.clip(np.nanmean(cloud[r - 1: r + 2, c - 1: c + 2]) / 100.0, 0, 1))
        if vis is not None:
            out["vis_m"] = float(np.nanmin(vis[r - 1: r + 2, c - 1: c + 2]))
        if u10 is not None and v10 is not None:
            out["wind_mid"] = float(np.hypot(u10[er, ec], v10[er, ec]))
            out["wind_mid_from"] = float(np.degrees(np.arctan2(-u10[er, ec], -v10[er, ec])) % 360.0)
        return out


# ---------------------------------------------------------------------------
# The gauge
# ---------------------------------------------------------------------------

def historic_record(resort: Resort, first: pd.Timestamp, last: pd.Timestamp) -> dict:
    """The gauge record around a past period, in the same form as the live one."""
    from ..extras import scorecard
    from ..obs import snotel
    site = scorecard.target(resort)
    wy = first.year + (first.month >= 10)
    daily = snotel.load_site(site["triplet"])["prec"]
    h = snotel.load_hourly(site["triplet"], first_wy=wy)
    lo, hi = first - pd.Timedelta(days=12), last + pd.Timedelta(days=4)
    h = h[(h.index >= lo) & (h.index <= hi)]
    daily = daily[(daily.index >= lo.normalize()) & (daily.index <= hi.normalize())]
    return scorecard.assemble(daily, h)


def published_forecast() -> dict | None:
    """Median water at the gauge for each forecast day, from the newest forecast on the site."""
    runs = sorted(p for p in web_data_dir().glob("[0-9]" * 8) if (p / "meta.json").exists())
    if not runs:
        return None
    meta = json.loads((runs[-1] / "meta.json").read_text())
    days = [(pd.Timestamp(d["start"]), float(d["water_in"]["q50"])) for d in meta.get("days", []) if d.get("available")]
    return {"run": pd.Timestamp(meta["run"]), "days": days} if days else None


# ---------------------------------------------------------------------------
# One hour of weather on the state grid
# ---------------------------------------------------------------------------

class Weather:
    def __init__(self, resort: Resort, layers: dict, rec: dict, model: Model | None, now: pd.Timestamp | None = None,
                 forecast: dict | None = None, daily_flow: pd.DataFrame | None = None):
        self.resort, self.layers, self.rec, self.model = resort, layers, rec, model
        self.now = pd.Timestamp(now) if now is not None else None           # hours after this are forecast
        self.forecast = forecast
        self.flow = daily_flow
        target = next(s for s in resort.snotel if s.get("target"))
        self.gauge_elev = target["elev_ft"] / 3.280839895
        self.gauge_rc = grid.gauge_cell(resort)
        elev = layers["elev"]
        zone_elev = elev[layers["zone"] > 0]
        self.ridge_elev, self.base_elev = float(np.quantile(zone_elev, 0.98)), float(zone_elev.min())
        self.bins = np.linspace(float(elev.min()), float(elev.max()), 48)
        self.slr = joblib.load(final.final_dir() / "slr.joblib")
        self.average = np.load(final.final_dir() / "hrrr_average_pattern.npy")
        self.water = rec["water"]["water_in"]
        self.provisional = rec["water"]["provisional"]
        self._shares: dict[pd.Timestamp, dict] = {}
        self._factors: dict[pd.Timestamp, float] = {}

    # -- how much water at the gauge ----------------------------------------------------------

    def _model_water(self, t: pd.Timestamp) -> float | None:
        m = self.model.hour(t) if self.model is not None else None
        return None if m is None else m["water_in"]

    def _forecast_water(self, t: pd.Timestamp) -> float | None:
        """The published median for the day, timed by the model's own hours."""
        if self.forecast is None:
            return None
        for start, total in self.forecast["days"]:
            if start < t <= start + pd.Timedelta(hours=24):
                if start not in self._shares:
                    hours = pd.date_range(start + pd.Timedelta(hours=1), periods=24, freq="h")
                    amounts = np.array([self._model_water(h) or 0.0 for h in hours])
                    share = amounts / amounts.sum() if amounts.sum() > 0.01 else np.full(24, 1.0 / 24.0)
                    self._shares[start] = dict(zip(hours, share))
                return total * self._shares[start].get(t, 0.0)
        return None

    def gauge_water(self, t: pd.Timestamp) -> tuple[float, str]:
        """(inches of water at the gauge in the hour ending at `t`, where the number came from)."""
        ahead = self.now is not None and t > self.now
        if not ahead and t in self.water.index and np.isfinite(self.water[t]) and not bool(self.provisional.get(t, False)):
            return float(self.water[t]), "gauge"
        m = self._model_water(t)
        if ahead:
            fc = self._forecast_water(t)
            return (fc, "median forecast") if fc is not None else (m, "HRRR") if m is not None else (0.0, "none")
        near = self.water.reindex([t - pd.Timedelta(hours=1), t, t + pd.Timedelta(hours=1)]).dropna()
        g = float(near.mean()) if len(near) else None
        if g is not None and m is not None:
            # a lone tick while the model is dry for hours either side is the raw gauge wobbling (it does so in the sun), not snow
            around = [self._model_water(t + pd.Timedelta(hours=k)) for k in range(-MODEL_DRY_REACH, MODEL_DRY_REACH + 1)]
            if g < LONE_TICK_IN and all(a is not None and a < MODEL_DRY_IN for a in around):
                return 0.0, "gauge (provisional) and HRRR"
            return 0.5 * (g + m), "gauge (provisional) and HRRR"
        return (g, "gauge (provisional)") if g is not None else (m, "HRRR") if m is not None else (0.0, "none")

    def _storm_liquid(self, t: pd.Timestamp) -> float:
        hours = pd.date_range(t - pd.Timedelta(hours=11), t + pd.Timedelta(hours=12), freq="h")
        got = self.water.reindex(hours).fillna(0.0).sum()
        return float(max(got, slr.MIN_LIQUID_IN))

    # -- the hour ------------------------------------------------------------------------------

    def _air(self, t: pd.Timestamp) -> dict:
        """Temperature profile and free-air wind for the hour: the model's, passed through the sensor."""
        m = self.model.hour(t) if self.model is not None else None
        water, water_source = self.gauge_water(t)
        water = water if water >= 0.001 else 0.0                # the cleaned gauge record leaves rounding dust
        obs_temp = self.rec["temp_c"].get(t, np.nan) if (self.now is None or t <= self.now) else np.nan
        sources = {"water": water_source}
        if m is not None:
            t_ref, t_km, tw_ref, tw_km = m["t_ref"], m["t_per_km"], m["tw_ref"], m["tw_per_km"]
            if np.isfinite(obs_temp):                       # the sensor is the truth at its own elevation: shift the model's profile to pass through it
                shift = (obs_temp + KELVIN) - float(at_elevation(t_ref, t_km, self.gauge_elev))
                t_ref, tw_ref = t_ref + shift, tw_ref + shift
                sources["temp"] = "SNOTEL sensor at mid-mountain; HRRR for the change with height"
            else:
                sources["temp"] = "HRRR"
            t700, t500, rh700, spd600, dir600 = m["t700"], m["t500"], m["rh700"], m["spd600"], m["dir600"]
            sources["wind"] = "HRRR free-air wind, corrected to ridge level"
        else:
            gauge_t = obs_temp if np.isfinite(obs_temp) else -6.0
            t_ref = gauge_t + KELVIN - STD_LAPSE_K_PER_KM * (self.gauge_elev - 3000.0) / 1000.0
            t_km = STD_LAPSE_K_PER_KM
            tw_ref, tw_km = t_ref - (0.7 if water > 0 else 2.5), STD_LAPSE_K_PER_KM
            t700, t500, rh700 = t_ref, t_ref - 22.0, 0.85 if water > 0 else 0.5
            dir600, spd600 = TYPICAL_WIND
            sources["temp"] = "SNOTEL sensor at mid-mountain; standard change with height" if np.isfinite(obs_temp) else "none: assumed"
            sources["wind"] = "none: a typical storm wind assumed"
            day = (t - pd.Timedelta(hours=8)).normalize()
            if self.flow is not None and day in self.flow.index:
                row = self.flow.loc[day]
                spd600, dir600 = float(np.hypot(row["u600"], row["v600"])), float(np.degrees(np.arctan2(-row["u600"], -row["v600"])) % 360.0)
                t700, t500 = float(row["t700"]) + KELVIN, float(row["t500"]) + KELVIN
                sources["wind"] = "ERA5 daily mean free-air wind (1.5 degree), corrected to ridge level"
        return {"m": m, "water": water, "obs_temp": obs_temp, "sources": sources, "t_ref": t_ref, "t_km": t_km, "tw_ref": tw_ref, "tw_km": tw_km,
                "t700": t700, "t500": t500, "rh700": rh700, "spd600": spd600, "dir600": dir600}

    def _ratio(self, a: dict, t: pd.Timestamp, elev_m: np.ndarray) -> np.ndarray:
        """Snow-to-liquid ratio as it falls at the given elevations, from the fitted model (layer 4)."""
        feats = pd.DataFrame({"t_site_c": at_elevation(a["t_ref"], a["t_km"], elev_m) - KELVIN, "tw_site_c": at_elevation(a["tw_ref"], a["tw_km"], elev_m) - KELVIN,
                              "t700_c": a["t700"] - KELVIN, "t500_c": a["t500"] - KELVIN, "spd600": a["spd600"], "rh700": a["rh700"],
                              "liquid": self._storm_liquid(t), "elev_m": elev_m})
        return slr.predict(self.slr, feats)

    def depth_factor(self, t: pd.Timestamp) -> float:
        """How the depth sensor says the day's new snow compares with the model's, as a bounded multiplier
        on snow depth (not on water): 1 when the sensor has nothing clear to say.

        The gauge gives water; the ratio model turns it into inches. The depth sensor beside the gauge
        measures the result. For each 24 hours from 5 am, the depth change the model expects at the
        gauge (its snow, settled by the fitted rates, on a settling pack) is set beside the change the
        sensor recorded, and the model's snow depth for those hours is scaled toward it."""
        start = (t - pd.Timedelta(hours=1) - pd.Timedelta(hours=DEPTH_DAY_START)).floor("D") + pd.Timedelta(hours=DEPTH_DAY_START)
        if start in self._factors:
            return self._factors[start]
        depth = self.rec["depth"]
        last = depth.dropna().index[-1] if depth.notna().any() else start
        end = min(start + pd.Timedelta(hours=24), last if self.now is None else min(last, self.now))
        factor = 1.0
        hours = pd.date_range(start + pd.Timedelta(hours=1), end, freq="h") if end > start else []
        d0, d1 = depth.get(start, np.nan), depth.get(end, np.nan)
        if len(hours) >= 6 and np.isfinite([d0, d1]).all():
            air = [self._air(h) for h in hours]
            water = np.array([a["water"] for a in air])
            if water.sum() >= DEPTH_MIN_WATER_IN:
                site = np.array([self.gauge_elev])
                share = np.array([float(snow_fraction(at_elevation(a["tw_ref"], a["tw_km"], self.gauge_elev) - KELVIN)) for a in air])
                ratio = np.array([float(self._ratio(a, h, site)[0]) if a["water"] > 0 else 13.0 for a, h in zip(air, hours)])
                temp = np.array([float(at_elevation(a["t_ref"], a["t_km"], self.gauge_elev) - KELVIN) for a in air])
                swe = self.rec["swe"].get(start, np.nan)
                rho = float(np.clip(swe / d0, 0.08, 0.5)) if np.isfinite(swe) and d0 >= 6 and swe > 0 else 0.3
                new, old = settle.simulate((water * share)[None, :], temp[None, :], ratio[None, :], np.array([d0]), np.array([rho]), *settle.fitted_rates())
                expected_new, pack_change = float(new[0]), float(old[0] - d0)
                if expected_new >= 1.5:
                    measured_new = float(d1 - d0) - pack_change           # what the sensor's change leaves for the new snow
                    factor = float(np.clip(measured_new / expected_new, *DEPTH_FACTOR_LIMITS))
        if self.now is None or start + pd.Timedelta(hours=24) <= min(last, self.now):
            self._factors[start] = factor                                 # a finished day: settled for good
        return factor

    def forcing(self, t: pd.Timestamp) -> tuple[Forcing, dict]:
        """The hour ending at `t` on the state grid, and the numbers at named places for the weather block."""
        t = pd.Timestamp(t)
        L, elev = self.layers, self.layers["elev"]
        a = self._air(t)
        m, water, obs_temp, sources = a["m"], a["water"], a["obs_temp"], a["sources"]
        t_ref, t_km, tw_ref, tw_km = a["t_ref"], a["t_km"], a["tw_ref"], a["tw_km"]
        temp = (at_elevation(t_ref, t_km, elev) - KELVIN).astype(np.float32)
        tw = at_elevation(tw_ref, tw_km, elev) - KELVIN
        share = snow_fraction(tw).astype(np.float32)

        if water > 0:
            dep = np.ones(elev.shape, dtype=np.float32)
            k = zones.REACH
            if m is not None and m["pattern"][k, k] >= HRRR_FLOOR_IN:
                d = np.clip((m["pattern"] / m["pattern"][k, k]) / self.average, 1 / ANOMALY_LIMIT, ANOMALY_LIMIT)
                dep = og.sample(d, L["hrrr_row"].ravel(), L["hrrr_col"].ravel()).reshape(elev.shape).astype(np.float32)
            everywhere = water * L["prism"] * dep ** SITE_DAMPING
            sensor = self.depth_factor(t) if (self.now is None or t <= self.now) else 1.0
            sources["depth"] = f"snow depth scaled by {sensor:.2f} to agree with the depth sensor" if sensor != 1.0 else "ratio model alone"
            ratio = (np.interp(elev, self.bins, self._ratio(a, t, self.bins)) * sensor).astype(np.float32)
            snow_water = (everywhere * share).astype(np.float32)
            snow, rain = snow_water * ratio, (everywhere * (1.0 - share)).astype(np.float32)
        else:
            snow_water = snow = np.zeros(elev.shape, dtype=np.float32)
            rain, ratio = 0.0, None
        ridge_ms = zones.RIDGE_WIND_SHARE * a["spd600"]
        ridge_from = zones.ridge_direction(a["dir600"])
        clear = 1.0 - m["cloud"] if m is not None and "cloud" in m else (0.0 if water > 0 else 0.5)
        f = Forcing(time=t, snow=snow, water=snow_water, temp_c=temp, rain=rain, ridge_wind_ms=float(ridge_ms), ridge_from=float(ridge_from),
                    wet_bulb_ridge_c=float(at_elevation(tw_ref, tw_km, self.ridge_elev) - KELVIN), clear=float(clear), sources=sources)
        r, c = self.gauge_rc
        at = lambda z: float(at_elevation(t_ref, t_km, z) - KELVIN)
        info = {"time": t, "water_in": float(water), "snow_in_hr": float(snow[r, c]) if water > 0 else 0.0,
                "ratio": float(ratio[r, c]) if ratio is not None else None,
                "rain_in_hr": float(np.asarray(rain)[r, c]) if water > 0 else 0.0,
                "temp_c": {"mid": at(self.gauge_elev), "ridge": at(self.ridge_elev), "base": at(self.base_elev)},
                "temp_measured": bool(np.isfinite(obs_temp)),
                "ridge": {"speed_ms": float(ridge_ms), "from_deg": float(ridge_from), "gust_ms": m.get("gust_ridge") if m else None},
                "mid": {"speed_ms": m.get("wind_mid") if m else None, "from_deg": m.get("wind_mid_from") if m else None, "gust_ms": m.get("gust_mid") if m else None},
                "cloud": m.get("cloud") if m else None, "vis_m": m.get("vis_m") if m else None,
                "model_run": m["run"] if m else None, "model_lead_h": m["lead"] if m else None, "sources": sources}
        return f, info


def daily_flow() -> pd.DataFrame | None:
    """ERA5 daily mean flow over Vail, for dates before the HRRR archive begins. On this computer
    it is where the Phase 2 build left it; the scheduled job gets a copy with the state's grid."""
    for p in (data_dir() / "upper" / "daily_flow.parquet", data_dir() / "state" / "daily_flow.parquet"):
        if p.exists():
            return pd.read_parquet(p)
    return None
