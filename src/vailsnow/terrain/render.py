"""Turn grids into coloured PNG overlays for the review map."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def ramp(values: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]], alpha: int = 255,
         transparent: np.ndarray | None = None) -> np.ndarray:
    """Colour `values` by linear interpolation between (value, rgb) stops. Returns RGBA uint8."""
    xs = np.array([s[0] for s in stops], dtype=np.float64)
    v = np.nan_to_num(values.astype(np.float64), nan=xs[0])
    out = np.empty(values.shape + (4,), dtype=np.uint8)
    for k in range(3):
        out[..., k] = np.interp(v, xs, [s[1][k] for s in stops]).astype(np.uint8)
    out[..., 3] = alpha
    out[np.isnan(values), 3] = 0
    if transparent is not None:
        out[transparent, 3] = 0
    return out


def classes(index: np.ndarray, colours: dict[int, tuple[int, int, int]], alpha: int = 255) -> np.ndarray:
    out = np.zeros(index.shape + (4,), dtype=np.uint8)
    for val, rgb in colours.items():
        m = index == val
        out[m, :3] = rgb
        out[m, 3] = alpha
    return out


def save_png(rgba: np.ndarray, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)
