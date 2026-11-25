"""Layers 5 and 6 (part two): wind redistribution and sun exposure on the 10 m grid.

Neither can be checked against a measurement: nothing records how snow is spread within the
ski area. The rules here are standard physics with stated, conservative constants, kept as a
separate toggle so the map can show "as fallen" beside "after wind".

Wind. Each cell has a shelter angle for the wind direction (Winstral's Sx from Phase 1: the
steepest angle up to terrain within 300 m upwind). A cell that looks down on its upwind terrain
is exposed and loses snow; a cell tucked behind higher ground is sheltered and gains it. Trees
stop the wind picking snow up. Snow removed is carried a short way downwind and laid down in
sheltered cells, and the total is conserved.

Sun. Clear-sky direct sunlight on each cell's slope and aspect over a day, relative to flat
ground, used to speed the settling of new snow after a storm clears.
"""
from __future__ import annotations

import numpy as np
import rasterio
from scipy.ndimage import gaussian_filter, shift

from ..resort import Resort
from ..terrain.shelter import DIRECTIONS_16

THRESHOLD_MS = 5.0            # ridge-level wind below which fresh snow stays put
FULL_MS = 20.0                # wind at which transport reaches its cap
MAX_LOSS = 0.40               # most a fully exposed, treeless cell can lose
MAX_GAIN = 0.80               # most a drift can add, as a share of what fell on the cell
EXPOSED_DEG = 10.0            # shelter angle (either sign) treated as fully exposed or fully sheltered
CARRY_M = 150.0               # how far downwind eroded snow travels before settling out
SPREAD_M = 80.0
SUN_GAIN = 0.6                # extra new-snow settling rate per unit of relative sunlight above flat ground


def mobility(speed_ms: float, snow_to_liquid: float = 14.0, wet_bulb_c: float = -8.0) -> float:
    """Share of the cap that can move: 0 below the threshold, 1 in a gale. Light snow moves more
    easily than dense snow, and snow near freezing sticks."""
    base = float(np.clip((speed_ms - THRESHOLD_MS) / (FULL_MS - THRESHOLD_MS), 0.0, 1.0))
    density = float(np.clip(snow_to_liquid / 14.0, 0.6, 1.3))
    sticky = float(np.clip((-wet_bulb_c) / 2.0, 0.3, 1.0))
    return min(base * density * sticky, 1.0)


def exposure_score(sx_deg: np.ndarray) -> np.ndarray:
    """+1 fully exposed to the wind, -1 fully sheltered, from the shelter angle."""
    return np.clip(-np.asarray(sx_deg, float) / EXPOSED_DEG, -1.0, 1.0)


def nearest_direction(wind_from: float) -> int:
    """Index of the stored 22.5 degree sector nearest a wind direction."""
    d = np.abs((np.asarray(DIRECTIONS_16) - wind_from + 180.0) % 360.0 - 180.0)
    return int(np.argmin(d))


def redistribute(snow: np.ndarray, sx_deg: np.ndarray, canopy: np.ndarray, wind_from: float, moving: float, cell: float = 10.0) -> np.ndarray:
    """Snow depth after wind. `snow` and the terrain layers share a grid with row 0 at the north.
    `moving` is the output of `mobility`. Cells outside the snow field (NaN) are left out."""
    inside = np.isfinite(snow)
    s = np.where(inside, snow, 0.0)
    score = np.nan_to_num(exposure_score(sx_deg))
    trees = np.clip(np.nan_to_num(canopy), 0.0, 1.0)
    loss = MAX_LOSS * moving * np.clip(score, 0.0, None) * (1.0 - trees) * s
    lifted = float(loss.sum())
    if lifted <= 0:
        return np.where(inside, s, np.nan)
    # carry it downwind: wind FROM `wind_from` moves snow toward the opposite bearing
    toward = np.radians(wind_from + 180.0)
    d_row, d_col = -np.cos(toward) * CARRY_M / cell, np.sin(toward) * CARRY_M / cell
    airborne = gaussian_filter(shift(loss, (d_row, d_col), order=1, mode="constant"), SPREAD_M / cell)
    # sheltered cells take it; open ground takes it more readily than thick forest
    catch = (np.clip(-score, 0.0, None) + 0.05) * (1.0 - 0.5 * trees) * inside
    gain = catch * airborne / np.maximum(gaussian_filter(catch, SPREAD_M / cell), 1e-6)
    # no drift deeper than MAX_GAIN times what fell there; what a full cell cannot hold goes to the rest
    cap = MAX_GAIN * s
    for _ in range(4):
        gain = np.minimum(gain * lifted / max(float(gain.sum()), 1e-9), cap)
        if lifted - gain.sum() < 1e-6 * lifted:
            break
    room = cap - gain
    gain += room * (lifted - gain.sum()) / max(float(room.sum()), 1e-9)       # exact conservation
    return np.where(inside, s - loss + gain, np.nan)


