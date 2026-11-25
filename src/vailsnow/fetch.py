"""Cached downloads. Nothing is fetched twice: a file that exists on disk is reused."""
from __future__ import annotations

import threading
import time
import urllib.parse
from pathlib import Path

import requests

USER_AGENT = "vail-snow-forecast/0.1"
DEM_URL = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage"


def cached_get(url: str, path: Path, timeout: int = 180, min_size: int = 1000, retries: int = 3) -> Path:
    """Download `url` to `path` unless it is already there."""
    path = Path(path)
    if path.exists() and path.stat().st_size >= min_size:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    body = r.content
    if body[:1] == b"{" or body[:5].lower() == b"<html":
        raise RuntimeError(f"service returned an error instead of data: {body[:300]!r}")
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.part")
    tmp.write_bytes(body)
    tmp.replace(path)
    return path


def fetch_3dep(bounds: tuple[float, float, float, float], cell: float, epsg: int, path: Path) -> Path:
    """USGS 3DEP elevation for a projected box (west, south, east, north), as a float32 GeoTIFF."""
    w, s, e, n = bounds
    width, height = round((e - w) / cell), round((n - s) / cell)
    if max(width, height) > 8000:
        raise ValueError("request larger than the service's 8000 pixel limit; tile it")
    q = {
        "bbox": f"{w:f},{s:f},{e:f},{n:f}", "bboxSR": epsg, "imageSR": epsg, "size": f"{width},{height}",
        "f": "image", "format": "tiff", "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation", "noData": -9999,
    }
    return cached_get(DEM_URL + "?" + urllib.parse.urlencode(q), path)
