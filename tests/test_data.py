"""Checks against the real terrain build. Skipped until `vailsnow terrain` has run."""
import json

import numpy as np
import pytest
import rasterio
from pyproj import Transformer

from vailsnow.resort import FT_PER_M, load_resort

pytestmark = pytest.mark.data
resort = load_resort()
OUT = resort.out_dir()
needs_build = pytest.mark.skipif(not (OUT / "terrain_10m.tif").exists(), reason="run `vailsnow terrain` first")


def sample(band: str, lat: float, lon: float) -> float:
    with rasterio.open(OUT / "terrain_10m.tif") as ds:
        x, y = Transformer.from_crs(4326, ds.crs, always_xy=True).transform(lon, lat)
        r, c = ds.index(x, y)
        return float(ds.read(ds.descriptions.index(band) + 1)[r, c])


@needs_build
@pytest.mark.parametrize("point_id, tolerance_ft", [("snotel_842", 40), ("patrol_hq", 60), ("vail_coop", 60), ("west_vail", 60)])
def test_grid_elevation_matches_surveyed_station_elevation(point_id, tolerance_ft):
    p = next(p for p in resort.points if p["id"] == point_id)
    assert sample("elevation", p["lat"], p["lon"]) * FT_PER_M == pytest.approx(p["elev_ft"], abs=tolerance_ft)


@needs_build
def test_layers_are_complete_and_in_range():
    with rasterio.open(OUT / "terrain_10m.tif") as ds:
        assert ds.res == (10.0, 10.0) and ds.crs.to_epsg() == 26913
        d = {n: ds.read(i + 1) for i, n in enumerate(ds.descriptions)}
    for name in ("elevation", "slope", "aspect", "curvature", "tpi_150m", "tpi_500m", "ridge_distance", "sky_view"):
        assert np.isfinite(d[name]).all(), name
    assert 2300 < d["elevation"].min() and d["elevation"].max() < 3700
    assert 0 <= d["slope"].min() and d["slope"].max() < 85
    assert 0 <= d["aspect"].min() and d["aspect"].max() < 360
    assert 0.3 < d["sky_view"].min() and d["sky_view"].max() <= 1.0
    cover = d["canopy_cover"]
    assert np.isfinite(cover).mean() > 0.99 and np.nanmin(cover) >= 0 and np.nanmax(cover) <= 1


@needs_build
def test_sx_has_sixteen_directions_and_opposite_winds_disagree():
    with rasterio.open(OUT / "sx_300m.tif") as ds:
        assert ds.count == 16
        nw = ds.read(ds.descriptions.index("sx_from_315.0") + 1) * 0.01
        se = ds.read(ds.descriptions.index("sx_from_135.0") + 1) * 0.01
    assert np.corrcoef(nw.ravel(), se.ravel())[0, 1] < -0.3


@needs_build
def test_every_zone_exists_and_known_terrain_faces_the_right_way():
    stats = json.loads((OUT / "zone_stats.json").read_text())
    assert not stats["seed_trails_not_in_map"]
    for z in resort.zones:
        assert stats["zones"][z.id]["acres"] > 100, z.id
    groups = stats["groups"]
    assert groups["Front Side"]["aspect_share"]["north"] > 0.5         # the Front Side faces mostly north
    assert groups["Back Bowls"]["aspect_share"]["north"] < 0.15        # the Back Bowls face away from north
    assert groups["Back Bowls"]["canopy_cover"] < groups["Front Side"]["canopy_cover"]   # open bowls, treed front
    assert stats["zones"]["lionshead"]["elev_min_ft"] < 8300
    assert stats["zones"]["petes"]["elev_max_ft"] > 11400


@needs_build
def test_northwest_wind_exposes_the_front_and_shelters_the_bowls():
    z = json.loads((OUT / "zone_stats.json").read_text())["zones"]
    assert z["lionshead"]["sx_nw_mean_deg"] < 0 < z["sun_down"]["sx_nw_mean_deg"]


@needs_build
def test_zone_polygons_are_valid_geojson_in_lat_lon():
    fc = json.loads((OUT / "zones.geojson").read_text())
    assert len(fc["features"]) == len(resort.zones)
    for f in fc["features"]:
        ring = f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"][0][0]
        lon, lat = ring[0][:2]
        assert -106.45 < lon < -106.25 and 39.55 < lat < 39.66
