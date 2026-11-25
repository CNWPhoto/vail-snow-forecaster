"""Layer 2 physics: the linear theory of orographic precipitation (Smith and Barstad 2004,
J. Atmos. Sci. 61, 1377-1391), written from the equations in the paper.

Air forced up a slope condenses water in proportion to the lift. The theory adds two things a
simple upslope rule lacks: the lift weakens with height above the ground according to the
stability of the air, and the condensed water takes time to become snow and time to fall, so it
lands downwind of where it formed. Everything is solved at once with a Fourier transform of
the terrain. It returns the orographic part only, which is negative in the lee; a background
rate has to be added and the sum clipped at zero.
"""
from __future__ import annotations

import numpy as np

from ..upper.physics import CP, EPS, G, RD, saturation_vapour_pressure

RV = 461.5
LV = 2.5e6


def moist_adiabatic_lapse(t_k: float, p_hpa: float) -> float:
    """Saturated adiabatic lapse rate, K per metre (positive)."""
    rs = EPS * saturation_vapour_pressure(t_k) / (p_hpa - saturation_vapour_pressure(t_k))
    return float(G * (1.0 + LV * rs / (RD * t_k)) / (CP + LV ** 2 * rs * EPS / (RD * t_k ** 2)))


def layer_depth(t_mean_k: float, p_low: float = 700.0, p_high: float = 500.0) -> float:
    """Thickness in metres of the layer between two pressure levels."""
    return float(RD * t_mean_k / G * np.log(p_low / p_high))


def parameters(t_k: float, t_drop_700_500: float, p_hpa: float = 700.0) -> dict:
    """The theory's three air-mass numbers from a temperature and the 700 to 500 mb temperature drop:
    `cw` uplift sensitivity (kg/m3), `hw` water-vapour scale height (m), `nm` moist stability (1/s)."""
    gamma = max(t_drop_700_500 / layer_depth(t_k - 0.5 * t_drop_700_500), 3.0e-3)       # K/m, kept away from zero
    gm = moist_adiabatic_lapse(t_k, p_hpa)
    rho_sat = saturation_vapour_pressure(t_k) * 100.0 / (RV * t_k)
    n2 = G / t_k * (gm - gamma)
    return {"cw": float(rho_sat * gm / gamma), "hw": float(RV * t_k ** 2 / (LV * gamma)),
            "nm": float(np.sqrt(np.clip(n2, 2.0e-3 ** 2, 1.5e-2 ** 2))), "gamma": float(gamma), "gamma_m": gm}


def taper(shape: tuple[int, int], frac: float = 0.1) -> np.ndarray:
    """Cosine edge taper so the terrain falls to zero at the borders (the transform is periodic)."""
    def edge(n: int) -> np.ndarray:
        w = np.ones(n)
        m = max(int(n * frac), 1)
        ramp = 0.5 * (1 - np.cos(np.pi * (np.arange(m) + 0.5) / m))
        w[:m], w[-m:] = ramp, ramp[::-1]
        return w
    return np.outer(edge(shape[0]), edge(shape[1]))


class Terrain:
    """A terrain grid prepared for repeated solves. `height` has row 0 at the north edge."""

    def __init__(self, height: np.ndarray, cell: float):
        h = np.asarray(height, dtype=float)[::-1]                # flip so y increases with row
        self.shape, self.cell = h.shape, float(cell)
        self.h_hat = np.fft.fft2((h - h.mean()) * taper(h.shape))
        k = 2 * np.pi * np.fft.fftfreq(h.shape[1], cell)
        l = 2 * np.pi * np.fft.fftfreq(h.shape[0], cell)
        self.k, self.l = np.meshgrid(k, l)

    def precipitation(self, u: float, v: float, cw: float, hw: float, nm: float, tau_c: float = 1000.0, tau_f: float = 1000.0) -> np.ndarray:
        """Orographic precipitation rate in mm per hour (negative where the air descends).
        `u`, `v` are the wind toward east and north (m/s); `tau_c`, `tau_f` the conversion and
        fallout times (s). Returned with row 0 at the north edge, like the input."""
        sigma = u * self.k + v * self.l
        safe = np.where(np.abs(sigma) < 1e-12, 1e-12, sigma)
        m2 = (nm ** 2 - safe ** 2) / safe ** 2 * (self.k ** 2 + self.l ** 2)
        m = np.where(m2 >= 0, np.sign(safe) * np.sqrt(np.abs(m2)), 1j * np.sqrt(np.abs(m2)))
        p_hat = cw * 1j * sigma * self.h_hat / ((1 - 1j * m * hw) * (1 + 1j * sigma * tau_c) * (1 + 1j * sigma * tau_f))
        p_hat[np.abs(sigma) < 1e-12] = 0.0
        return (np.real(np.fft.ifft2(p_hat)) * 3600.0)[::-1]


def sample(field: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Bilinear sample of a grid at fractional (row, col) positions."""
    r = np.clip(np.asarray(rows, float), 0, field.shape[0] - 1.001)
    c = np.clip(np.asarray(cols, float), 0, field.shape[1] - 1.001)
    r0, c0 = r.astype(int), c.astype(int)
    fr, fc = r - r0, c - c0
    return (field[r0, c0] * (1 - fr) * (1 - fc) + field[r0, c0 + 1] * (1 - fr) * fc
            + field[r0 + 1, c0] * fr * (1 - fc) + field[r0 + 1, c0 + 1] * fr * fc)