# ---------------------------------------------------------------------------
# Lee loading during the storm
#
# `redistribute` above moves snow that has already fallen a short way. It cannot produce what
# people who ski the mountain see: slopes just over a ridge from the wind holding far more
# than the windward side, storm after storm. That comes from snow being carried over the crest
# while it falls and dropped where the wind slackens. Nothing public measures it. The constants
# below are tuned to the user's account of Vail (2026-11-24): east- and north-facing slopes just
# below a ridge hold about 30% more until the slope eases toward flat, and named spots
# (Northwoods, Kangaroo Cornice, Lover's Leap, the ridge under the Tea Cup lift) about twice the
# windward side; "if the mountain reports 10 in, those spots have about 13". Ridge-level wind on
# storm days blows from the southwest or west 78% of the time. With these constants and a typical
# storm wind, open east- and north-facing slopes within 100 m of a ridge come out at 12.5 to 13.2 in
# from a base of 10, the windward side of the same ridges at 7.8 to 9.0, Northwoods at 11.7 to 14.1,
# Kangaroo Cornice at 11.8 to 12.1 and the top of Lover's Leap at 12.2 to 12.4.
# ---------------------------------------------------------------------------

LEE_FULL_DEG = 15.0           # shelter angle at which a slope counts as fully in the lee (or, negative, fully exposed)
LEE_GAIN = 0.9                # extra snow on a fully lee, open slope at the ridge in a typical storm wind (tuned, see above)
LEE_SCOUR = 0.3               # share stripped from a fully exposed, open slope at the ridge in the same wind (tuned)
RIDGE_REACH_M = 150.0         # loading and scouring fade with distance from the nearest ridge line over about this far
LEE_CALM_MS = 2.0             # sustained ridge wind below which snow falls where it falls
LEE_TYPICAL_MS = 6.3          # the median sustained ridge wind on storm days: strength 1
LEE_MAX_STRENGTH = 1.6
LOW_SHARE = 0.4               # the bottom of the mountain feels this share of the ridge-top effect


def lee_strength(ridge_wind_ms: float, snow_to_liquid: float = 14.0, wet_bulb_c: float = -8.0) -> float:
    """How strongly the wind sorts falling snow: 0 in calm, 1 in a typical storm wind, more in a
    gale. Light cold snow is carried further than dense or sticky snow."""
    wind = float(np.clip((ridge_wind_ms - LEE_CALM_MS) / (LEE_TYPICAL_MS - LEE_CALM_MS), 0.0, LEE_MAX_STRENGTH))
    carry = mobility(FULL_MS, snow_to_liquid, wet_bulb_c) / mobility(FULL_MS, 14.0, -8.0)
    return wind * float(np.clip(carry, 0.3, 1.2))


def lee_factor(sx_deg: np.ndarray, ridge_distance_m: np.ndarray, canopy: np.ndarray, height: np.ndarray, strength: float) -> np.ndarray:
    """Multiplier on falling snow for one wind direction, before the total is put back: above 1 in
    the lee below a ridge, below 1 on the exposed side. `height` is 0 at the bottom of the
    mountain and 1 at the top. Trees stop scouring entirely and halve the loading."""
    lee = np.clip(np.nan_to_num(sx_deg) / LEE_FULL_DEG, -1.0, 1.0)
    near = 1.0 / (1.0 + (np.nan_to_num(ridge_distance_m, nan=1e4) / RIDGE_REACH_M) ** 2)
    reach = strength * near * (LOW_SHARE + (1.0 - LOW_SHARE) * np.clip(np.nan_to_num(height), 0.0, 1.0))
    cover = np.clip(np.nan_to_num(canopy), 0.0, 1.0)
    gain = LEE_GAIN * np.maximum(lee, 0.0) * (1.0 - 0.5 * cover)
    loss = LEE_SCOUR * np.maximum(-lee, 0.0) * (1.0 - cover)
    return np.maximum(1.0 + reach * (gain - loss), 0.2)


