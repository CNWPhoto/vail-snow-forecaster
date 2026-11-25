"""Small, testable pieces of atmospheric arithmetic used for storm features."""
from __future__ import annotations

import numpy as np

G = 9.80665
RD = 287.05
CP = 1004.0
EPS = 0.622


def saturation_vapour_pressure(t_k):
    """Over liquid water, hPa (Bolton 1980)."""
    t_c = np.asarray(t_k, dtype=float) - 273.15
    return 6.112 * np.exp(17.67 * t_c / (t_c + 243.5))


def relative_humidity(q, t_k, p_hpa):
    """Relative humidity (0 to 1, with respect to liquid water) from specific humidity."""
    q = np.asarray(q, dtype=float)
    e = q * p_hpa / (EPS + (1.0 - EPS) * q)
    return np.clip(e / saturation_vapour_pressure(t_k), 0.0, 1.5)


def potential_temperature(t_k, p_hpa):
    return np.asarray(t_k, dtype=float) * (1000.0 / p_hpa) ** (RD / CP)


def lapse_rate(t_low, t_high, z_low, z_high):
    """Temperature drop per kilometre between two levels (positive = cooling with height)."""
    return (np.asarray(t_low, dtype=float) - t_high) / ((np.asarray(z_high, dtype=float) - z_low) / 1000.0)


def brunt_vaisala(t_low, t_high, p_low, p_high, z_low, z_high):
    """Dry static stability N (1/s) for a layer; 0 where the layer is unstable."""
    th_lo, th_hi = potential_temperature(t_low, p_low), potential_temperature(t_high, p_high)
    n2 = G / (0.5 * (th_lo + th_hi)) * (th_hi - th_lo) / (np.asarray(z_high, dtype=float) - z_low)
    return np.sqrt(np.clip(n2, 0.0, None))


def froude(speed, n, height_m):
    """Froude number U / (N h): above about 1 the air goes over the barrier, below it tends to be blocked."""
    n = np.asarray(n, dtype=float)
    return np.where(n > 1e-6, np.asarray(speed, dtype=float) / (np.maximum(n, 1e-6) * height_m), np.inf)


def level_of(value, profile, coord):
    """Where `profile` (ordered along `coord`) first crosses `value`, interpolated in `coord`.
    Returns NaN if it never does. Scans from the first element onward."""
    profile, coord = np.asarray(profile, dtype=float), np.asarray(coord, dtype=float)
    for i in range(len(profile) - 1):
        a, b = profile[i], profile[i + 1]
        if (a - value) * (b - value) <= 0 and a != b:
            return float(coord[i] + (value - a) / (b - a) * (coord[i + 1] - coord[i]))
    return float("nan")


def integrated_vapour_transport(q, u, v, p_hpa):
    """Magnitude of vertically integrated water-vapour transport (kg per metre per second)
    over the given pressure levels."""
    p = np.asarray(p_hpa, dtype=float) * 100.0
    order = np.argsort(p)
    q, u, v, p = (np.asarray(x, dtype=float)[order] for x in (q, u, v, p))
    qu, qv = np.trapezoid(q * u, p) / G, np.trapezoid(q * v, p) / G
    return float(np.hypot(qu, qv))


def vector_mean_direction(u, v, weights=None):
    """Direction the mean wind blows from, its vector-mean speed, and steadiness
    (vector-mean speed over mean speed: 1 when the wind never turns)."""
    u, v = np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    w = np.ones_like(u) if weights is None else np.asarray(weights, dtype=float)
    w = w / w.sum()
    um, vm = float((w * u).sum()), float((w * v).sum())
    mean_speed = float((w * np.hypot(u, v)).sum())
    vec = float(np.hypot(um, vm))
    return float(np.degrees(np.arctan2(-um, -vm)) % 360.0), vec, (vec / mean_speed if mean_speed > 0 else float("nan"))


def angle_diff(a, b):
    """Signed smallest difference a - b in degrees (positive = a is clockwise of b)."""
    return (np.asarray(a, dtype=float) - b + 180.0) % 360.0 - 180.0


LV = 2.501e6


def vapour_pressure_from_dewpoint(td_k):
    """Vapour pressure in hPa from dew point."""
    return saturation_vapour_pressure(td_k)


def wet_bulb(t_k, td_k, p_hpa, iterations: int = 30):
    """Wet-bulb temperature in kelvin from temperature, dew point and pressure.
    Solves the psychrometric equation e_s(Tw) - A p (T - Tw) = e by bisection between Td and T."""
    t, td = np.asarray(t_k, dtype=float), np.asarray(td_k, dtype=float)
    p = np.broadcast_to(np.asarray(p_hpa, dtype=float), np.broadcast(t, td).shape)
    td = np.minimum(td, t)
    e = saturation_vapour_pressure(td)
    a = CP / (EPS * LV)                       # psychrometric coefficient, per kelvin
    lo, hi = td.copy(), t.copy()
    for _ in range(iterations):
        mid = 0.5 * (lo + hi)
        f = saturation_vapour_pressure(mid) - a * p * (t - mid) - e
        hi = np.where(f > 0, mid, hi)
        lo = np.where(f > 0, lo, mid)
    return 0.5 * (lo + hi)


def standard_pressure(z_m):
    """Pressure in hPa at a height, standard atmosphere."""
    return 1013.25 * (1.0 - 2.25577e-5 * np.asarray(z_m, dtype=float)) ** 5.25588
