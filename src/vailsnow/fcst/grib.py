"""Pull single fields out of large GRIB2 files on public buckets, and keep only a window of each.

NOAA publishes a small `.idx` text file next to each GRIB2 file listing where every field starts.
Reading that first lets us request just the bytes of the fields we want.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import numpy as np
import requests

from ..fetch import USER_AGENT

_local = threading.local()

# Region kept from every field (a little wider than the project box).
LAT = (38.9, 40.1)
LON = (-107.3, -105.5)


def session() -> requests.Session:
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = USER_AGENT
    return _local.s


@dataclass(frozen=True)
class Entry:
    start: int
    end: int | None      # None for the last field in the file
    text: str            # e.g. "APCP:surface:18-24 hour acc fcst:"


def parse_idx(text: str) -> list[Entry]:
    """Parse a wgrib2-style index: `number:offset:d=date:field:level:period:...`."""
    rows = []
    for line in text.splitlines():
        parts = line.split(":", 3)
        if len(parts) == 4 and parts[1].isdigit():
            rows.append((int(parts[1]), parts[3]))
    return [Entry(off, rows[i + 1][0] - 1 if i + 1 < len(rows) else None, txt) for i, (off, txt) in enumerate(rows)]


def get(url: str, byte_range: tuple[int, int | None] | None = None, optional: bool = False) -> bytes | None:
    headers = {}
    if byte_range:
        a, b = byte_range
        headers["Range"] = f"bytes={a}-{'' if b is None else b}"
    last: Exception | None = None
    for _ in range(4):
        try:
            r = session().get(url, headers=headers, timeout=120)
            if r.status_code in (403, 404):
                if optional:
                    return None
                raise FileNotFoundError(url)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last = e
    raise RuntimeError(f"failed after retries: {url}: {last}")


def select(entries: list[Entry], pattern: str) -> list[Entry]:
    rx = re.compile(pattern)
    return [e for e in entries if rx.search(e.text)]


def fetch_fields(url: str, patterns: dict[str, str]) -> dict[str, bytes]:
    """{label: raw GRIB message} for the first field matching each pattern. Missing fields are left out."""
    idx = get(url + ".idx", optional=True)
    if idx is None:
        return {}
    entries = parse_idx(idx.decode("latin-1"))
    out = {}
    for label, pattern in patterns.items():
        hit = select(entries, pattern)
        if hit:
            raw = get(url, (hit[0].start, hit[0].end), optional=True)
            if raw:
                out[label] = raw
    return out


_windows: dict[tuple, tuple[slice, slice, np.ndarray, np.ndarray]] = {}
_lock = threading.Lock()


def _window(gid, lat: tuple[float, float], lon: tuple[float, float]):
    """Row and column slices of the grid covering the box, cached per grid."""
    import eccodes as ec
    ni, nj = ec.codes_get(gid, "Ni" if ec.codes_is_defined(gid, "Ni") else "Nx"), ec.codes_get(gid, "Nj" if ec.codes_is_defined(gid, "Nj") else "Ny")
    key = (ec.codes_get(gid, "gridType"), ni, nj, round(ec.codes_get(gid, "latitudeOfFirstGridPointInDegrees"), 4),
           round(ec.codes_get(gid, "longitudeOfFirstGridPointInDegrees"), 4), lat, lon)
    with _lock:
        if key not in _windows:
            lats = ec.codes_get_array(gid, "latitudes").reshape(nj, ni)
            lons = ec.codes_get_array(gid, "longitudes").reshape(nj, ni)
            lons = np.where(lons > 180, lons - 360, lons)
            inside = (lats >= lat[0]) & (lats <= lat[1]) & (lons >= lon[0]) & (lons <= lon[1])
            rows, cols = np.where(inside.any(axis=1))[0], np.where(inside.any(axis=0))[0]
            if len(rows) == 0:
                raise ValueError("the box is outside this grid")
            rs, cs = slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)
            _windows[key] = (rs, cs, lats[rs, cs].astype(np.float32), lons[rs, cs].astype(np.float32))
        return _windows[key]


def decode_window(raw: bytes, lat: tuple[float, float] = LAT, lon: tuple[float, float] = LON) -> list[dict]:
    """Every GRIB message found in `raw`, cut to the box. Each item has values, lat, lon and a few keys."""
    import eccodes as ec
    out = []
    pos = 0
    while True:
        pos = raw.find(b"GRIB", pos)
        if pos < 0:
            break
        total = int.from_bytes(raw[pos + 8: pos + 16], "big")       # GRIB2 total length
        if total <= 0 or pos + total > len(raw):
            break
        gid = ec.codes_new_from_message(raw[pos: pos + total])
        try:
            rs, cs, lats, lons = _window(gid, lat, lon)
            ni = ec.codes_get(gid, "Ni" if ec.codes_is_defined(gid, "Ni") else "Nx")
            nj = ec.codes_get(gid, "Nj" if ec.codes_is_defined(gid, "Nj") else "Ny")
            full = ec.codes_get_values(gid).reshape(nj, ni)
            if ec.codes_get(gid, "alternativeRowScanning"):
                full = unscramble(full)
            vals = full[rs, cs].astype(np.float32)
            if ec.codes_get(gid, "bitmapPresent"):
                vals[vals == ec.codes_get(gid, "missingValue")] = np.nan
            item = {"values": vals, "lat": lats, "lon": lons}
            for k in ("shortName", "dataDate", "dataTime", "stepRange", "units", "validityDate", "validityTime"):
                try:
                    item[k] = ec.codes_get(gid, k)
                except Exception:
                    item[k] = None
            out.append(item)
        finally:
            ec.codes_release(gid)
        pos += total
    return out


def unscramble(values: np.ndarray) -> np.ndarray:
    """Undo "alternate row scanning": NBM and NDFD grids store every second row right to left.
    The library returns values in stored order but coordinates in regular order, so without
    this the odd rows are mirrored across the whole grid."""
    out = values.copy()
    out[1::2] = out[1::2, ::-1]
    return out


def nearest(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> tuple[int, int]:
    """Index of the window cell nearest a point."""
    d = (lats - lat) ** 2 + ((lons - lon) * np.cos(np.radians(lat))) ** 2
    r, c = np.unravel_index(int(np.argmin(d)), d.shape)
    return int(r), int(c)
