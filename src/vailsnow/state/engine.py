"""The mountain state and the rule that carries it forward one hour.

The state is what is on the ground in every cell: how deep the fresh layer is, how much of it
nobody has skied yet (kept separately for open ground and for ground under trees, because the
two are used up at very different speeds), the old pack underneath, and what the wind and sun
have done to the surface. `step` takes the state at the end of one hour and the weather of the
next hour and returns the state at the end of that hour. Nothing here reads a file or the
network, so the same code runs live, in a hindcast and in the tests.

Bookkeeping, all in inches:

    fresh, fresh_w     depth and water of the fresh layer
    open, trees        untracked depth on open ground and under trees (never more than `fresh`)
    base, base_w       depth and water of the old pack
    wind               net depth the wind has added to (+) or taken from (-) the fresh layer

Water enters only as snowfall and leaves only as melt. In between it moves from cell to cell
(wind) or from the fresh layer to the base (ageing) and is never created or lost, which is what
the tests check.

What uses fresh snow up, in the order applied each hour: wind, settling, ageing, skiers,
grooming. Every rate is a prior from the resort file, not a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd

from ..model import exposure, settle

HISTORY_HOURS = 168
HIST_SCALE = 100.0            # the new-snow history is stored in hundredths of an inch
TRACE_IN = 0.05               # an hour with less new snow than this does not count as accumulation
CRUST_NONE, CRUST_SUN, CRUST_MELT = 0, 1, 2
FLOAT_FIELDS = ("fresh", "fresh_w", "open", "trees", "base", "base_w", "wind", "hours_since", "sun_dose", "thaw", "cover")


@dataclass
class State:
    time: pd.Timestamp                    # end of the last hour stepped, UTC
    fresh: np.ndarray
    fresh_w: np.ndarray
    open: np.ndarray
    trees: np.ndarray
    base: np.ndarray
    base_w: np.ndarray
    wind: np.ndarray
    hours_since: np.ndarray               # hours since the cell last accumulated
    sun_dose: np.ndarray                  # clear, sunlit hours on the surface since it last snowed
    thaw: np.ndarray                      # degree-hours above freezing since the last hard freeze
    cover: np.ndarray                     # new snow since the present crust formed
    crust: np.ndarray                     # 0 none, 1 sun crust, 2 melt-freeze crust
    wet: np.ndarray                       # the surface has thawed or been rained on and not yet refrozen
    history: np.ndarray                   # (168, rows / k, cols / k): new snow in each of the last 168 hours
    last_groomed: pd.Timestamp | None = None
    melted: float = 0.0                   # water lost to melt since the state began, summed over cells (for the books)

    @property
    def shape(self) -> tuple[int, int]:
        return self.fresh.shape

    def copy(self) -> "State":
        return State(**{f.name: (getattr(self, f.name).copy() if isinstance(getattr(self, f.name), np.ndarray) else getattr(self, f.name)) for f in fields(self)})


def empty(time: pd.Timestamp, shape: tuple[int, int], history_block: int = 2, base_in: np.ndarray | float = 0.0, base_density: float = 0.28) -> State:
    """A state with no fresh snow, on a pack of the given depth."""
    z = lambda: np.zeros(shape, dtype=np.float32)
    base = np.broadcast_to(np.asarray(base_in, np.float32), shape).copy()
    return State(time=pd.Timestamp(time), fresh=z(), fresh_w=z(), open=z(), trees=z(), base=base, base_w=base * np.float32(base_density),
                 wind=z(), hours_since=np.full(shape, 999.0, dtype=np.float32), sun_dose=z(), thaw=z(), cover=z(),
                 crust=np.zeros(shape, dtype=np.uint8), wet=np.zeros(shape, dtype=bool),
                 history=np.zeros((HISTORY_HOURS, shape[0] // history_block, shape[1] // history_block), dtype=np.uint16))


@dataclass
class Forcing:
    """The weather of one hour. Fields are per cell unless marked as one number for the mountain."""
    time: pd.Timestamp                    # end of the hour, UTC
    snow: np.ndarray                      # new snow as it falls, before wind (in)
    water: np.ndarray                     # the water in that snow (in)
    temp_c: np.ndarray                    # air temperature
    rain: np.ndarray | float = 0.0        # liquid that fell as rain (in)
    ridge_wind_ms: float = 0.0            # sustained wind at ridge level, one number
    ridge_from: float = 270.0             # the direction it blows from
    wet_bulb_ridge_c: float = -8.0
    clear: float = 0.0                    # share of the sky that is clear, 0 to 1
    sources: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The ground, and who skis it
# ---------------------------------------------------------------------------

@dataclass
class Ground:
    """Static layers plus the per-cell rates worked out from the resort file."""
    layers: dict
    cell: float
    k_open: np.ndarray                    # share of untracked snow on open ground used up per lift-served hour, as a rate
    k_trees: np.ndarray
    groomed: np.ndarray                   # share of each cell's open ground that is groomed nightly
    opens: np.ndarray                     # first day each cell is open, as days since 1970 (a huge number where never)
    fade_open: np.ndarray                 # hours over which stale fresh snow fades into the base, open ground
    fade_trees: np.ndarray
    lat: float
    lon: float
    history_block: int
    _sun: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.k_open.shape

    def sun_index(self, day_of_year: int) -> np.ndarray:
        """Clear-sky sunlight on each cell over a day, relative to flat ground (kept for the day in hand)."""
        if day_of_year not in self._sun:
            self._sun.clear()
            self._sun[day_of_year] = exposure.sun_index(self.layers["slope"], self.layers["aspect"], self.lat, day_of_year).astype(np.float32)
        return self._sun[day_of_year]


def _ramp(x, lo: float, hi: float):
    return np.clip((np.asarray(x, np.float32) - lo) / (hi - lo), 0.0, 1.0)


def ground(layers: dict, config: dict, zone_groups: dict[int, str], cell: float | None = None) -> Ground:
    """Per-cell survival rates from terrain. `zone_groups` maps a zone index to its group name."""
    sv, age, ops = config["survival"], config["ageing"], config["operations"]
    cell = float(cell or config["cell"])
    tree_share, slope = layers["tree_share"], layers["slope"]
    # where people go: near mapped runs and lift tops, on slopes that are neither flat nor extreme
    reach = sv["far_share"] + (1.0 - sv["far_share"]) * np.exp(-layers["d_run"] / sv["trail_reach_m"])
    reach = reach * (0.6 + 0.4 * np.exp(-layers["d_top"] / sv["lift_reach_m"]))
    pitch = (0.3 + 0.7 * _ramp(slope, *sv["flat_deg"])) * (1.0 - 0.8 * _ramp(slope, *sv["steep_deg"]))
    traffic = reach * pitch
    # open ground: groomed, ungroomed run or bowl, mixed by how much of the cell's open ground each covers
    bowl = np.clip(1.0 - layers["groomed"] - layers["run"], 0.0, 1.0)
    k_open = (layers["groomed"] / sv["groomed"] + layers["run"] / sv["run"] + bowl / sv["bowl"]) * traffic
    # under trees: from glade to tight trees as the stand closes in, slower again where the trees are shaded
    tight = _ramp(tree_share, 0.3, 0.7) * (1.0 - _ramp(layers["glade_run"], 0.1, 0.5))
    hours = np.exp(np.log(sv["glade"]) * (1.0 - tight) + np.log(sv["trees"]) * tight)
    sun = exposure.sun_index(slope, layers["aspect"], float(layers["lat"]), 15)             # mid-January: 1 = flat ground, 0 = never lit
    shade = np.clip(1.0 - sun, 0.0, 1.0)
    k_trees = traffic / (hours * (1.0 + sv["shade_slows"] * shade))
    inside = layers["inside"]
    k_open = np.where(inside, k_open, pitch / sv["out_of_bounds"])
    k_trees = np.where(inside, k_trees, pitch / sv["out_of_bounds"])
    # opening day by zone group; ground inside the boundary but in no zone opens with the Front Side, outside it is never closed
    day = lambda s: int(pd.Timestamp(s).value // 86_400_000_000_000)
    first = min(day(v) for v in ops["opens"].values())
    opens = np.where(inside, first, 0).astype(np.int64)
    for index, group in zone_groups.items():
        opens[layers["zone"] == index] = day(ops["opens"][group]) if group in ops["opens"] else first
    fade_open = age["fade_hours"] / (1.0 + age["sun_speeds"] * np.clip(sun - 1.0, -0.5, 1.0))
    fade_trees = age["fade_hours"] + (age["shaded_trees_fade_hours"] - age["fade_hours"]) * np.clip(tree_share, 0, 1) * shade
    return Ground(layers=layers, cell=cell, k_open=k_open.astype(np.float32), k_trees=k_trees.astype(np.float32),
                  groomed=layers["groomed"].astype(np.float32), opens=opens, fade_open=fade_open.astype(np.float32),
                  fade_trees=fade_trees.astype(np.float32), lat=float(layers["lat"]), lon=float(layers["lon"]),
                  history_block=int(round(config["history_cell"] / cell)))


@dataclass(frozen=True)
class Lifts:
    """What the lifts are doing during one hour."""
    share: float          # share of the hour the lifts were turning
    crowd: float          # 1 on a weekday, more on weekends and holidays
    day: int              # the local date, as days since 1970
    groom: bool           # the groomers finish during this hour


def lifts(config: dict, time: pd.Timestamp) -> Lifts:
    """Lift hours, crowding and grooming for the hour ending at `time` (UTC)."""
    ops = config["operations"]
    end = pd.Timestamp(time, tz="UTC").tz_convert(ops["timezone"])
    start = end - pd.Timedelta(hours=1)
    date = start.tz_localize(None).normalize()
    day = int(date.value // 86_400_000_000_000)
    season = [pd.Timestamp(d) for d in ops["season"]]
    groom = start.hour == int(ops["groom_hour"]) and season[0] <= date <= season[1]
    if not season[0] <= date <= season[1]:
        return Lifts(0.0, 1.0, day, False)
    first, last = ops["extended_hours"] if date >= pd.Timestamp(ops["extended_from"]) else ops["hours"]
    a, b = start.hour + start.minute / 60.0, start.hour + start.minute / 60.0 + 1.0
    share = max(0.0, min(b, last) - max(a, first))
    crowd = ops["holiday"] if any(pd.Timestamp(lo) <= date <= pd.Timestamp(hi) for lo, hi in ops["holidays"]) else ops["weekend"] if date.day_of_week >= 5 else 1.0
    return Lifts(float(share), float(crowd), day, bool(groom))


# ---------------------------------------------------------------------------
# Sun
# ---------------------------------------------------------------------------

def sun_position(time: pd.Timestamp, lat: float, lon: float) -> tuple[float, float]:
    """(elevation, azimuth) of the sun in degrees at a UTC time. Azimuth is clockwise from north.
    Good to a fraction of a degree, which is all a crust rule needs."""
    t = pd.Timestamp(time)
    doy = t.day_of_year + (t.hour + t.minute / 60.0) / 24.0
    g = 2 * np.pi * (doy - 1) / 365.0
    decl = 0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g) + 0.000907 * np.sin(2 * g)
    eqt = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g) - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))     # minutes
    solar = (t.hour * 60 + t.minute + eqt + 4.0 * lon) / 60.0                 # local solar time, hours
    ha = np.radians(15.0 * (solar - 12.0))
    la = np.radians(lat)
    up = np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha)
    elev = np.arcsin(np.clip(up, -1, 1))
    az = np.arctan2(-np.sin(ha) * np.cos(decl), np.cos(la) * np.sin(decl) - np.sin(la) * np.cos(decl) * np.cos(ha))
    return float(np.degrees(elev)), float(np.degrees(az) % 360.0)


def sun_on_slope(slope_deg: np.ndarray, aspect_deg: np.ndarray, elev_deg: float, az_deg: float) -> np.ndarray:
    """How squarely the sun strikes each cell right now: 1 face on, 0 edge on or in its own shade."""
    if elev_deg <= 2.0:
        return np.zeros_like(slope_deg, dtype=np.float32)
    sl, asp, el, az = np.radians(slope_deg), np.radians(aspect_deg), np.radians(elev_deg), np.radians(az_deg)
    cosine = np.cos(sl) * np.sin(el) + np.sin(sl) * np.cos(el) * np.cos(az - asp)
    return np.clip(cosine, 0.0, 1.0).astype(np.float32)


def sun_times(day: pd.Timestamp, lat: float, lon: float) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Sunrise and sunset (UTC) on a local calendar day, for a flat horizon. Found by stepping
    through the day a minute at a time, which is plenty for a game's sky."""
    start = pd.Timestamp(day).normalize() - pd.Timedelta(hours=round(lon / 15.0))      # local midnight, near enough
    minutes = pd.date_range(start, periods=24 * 60, freq="min")
    up = np.array([sun_position(m, lat, lon)[0] > -0.833 for m in minutes])
    lit = np.nonzero(up)[0]
    return (minutes[lit[0]], minutes[lit[-1]]) if len(lit) else (start, start)


