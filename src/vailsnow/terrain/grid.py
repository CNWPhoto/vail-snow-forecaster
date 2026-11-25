"""The fine terrain grid: lidar heights block-averaged to the working cell size,
with a margin of coarser regional terrain around it."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import Affine, from_origin
from rasterio.warp import Resampling, reproject

from ..fetch import fetch_3dep
from ..resort import Resort, cache_dir


@dataclass
class Grid:
    """A north-up raster in a projected CRS. `pad` cells on every side are margin."""
    z: np.ndarray          # elevation, metres, including the margin
    cell: float
    west: float            # outer west edge of the padded array
    north: float           # outer north edge of the padded array
    pad: int
    epsg: int

    @property
    def core(self) -> tuple[slice, slice]:
        p = self.pad
        return (slice(p, self.z.shape[0] - p), slice(p, self.z.shape[1] - p))

    @property
    def core_shape(self) -> tuple[int, int]:
        return (self.z.shape[0] - 2 * self.pad, self.z.shape[1] - 2 * self.pad)

    @property
    def core_transform(self) -> Affine:
        return from_origin(self.west + self.pad * self.cell, self.north - self.pad * self.cell, self.cell, self.cell)

    @property
    def transform(self) -> Affine:
        return from_origin(self.west, self.north, self.cell, self.cell)

    def core_bounds(self) -> tuple[float, float, float, float]:
        h, w = self.core_shape
        x0, y1 = self.west + self.pad * self.cell, self.north - self.pad * self.cell
        return (x0, y1 - h * self.cell, x0 + w * self.cell, y1)

    def core_rc(self, x, y):
        """Row and column in the core grid for projected coordinates (may fall outside)."""
        x0, _, _, y1 = self.core_bounds()
        return (np.floor((y1 - np.asarray(y)) / self.cell).astype(int),
                np.floor((np.asarray(x) - x0) / self.cell).astype(int))


def block_mean(a: np.ndarray, f: int) -> np.ndarray:
    """Average non-overlapping f x f blocks, dropping any ragged edge."""
    h, w = (a.shape[0] // f) * f, (a.shape[1] // f) * f
    return a[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3), dtype=np.float64).astype(np.float32)


def region_bounds(resort: Resort) -> tuple[float, float, float, float]:
    """The regional lat/lon box as a projected box snapped to the regional cell size."""
    r, cell = resort.region, resort.region["cell"]
    t = Transformer.from_crs(4326, resort.epsg, always_xy=True)
    xs, ys = t.transform([r["west"], r["east"], r["west"], r["east"]], [r["south"], r["south"], r["north"], r["north"]])
    snap = lambda v, up: (np.ceil(v / cell) if up else np.floor(v / cell)) * cell
    return (float(snap(min(xs), False)), float(snap(min(ys), False)), float(snap(max(xs), True)), float(snap(max(ys), True)))


def regional_dem_path(resort: Resort):
    """Regional DEM covering the whole configured box, fetched once from USGS 3DEP."""
    path = cache_dir() / "3dep" / f"{resort.id}_region_{int(resort.region['cell'])}m.tif"
    return fetch_3dep(region_bounds(resort), resort.region["cell"], resort.epsg, path)


def build_grid(resort: Resort) -> Grid:
    fine = resort.fine
    cell, src = fine["cell"], fine["source_cell"]
    f = int(round(cell / src))
    nodes = np.load(resort.source_path("heights"), mmap_mode="r")
    core = block_mean(np.asarray(nodes), f)
    # Each lidar node is treated as the centre of a source-size pixel.
    core_west, core_north = fine["source_e0"] - src / 2, fine["source_n_top"] + src / 2
    pad = int(round(fine["margin"] / cell))
    h, w = core.shape
    z = np.full((h + 2 * pad, w + 2 * pad), np.nan, dtype=np.float32)
    west, north = core_west - pad * cell, core_north + pad * cell
    with rasterio.open(regional_dem_path(resort)) as ds:
        reproject(rasterio.band(ds, 1), z, dst_transform=from_origin(west, north, cell, cell),
                  dst_crs=f"EPSG:{resort.epsg}", resampling=Resampling.bilinear, src_nodata=-9999, dst_nodata=np.nan)
    if np.isnan(z).any():
        raise RuntimeError("regional DEM does not cover the padded grid")
    z[pad:pad + h, pad:pad + w] = core
    return Grid(z, cell, west, north, pad, resort.epsg)


def canopy_layers(resort: Resort, grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """Canopy cover (0 to 1: share of lidar first returns that hit vegetation) and
    mean canopy height (m) on the core grid."""
    f = int(round(resort.fine["cell"] / resort.fine["source_cell"]))
    with np.load(resort.source_path("canopy")) as d:
        n1 = block_mean(d["n1"].astype(np.float32), f)
        nh = block_mean(d["nh"].astype(np.float32), f)
        top = block_mean(d["top"].astype(np.float32) / 4.0, f)
    h, w = grid.core_shape
    cover = np.where(n1 > 0, nh / np.maximum(n1, 1e-6), np.nan)
    return np.clip(cover[:h, :w], 0, 1).astype(np.float32), top[:h, :w].astype(np.float32)
