"""ERA5 reanalysis from the public WeatherBench 2 copies on Google Cloud
(ERA5 is CC BY 4.0, Copernicus Climate Change Service).

Two six-hourly stores are used, both on 13 pressure levels and both ending 2023-01-10:

* FINE: 1.5 degree grid, two days per chunk (about 10 MB per pressure-level variable).
  Fetched only for the chunks a top storm touches.
* COARSE: 5.6 degree grid, 25 days per chunk (about 10 MB). Fetched for every winter
  since 1978, so each day can be given a flow direction whether or not it snowed at Vail.

A chunk is one variable for the whole globe, so only whole chunks can be fetched; each is
downloaded once and kept.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd
from numcodecs import Blosc

from ..fetch import cached_get
from ..resort import cache_dir

BASE = "https://storage.googleapis.com/weatherbench2/datasets/era5"
T0 = pd.Timestamp("1959-01-01")
END = pd.Timestamp("2023-01-10 18:00")
LEVELS = (50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 850, 925, 1000)
STEP = pd.Timedelta(hours=6)


def time_index(t: pd.Timestamp) -> int:
    return int((t - T0) / STEP)


@dataclass(frozen=True)
class Store:
    name: str
    path: str
    steps_per_chunk: int
    n_lon: int
    n_lat: int

    @property
    def url(self) -> str:
        return f"{BASE}/{self.path}"

    @property
    def dir(self):
        return cache_dir() / "era5_wb2" / self.name

    def _coord(self, name: str) -> np.ndarray:
        p = cached_get(f"{self.url}/{name}/0", self.dir / name / "0", min_size=1)
        return np.frombuffer(Blosc().decode(p.read_bytes()), dtype="<f8")

    @cached_property
    def lat(self) -> np.ndarray:
        return self._coord("latitude")

    @cached_property
    def lon(self) -> np.ndarray:
        return self._coord("longitude")

    def chunk_of(self, t: pd.Timestamp) -> int:
        return time_index(t) // self.steps_per_chunk

    def chunk_times(self, chunk: int) -> pd.DatetimeIndex:
        return pd.date_range(T0 + STEP * (chunk * self.steps_per_chunk), periods=self.steps_per_chunk, freq="6h")

    def key(self, var: str, chunk: int, levels: bool) -> str:
        return f"{var}/{chunk}.0.0.0" if levels else f"{var}/{chunk}.0.0"

    def chunk_path(self, var: str, chunk: int, levels: bool = True):
        return self.dir / self.key(var, chunk, levels)

    def fetch(self, var: str, chunk: int, levels: bool = True):
        k = self.key(var, chunk, levels)
        return cached_get(f"{self.url}/{k}", self.dir / k)

    def load(self, var: str, chunk: int, levels: bool = True) -> np.ndarray:
        """One chunk as (time, level, lon, lat) or (time, lon, lat). The store's last chunk is short."""
        raw = np.frombuffer(Blosc().decode(self.fetch(var, chunk, levels).read_bytes()), dtype="<f4")
        tail = (len(LEVELS), self.n_lon, self.n_lat) if levels else (self.n_lon, self.n_lat)
        return raw.reshape((-1,) + tail)

    def nearest(self, lat: float, lon: float) -> tuple[int, int]:
        """(lon index, lat index) of the grid point nearest a location."""
        dlon = np.abs((self.lon - lon % 360.0 + 180.0) % 360.0 - 180.0)
        return int(np.argmin(dlon)), int(np.argmin(np.abs(self.lat - lat)))

    def weights(self, lat: float, lon: float) -> list[tuple[int, int, float]]:
        """Bilinear weights (lon index, lat index, weight) for a location."""
        lon = lon % 360.0
        dl = self.lon[1] - self.lon[0]
        fx = (lon - self.lon[0]) / dl
        i0 = int(np.floor(fx)) % self.n_lon
        wx = fx - np.floor(fx)
        j0 = int(np.clip(np.searchsorted(self.lat, lat) - 1, 0, self.n_lat - 2))
        wy = float(np.clip((lat - self.lat[j0]) / (self.lat[j0 + 1] - self.lat[j0]), 0.0, 1.0))
        out = [(i0, j0, (1 - wx) * (1 - wy)), ((i0 + 1) % self.n_lon, j0, wx * (1 - wy)),
               (i0, j0 + 1, (1 - wx) * wy), ((i0 + 1) % self.n_lon, j0 + 1, wx * wy)]
        return [(i, j, float(w)) for i, j, w in out if w > 1e-9]

    def profile(self, var: str, chunk: int, lat: float, lon: float, levels: bool = True) -> pd.DataFrame | pd.Series:
        """Values at a location for every time in a chunk: a column per pressure level, or a series."""
        a = self.load(var, chunk, levels)
        v = sum(w * a[..., i, j] for i, j, w in self.weights(lat, lon))
        t = self.chunk_times(chunk)[:len(v)]
        return pd.DataFrame(v, index=t, columns=list(LEVELS)) if levels else pd.Series(v, index=t, name=var)


FINE = Store("fine_1p5deg", "1959-2023_01_10-6h-240x121_equiangular_with_poles_conservative.zarr", 8, 240, 121)
COARSE = Store("coarse_5p6deg", "1959-2023_01_10-6h-64x32_equiangular_conservative.zarr", 100, 64, 32)


def wind_from(u, v):
    """Speed (m/s) and the compass direction the wind blows FROM (degrees)."""
    return np.hypot(u, v), (np.degrees(np.arctan2(-u, -v)) % 360.0)


def compass(deg: float) -> str:
    names = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return names[int((deg % 360.0) / 22.5 + 0.5) % 16]