# ---------------------------------------------------------------------------
# One hour
# ---------------------------------------------------------------------------

def _sx(g: Ground, wind_from: float) -> np.ndarray:
    return g.layers["sx"][exposure.nearest_direction(wind_from)]


def _block_mean(a: np.ndarray, k: int) -> np.ndarray:
    h, w = a.shape[0] // k, a.shape[1] // k
    return a[: h * k, : w * k].reshape(h, k, w, k).mean(axis=(1, 3))


def slot(time: pd.Timestamp) -> int:
    return int(pd.Timestamp(time).value // 3_600_000_000_000) % HISTORY_HOURS


def step(s: State, f: Forcing, g: Ground, config: dict, rates: tuple[float, float, float] | None = None) -> State:
    """The state at the end of the hour described by `f`. `s` is left untouched."""
    new_rate, old_rate, self_rate = rates or settle.fitted_rates()
    age, wind_cfg, crust_cfg = config["ageing"], config["wind"], config["crust"]
    n = s.copy()
    n.time = pd.Timestamp(f.time)
    temp = np.broadcast_to(np.asarray(f.temp_c, np.float32), s.shape)
    snow = np.maximum(np.asarray(f.snow, np.float32), 0.0)
    water = np.maximum(np.asarray(f.water, np.float32), 0.0)
    layers = g.layers

    # 1. Snowfall, sorted by the wind as it lands: the lee of a ridge catches what the windward side loses.
    falling = float(snow.max()) > 0.0
    if falling:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(water > 0, snow / np.maximum(water, 1e-9), 0.0)
        mean_ratio = float(snow.sum() / max(float(water.sum()), 1e-9))
        strength = exposure.lee_strength(f.ridge_wind_ms, mean_ratio, f.wet_bulb_ridge_c)
        landed_w = exposure.lee_load(water.astype(np.float64), _sx(g, f.ridge_from), layers["ridge_distance"], layers["canopy"], layers["height"], strength).astype(np.float32)
        landed = landed_w * ratio
        n.fresh += landed
        n.fresh_w += landed_w
        n.open += landed
        n.trees += landed
        n.wind += landed - snow
        n.cover += landed
    else:
        landed = np.zeros(s.shape, dtype=np.float32)
    n.history[slot(f.time)] = np.clip(np.round(_block_mean(landed, g.history_block) * HIST_SCALE), 0, 65535).astype(np.uint16)
    snowing = landed >= TRACE_IN
    n.hours_since = np.where(snowing, 0.0, n.hours_since + 1.0).astype(np.float32)

    # 2. Wind moving snow that is already down. Only loose snow moves: recent, not crusted.
    with np.errstate(divide="ignore", invalid="ignore"):
        layer_ratio = float(n.fresh.sum() / max(float(n.fresh_w.sum()), 1e-9)) if float(n.fresh_w.sum()) > 0 else 14.0
    moving = exposure.mobility(f.ridge_wind_ms, layer_ratio, f.wet_bulb_ridge_c) * wind_cfg["hour_share"]
    if moving > 0 and float(n.fresh_w.max()) > 0:
        loose = np.exp(-n.hours_since / wind_cfg["hardens_hours"]) * (n.crust == CRUST_NONE)
        loose_w = (n.fresh_w * loose).astype(np.float64)
        moved_w = exposure.redistribute(loose_w, _sx(g, f.ridge_from), layers["canopy"], f.ridge_from, moving, g.cell)
        delta_w = (moved_w - loose_w).astype(np.float32)
        with np.errstate(divide="ignore", invalid="ignore"):
            keep = np.where(delta_w < 0, np.clip(1.0 + delta_w / np.maximum(n.fresh_w, 1e-9), 0.0, 1.0), 1.0)      # scoured cells keep this share
            density = np.where(n.fresh > 0, n.fresh_w / np.maximum(n.fresh, 1e-9), 0.0)
        drift = np.where(delta_w > 0, delta_w / np.maximum(density, 0.11), 0.0)                                    # wind-packed where it lands
        before = n.fresh.copy()
        n.fresh = n.fresh * keep + drift
        n.fresh_w = np.maximum(n.fresh_w + delta_w, 0.0)
        n.open = n.open * keep + drift
        n.trees = n.trees * keep + drift
        n.wind += n.fresh - before

    # 3. Settling, by the rate laws fitted to the depth sensors (model/settle.py), faster on sunlit slopes in clear weather.
    t = np.minimum(temp, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rho_new = np.where(n.fresh > 0, n.fresh_w / np.maximum(n.fresh, 1e-9), 0.0)
        rho_old = np.where(n.base > 0, n.base_w / np.maximum(n.base, 1e-9), 0.0)
    slow = np.where(rho_new > settle.RHO_SETTLED, np.exp(-46.0 * (rho_new - settle.RHO_SETTLED)), 1.0)
    day_of_year = int(pd.Timestamp(f.time).day_of_year)
    sunf = exposure.sun_factor(g.sun_index(day_of_year), f.clear) if f.clear > 0 else 1.0
    shrink = np.exp(-new_rate * sunf * np.exp(0.10 * t) * slow).astype(np.float32)
    for name in ("fresh", "open", "trees", "wind"):
        setattr(n, name, getattr(n, name) * shrink)
    load_cm = (n.fresh_w + 0.5 * n.base_w) * settle.CM_PER_IN
    n.base = (n.base * np.exp(-(old_rate * load_cm * np.exp(-21.0 * rho_old) + self_rate) * np.exp(0.08 * t))).astype(np.float32)

    # 3b. Melt, by degree-hours above freezing: the fresh layer first, then the pack. The water leaves the mountain.
    warm = np.maximum(temp, 0.0)
    if float(warm.max()) > 0:
        melt = (config["melt"]["in_per_degree_hour"] * warm).astype(np.float32)
        from_fresh = np.minimum(melt, n.fresh_w)
        with np.errstate(divide="ignore", invalid="ignore"):
            left = np.where(n.fresh_w > 0, 1.0 - from_fresh / np.maximum(n.fresh_w, 1e-9), 1.0).astype(np.float32)
        for name in ("fresh", "fresh_w", "open", "trees", "wind"):
            setattr(n, name, getattr(n, name) * left)
        from_base = np.minimum(melt - from_fresh, n.base_w)
        with np.errstate(divide="ignore", invalid="ignore"):
            left = np.where(n.base_w > 0, 1.0 - from_base / np.maximum(n.base_w, 1e-9), 1.0).astype(np.float32)
        n.base, n.base_w = n.base * left, n.base_w * left
        n.melted = n.melted + float((from_fresh + from_base).sum(dtype=np.float64))

    # 4. Ageing. Snow nobody has touched still stops skiing as fresh: after a while it fades into the base,
    #    sooner on open sunlit slopes, much later under shaded trees.
    stale = n.hours_since > age["fresh_hours"]
    if stale.any():
        share = layers["tree_share"]
        keep_open, keep_trees = np.exp(-1.0 / g.fade_open), np.exp(-1.0 / g.fade_trees)
        keep_layer = (1.0 - share) * keep_open + share * keep_trees
        gone, gone_w = np.where(stale, n.fresh * (1.0 - keep_layer), 0.0), np.where(stale, n.fresh_w * (1.0 - keep_layer), 0.0)
        n.fresh, n.fresh_w = n.fresh - gone, n.fresh_w - gone_w
        n.base, n.base_w = n.base + gone, n.base_w + gone_w
        n.open = np.where(stale, n.open * keep_open, n.open)
        n.trees = np.where(stale, n.trees * keep_trees, n.trees)
        n.wind = np.where(stale, n.wind * keep_layer, n.wind)

    # 5. Skiers, while the lifts turn, on terrain that is open. Closed terrain keeps what it has.
    #    Ground outside the ski-area boundary is never closed, but sees almost nobody.
    lift = lifts(config, f.time)
    if lift.share > 0:
        is_open = g.opens <= lift.day
        n.open = np.where(is_open, n.open * np.exp(-g.k_open * lift.share * lift.crowd), n.open)
        n.trees = np.where(is_open, n.trees * np.exp(-g.k_trees * lift.share * lift.crowd), n.trees)

    # 6. Grooming: the groomed share of each cell's open ground is reset to corduroy.
    if lift.groom:
        n.open = np.where(g.opens <= lift.day, n.open * (1.0 - g.groomed), n.open)
        n.last_groomed = pd.Timestamp(f.time)

    # 7. Crust on the surface. New snow buries it.
    rain = np.broadcast_to(np.asarray(f.rain, np.float32), s.shape)
    n.thaw = np.where(temp > 0, n.thaw + temp, np.where(temp < -3.0, 0.0, n.thaw)).astype(np.float32)
    n.wet = n.wet | (n.thaw >= crust_cfg["thaw_degree_hours"]) | (rain > 0.02)
    froze = n.wet & (temp <= crust_cfg["refreeze_c"])
    n.crust = np.where(froze, CRUST_MELT, n.crust).astype(np.uint8)
    n.wet = n.wet & ~froze
    n.thaw = np.where(froze, 0.0, n.thaw).astype(np.float32)
    elev_deg, az_deg = sun_position(pd.Timestamp(f.time) - pd.Timedelta(minutes=30), g.lat, g.lon)
    if f.clear > 0.5 and elev_deg > 2.0:
        lit = sun_on_slope(layers["slope"], layers["aspect"], elev_deg, az_deg) * (1.0 - np.clip(layers["canopy"], 0, 1))
        n.sun_dose = np.where(temp >= crust_cfg["sun_min_temp_c"], n.sun_dose + f.clear * lit, n.sun_dose).astype(np.float32)
    n.sun_dose = np.where(snowing, 0.0, n.sun_dose).astype(np.float32)
    baked = (n.sun_dose >= crust_cfg["sun_dose"]) & (n.crust == CRUST_NONE)
    n.crust = np.where(baked, CRUST_SUN, n.crust).astype(np.uint8)
    n.cover = np.where(froze | baked, 0.0, n.cover).astype(np.float32)
    buried = (n.crust != CRUST_NONE) & (n.cover >= crust_cfg["bury_in"])
    n.crust = np.where(buried, CRUST_NONE, n.crust).astype(np.uint8)

    # untracked snow is part of the fresh layer, and nothing is ever negative
    n.fresh, n.fresh_w = np.maximum(n.fresh, 0.0), np.maximum(n.fresh_w, 0.0)
    n.open, n.trees = np.clip(n.open, 0.0, n.fresh), np.clip(n.trees, 0.0, n.fresh)
    n.base, n.base_w = np.maximum(n.base, 0.0), np.maximum(n.base_w, 0.0)
    for name in FLOAT_FIELDS:
        setattr(n, name, getattr(n, name).astype(np.float32, copy=False))
    return n


# ---------------------------------------------------------------------------
# Reading the state
# ---------------------------------------------------------------------------

def new_snow(s: State, hours: int, shape: tuple[int, int] | None = None) -> np.ndarray:
    """New snow over the last `hours` hours (as it fell and was loaded by wind, before settling), in inches."""
    last = slot(s.time)
    idx = [(last - k) % HISTORY_HOURS for k in range(min(hours, HISTORY_HOURS))]
    total = s.history[idx].sum(axis=0, dtype=np.float64) / HIST_SCALE
    shape = shape or s.shape
    k = shape[0] // total.shape[0]
    return np.repeat(np.repeat(total, k, axis=0), k, axis=1)[: shape[0], : shape[1]].astype(np.float32) if k > 1 else total.astype(np.float32)


def tracked(s: State) -> tuple[np.ndarray, np.ndarray]:
    """Share of the fresh layer that has been skied, on open ground and under trees. Where there is
    no fresh layer to speak of the surface counts as fully tracked."""
    has = s.fresh > 0.1
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.where(has, 1.0 - np.clip(s.open / np.maximum(s.fresh, 1e-6), 0, 1), 1.0).astype(np.float32),
                np.where(has, 1.0 - np.clip(s.trees / np.maximum(s.fresh, 1e-6), 0, 1), 1.0).astype(np.float32))


def density(s: State) -> np.ndarray:
    """Density of the fresh layer in kg per cubic metre (water is 1000). NaN where there is none."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(s.fresh > 0.1, 1000.0 * s.fresh_w / np.maximum(s.fresh, 1e-6), np.nan).astype(np.float32)


def wind_effect(s: State) -> np.ndarray:
    """-1 scoured bare of its new snow, 0 untouched, +1 holding twice what fell there."""
    fell = np.maximum(s.fresh - s.wind, 0.5)
    return np.clip(s.wind / fell, -1.0, 1.0).astype(np.float32)


def untracked(s: State, g: Ground) -> np.ndarray:
    """Fresh untracked depth averaged over each cell's open ground and trees."""
    share = g.layers["tree_share"]
    return ((1.0 - share) * s.open + share * s.trees).astype(np.float32)


def save(s: State, path) -> None:
    out = {name: getattr(s, name).astype(np.float32 if name in ("base", "base_w") else np.float16) for name in FLOAT_FIELDS}
    out.update(crust=s.crust, wet=s.wet, history=s.history, time=np.array(str(s.time)), melted=np.array(s.melted), last_groomed=np.array(str(s.last_groomed) if s.last_groomed is not None else ""))
    np.savez_compressed(path, **out)


def load(path) -> State:
    with np.load(path) as z:
        groomed = str(z["last_groomed"])
        return State(time=pd.Timestamp(str(z["time"])), crust=z["crust"], wet=z["wet"], history=z["history"],
                     last_groomed=pd.Timestamp(groomed) if groomed else None, melted=float(z["melted"]) if "melted" in z.files else 0.0,
                     **{name: z[name].astype(np.float32) for name in FLOAT_FIELDS})
