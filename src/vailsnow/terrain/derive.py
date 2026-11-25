"""Terrain layers derived from an elevation grid.

Convention used everywhere: arrays are (rows, cols) with row 0 at the NORTH
edge and column 0 at the WEST edge. Angles are degrees clockwise from north.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


def slope_aspect(z: np.ndarray, cell: float) -> tuple[np.ndarray, np.ndarray]:
    """Slope (degrees from horizontal) and aspect (the compass direction the slope faces)."""
    d_row, d_col = np.gradient(z.astype(np.float64), cell)
    dz_east, dz_north = d_col, -d_row
    slope = np.degrees(np.arctan(np.hypot(dz_east, dz_north)))
    aspect = np.degrees(np.arctan2(-dz_east, -dz_north)) % 360.0   # downhill direction
    return slope.astype(np.float32), aspect.astype(np.float32)


def curvature(z: np.ndarray, cell: float, smooth_m: float = 15.0) -> np.ndarray:
    """Surface curvature in 1/100 m. Positive on ridges and knolls, negative in gullies and bowls."""
    zs = ndi.gaussian_filter(z.astype(np.float64), smooth_m / cell)
    return (-100.0 * ndi.laplace(zs) / (cell * cell)).astype(np.float32)


def tpi(z: np.ndarray, cell: float, radius_m: float) -> np.ndarray:
    """Topographic position index: height above the mean of the surrounding `radius_m` (metres)."""
    size = 2 * int(round(radius_m / cell)) + 1
    return (z - ndi.uniform_filter(z.astype(np.float64), size, mode="nearest")).astype(np.float32)


def _receivers(z: np.ndarray, uphill: bool) -> np.ndarray:
    """Flat index of the steepest neighbour (down, or up when `uphill`) for each cell; own index if none."""
    zz = -z if uphill else z
    h, w = zz.shape
    pad = np.pad(zz.astype(np.float64), 1, mode="constant", constant_values=np.inf)
    best = np.zeros((h, w))
    recv = np.arange(h * w).reshape(h, w)
    idx = recv.copy()
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nb = pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
            drop = (zz - nb) / np.hypot(dr, dc)
            better = drop > best
            best = np.where(better, drop, best)
            recv = np.where(better, idx + dr * w + dc, recv)
    return recv.ravel()


def accumulation(z: np.ndarray, uphill: bool = False) -> np.ndarray:
    """Number of cells draining through each cell (D8). With `uphill`, the same on the
    upside-down surface, which concentrates along ridge crests instead of valley floors."""
    recv = _receivers(z, uphill)
    zz = (-z if uphill else z).ravel()
    order = np.argsort(-zz, kind="stable")          # highest first
    acc = np.ones(zz.size, dtype=np.int64)
    recv_l, acc_l = recv.tolist(), acc.tolist()
    for i in order.tolist():
        r = recv_l[i]
        if r != i:
            acc_l[r] += acc_l[i]
    return np.asarray(acc_l, dtype=np.int64).reshape(z.shape)


def ridge_mask(z: np.ndarray, cell: float, min_area_m2: float = 30000.0, smooth_m: float = 30.0) -> np.ndarray:
    """Ridge crests: where at least `min_area_m2` of terrain 'drains' upward into the cell, and
    the cell stands above its surroundings."""
    zs = ndi.gaussian_filter(z.astype(np.float64), smooth_m / cell)
    acc = accumulation(zs, uphill=True)
    return (acc * cell * cell >= min_area_m2) & (tpi(zs, cell, 150.0) > 0)


def valley_mask(z: np.ndarray, cell: float, min_area_m2: float = 1.0e6, smooth_m: float = 30.0) -> np.ndarray:
    """Main drainage lines: cells that at least `min_area_m2` of terrain drains through."""
    zs = ndi.gaussian_filter(z.astype(np.float64), smooth_m / cell)
    return accumulation(zs) * cell * cell >= min_area_m2


def distance_to(mask: np.ndarray, cell: float) -> np.ndarray:
    """Distance in metres from each cell to the nearest True cell of `mask`."""
    if not mask.any():
        return np.full(mask.shape, np.inf, dtype=np.float32)
    return (ndi.distance_transform_edt(~mask) * cell).astype(np.float32)


def hillshade(z: np.ndarray, cell: float, azimuth: float = 315.0, altitude: float = 45.0) -> np.ndarray:
    """Shaded relief, 0 to 1, lit from `azimuth`."""
    slope, aspect = slope_aspect(z, cell)
    s, a = np.radians(slope), np.radians(aspect)
    alt, az = np.radians(altitude), np.radians(azimuth)
    shade = np.sin(alt) * np.cos(s) + np.cos(alt) * np.sin(s) * np.cos(az - a)
    return np.clip(shade, 0.0, 1.0).astype(np.float32)
