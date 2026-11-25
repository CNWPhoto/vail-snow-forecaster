"""Directional shelter and exposure terms.

Sx (Winstral et al. 2002, "maximum upwind slope"): for wind arriving FROM a
given compass direction, the steepest angle from a cell up to any terrain
within `dmax` metres upwind. Positive means higher ground stands upwind and the
cell is sheltered (snow tends to deposit). Negative means the cell looks down
on everything upwind and is exposed (snow tends to scour).

The same search with a long reach and negative values clipped gives the
horizon angle, from which sky view is computed.
"""
from __future__ import annotations

import numpy as np

DIRECTIONS_16 = tuple(float(a) for a in np.arange(0.0, 360.0, 22.5))


def _sample(zp: np.ndarray, pad: int, shape: tuple[int, int], d_row: float, d_col: float) -> np.ndarray:
    """Bilinear sample of the padded grid at (row + d_row, col + d_col) for every cell."""
    h, w = shape
    r0, c0 = int(np.floor(d_row)), int(np.floor(d_col))
    fr, fc = d_row - r0, d_col - c0
    out = np.zeros((h, w), dtype=np.float32)
    for rr, wr in ((r0, 1.0 - fr), (r0 + 1, fr)):
        if wr == 0.0:
            continue
        for cc, wc in ((c0, 1.0 - fc), (c0 + 1, fc)):
            if wc == 0.0:
                continue
            out += np.float32(wr * wc) * zp[pad + rr:pad + rr + h, pad + cc:pad + cc + w]
    return out


def max_upwind_angle(z: np.ndarray, cell: float, azimuth: float, distances: np.ndarray) -> np.ndarray:
    """Largest angle (degrees) from each cell up to terrain at the given `distances` (metres)
    in compass direction `azimuth`. Beyond the grid edge the edge value is repeated."""
    z = z.astype(np.float32)
    pad = int(np.ceil(distances.max() / cell)) + 2
    zp = np.pad(z, pad, mode="edge")
    east, north = np.sin(np.radians(azimuth)), np.cos(np.radians(azimuth))
    best = np.full(z.shape, -np.inf, dtype=np.float32)
    for d in distances:
        there = _sample(zp, pad, z.shape, -north * d / cell, east * d / cell)   # north is row minus
        np.maximum(best, (there - z) / np.float32(d), out=best)
    return np.degrees(np.arctan(best)).astype(np.float32)


def sx(z: np.ndarray, cell: float, wind_from: float, dmax: float = 300.0) -> np.ndarray:
    """Winstral maximum upwind slope for wind arriving from `wind_from`, searching `dmax` metres."""
    return max_upwind_angle(z, cell, wind_from, np.arange(cell, dmax + 0.5 * cell, cell))


def horizon_distances(cell: float, reach: float) -> np.ndarray:
    """Search distances that are dense nearby and sparse far away."""
    d, out = cell, []
    while d <= reach:
        out.append(d)
        d += max(cell, 0.08 * d)
    return np.asarray(out)


def sky_view(z: np.ndarray, cell: float, reach: float = 3000.0, directions=DIRECTIONS_16) -> np.ndarray:
    """Fraction of the sky hemisphere visible from each cell (1 = open, lower = enclosed)."""
    dist = horizon_distances(cell, reach)
    total = np.zeros(z.shape, dtype=np.float32)
    for az in directions:
        hor = np.radians(np.maximum(max_upwind_angle(z, cell, az, dist), 0.0))
        total += np.cos(hor) ** 2
    return (total / len(directions)).astype(np.float32)
