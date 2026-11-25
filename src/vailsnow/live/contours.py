"""Turn a gridded field into filled contour bands and contour lines, as GeoJSON in longitude and latitude."""
from __future__ import annotations

import numpy as np
from contourpy import FillType, LineType, contour_generator
from pyproj import Transformer

BINS_IN = (0.1, 1.0, 3.0, 6.0, 9.0, 12.0, 18.0, 24.0)        # lower edge of each band; the last has no top
LABELS = ("trace-1", "1-3", "3-6", "6-9", "9-12", "12-18", "18-24", "24+")
TOP = 1.0e6


def _ring(points: np.ndarray, to_ll: Transformer) -> list[list[float]]:
    lon, lat = to_ll.transform(points[:, 0], points[:, 1])
    return [[round(float(a), 5), round(float(b), 5)] for a, b in zip(lon, lat)]


def bands(field: np.ndarray, x: np.ndarray, y: np.ndarray, epsg: int, edges: tuple[float, ...] = BINS_IN,
          labels: tuple[str, ...] = LABELS) -> dict:
    """Filled bands and the lines between them. `field` has NaN outside the forecast area."""
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    z = np.ma.masked_invalid(field)
    features = []
    if z.count() == 0:
        return {"type": "FeatureCollection", "features": features}
    fill = contour_generator(x=x, y=y, z=z, fill_type=FillType.OuterOffset, corner_mask=True)
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else TOP
        polys, offsets = fill.filled(lo, hi)
        coords = []
        for pts, off in zip(polys, offsets):
            rings = [_ring(pts[off[k]: off[k + 1]], to_ll) for k in range(len(off) - 1)]
            if rings and len(rings[0]) >= 4:
                coords.append(rings)
        if coords:
            features.append({"type": "Feature", "properties": {"band": i, "label": labels[i], "kind": "band"},
                             "geometry": {"type": "MultiPolygon", "coordinates": coords}})
    line = contour_generator(x=x, y=y, z=z, line_type=LineType.Separate, corner_mask=True)
    for i, level in enumerate(edges[1:], start=1):
        parts = [_ring(p, to_ll) for p in line.lines(level) if len(p) >= 8]
        if parts:
            features.append({"type": "Feature", "properties": {"band": i, "level": f"{level:g}", "kind": "line"},
                             "geometry": {"type": "MultiLineString", "coordinates": parts}})
    return {"type": "FeatureCollection", "features": features}
