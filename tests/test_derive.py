import numpy as np
import pytest

from vailsnow.terrain import derive


def plane(dz_east: float, dz_north: float, n: int = 40, cell: float = 10.0) -> np.ndarray:
    """A tilted plane. Row 0 is north, so northing decreases with row."""
    rows, cols = np.mgrid[0:n, 0:n]
    return (1000.0 + dz_east * cols * cell + dz_north * (n - 1 - rows) * cell).astype(np.float32)


@pytest.mark.parametrize("dz_east, dz_north, facing", [
    (0.0, 0.2, 180.0),     # rises to the north, so it faces south
    (0.0, -0.2, 0.0),      # rises to the south, faces north
    (0.2, 0.0, 270.0),     # rises to the east, faces west
    (-0.2, 0.0, 90.0),     # rises to the west, faces east
])
def test_aspect_is_the_direction_the_slope_faces(dz_east, dz_north, facing):
    slope, aspect = derive.slope_aspect(plane(dz_east, dz_north), 10.0)
    assert slope[20, 20] == pytest.approx(np.degrees(np.arctan(0.2)), abs=0.01)
    assert (aspect[20, 20] - facing + 180) % 360 - 180 == pytest.approx(0.0, abs=0.01)


def test_flat_ground_has_no_slope():
    slope, _ = derive.slope_aspect(np.full((20, 20), 3000.0, dtype=np.float32), 10.0)
    assert float(slope.max()) == 0.0


def ridge_surface(n: int = 81, cell: float = 10.0) -> np.ndarray:
    """A north-south ridge down the middle column."""
    cols = np.arange(n)
    return np.tile(3000.0 - np.abs(cols - n // 2) * cell * 0.3, (n, 1)).astype(np.float32)


def test_curvature_and_position_index_signs():
    z = ridge_surface()
    assert derive.curvature(z, 10.0)[40, 40] > 0          # convex on the crest
    assert derive.curvature(-z, 10.0)[40, 40] < 0         # concave in a gully
    assert derive.tpi(z, 10.0, 150.0)[40, 40] > 0
    assert derive.tpi(z, 10.0, 150.0)[40, 20] == pytest.approx(0.0, abs=1e-3)   # uniform slope, window clear of the edge


def test_ridge_mask_finds_the_crest_and_distance_grows_away_from_it():
    z = ridge_surface() + np.linspace(0, 40, 81, dtype=np.float32)[:, None]   # crest also climbs southward
    ridge = derive.ridge_mask(z, 10.0, min_area_m2=20000.0, smooth_m=10.0)
    assert ridge[40, 39:42].any()
    assert not ridge[40, :30].any() and not ridge[40, 51:].any()
    d = derive.distance_to(ridge, 10.0)
    assert d[40, 10] > d[40, 30] > d[40, 40]


def test_accumulation_collects_a_valley():
    z = -ridge_surface() + np.linspace(40, 0, 81, dtype=np.float32)[:, None]  # valley draining south
    acc = derive.accumulation(z)
    assert acc[79, 40] > 1000          # most of the grid drains out through the valley floor
    assert acc[40, 5] < 50
    assert derive.valley_mask(z, 10.0, min_area_m2=100000.0, smooth_m=10.0)[70, 39:42].any()


def test_hillshade_is_brighter_facing_the_light():
    lit = derive.hillshade(plane(-0.3, 0.3), 10.0, azimuth=315.0)[20, 20]     # faces... away from NW
    shaded = derive.hillshade(plane(0.3, -0.3), 10.0, azimuth=315.0)[20, 20]  # faces NW
    assert shaded > lit