def lee_load(snow: np.ndarray, sx_deg: np.ndarray, ridge_distance_m: np.ndarray, canopy: np.ndarray, height: np.ndarray, strength: float) -> np.ndarray:
    """Snow after in-storm lee loading. The total over the field is unchanged: what the lee gains, the exposed side loses."""
    if strength <= 0:
        return snow
    inside = np.isfinite(snow)
    out = np.where(inside, snow * lee_factor(sx_deg, ridge_distance_m, canopy, height, strength), np.nan)
    total = np.nansum(out)
    return out * (np.nansum(snow) / total) if total > 0 else snow


def load_layers(resort: Resort) -> dict[str, np.ndarray]:
    """The terrain layers the two rules need, from the Phase 1 build."""
    d = resort.out_dir()
    with rasterio.open(d / "terrain_10m.tif") as ds:
        names = list(ds.descriptions)
        out = {k: ds.read(names.index(k) + 1).astype(np.float32) for k in ("elevation", "slope", "aspect", "canopy_cover", "ridge_distance")}
    with rasterio.open(d / "zones_10m.tif") as ds:
        out["zone"], out["band"] = ds.read(1), ds.read(2)
    z = np.where(out["zone"] > 0, out["elevation"], np.nan)
    out["height"] = np.clip((out["elevation"] - np.nanmin(z)) / (np.nanmax(z) - np.nanmin(z)), 0.0, 1.0)       # 0 at the base, 1 at the top
    return out


def load_sx(resort: Resort, wind_from: float) -> np.ndarray:
    with rasterio.open(resort.out_dir() / "sx_300m.tif") as ds:
        return ds.read(nearest_direction(wind_from) + 1).astype(np.float32) / 100.0


def sun_index(slope_deg: np.ndarray, aspect_deg: np.ndarray, lat_deg: float, day_of_year: int) -> np.ndarray:
    """Clear-sky direct sunlight a slope receives over the day, relative to flat ground at the
    same place (1 = flat, 0 = never lit). Self-shading only: shadows cast by other terrain are not traced."""
    lat = np.radians(lat_deg)
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + day_of_year) / 365.0)
    slope, aspect = np.radians(slope_deg), np.radians(aspect_deg)
    nx, ny, nz = np.sin(slope) * np.sin(aspect), np.sin(slope) * np.cos(aspect), np.cos(slope)       # east, north, up
    on_slope, on_flat = np.zeros_like(nz, dtype=np.float64), 0.0
    for hour_angle in np.radians(np.arange(-112.5, 113.0, 7.5)):                                     # every half hour
        up = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(hour_angle)
        if up <= 0.02:
            continue
        east = -np.cos(decl) * np.sin(hour_angle)
        north = np.cos(lat) * np.sin(decl) - np.sin(lat) * np.cos(decl) * np.cos(hour_angle)
        on_slope += np.clip(nx * east + ny * north + nz * up, 0.0, None)
        on_flat += up
    return on_slope / on_flat


def sun_factor(index: np.ndarray | float, clear_share: float) -> np.ndarray:
    """Multiplier on the new-snow settling rate: 1 under cloud or on flat ground, higher on
    sunlit slopes in clear weather, lower on shaded ones."""
    return np.clip(1.0 + SUN_GAIN * clear_share * (np.asarray(index, float) - 1.0), 0.5, 2.0)


def by_zone_band(field: np.ndarray, zone: np.ndarray, band: np.ndarray, resort: Resort) -> dict[tuple[str, str], dict]:
    """Mean and spread of a 10 m field over each zone and elevation band."""
    out = {}
    for z in resort.zones:
        for b in resort.bands:
            sel = (zone == z.index) & (band == b.index) & np.isfinite(field)
            if sel.sum() >= 50:
                v = field[sel]
                out[(z.id, b.id)] = {"mean": float(v.mean()), "p10": float(np.quantile(v, 0.1)), "p90": float(np.quantile(v, 0.9))}
    return out
