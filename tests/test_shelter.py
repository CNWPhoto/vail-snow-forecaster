import numpy as np
import pytest

from vailsnow.terrain import shelter


def wall(n: int = 61, cell: float = 10.0, height: float = 50.0) -> np.ndarray:
    """Flat ground with a north-south wall along column 40 (east of centre)."""
    z = np.zeros((n, n), dtype=np.float32)
    z[:, 40] = height
    return z


def test_sx_is_positive_in_the_lee_of_upwind_terrain():
    z = wall()
    east_wind = shelter.sx(z, 10.0, wind_from=90.0, dmax=300.0)
    # 100 m downwind (west) of the wall: sheltered by a 50 m wall, atan(50/100)
    assert east_wind[30, 30] == pytest.approx(np.degrees(np.arctan(0.5)), abs=0.1)
    # the same cell under a west wind has nothing upwind of it
    assert shelter.sx(z, 10.0, wind_from=270.0, dmax=300.0)[30, 30] == pytest.approx(0.0, abs=1e-4)


def test_sx_is_negative_on_an_exposed_crest():
    # Sx is the MAXIMUM upwind angle, so on a crest it is the shallowest look-down: the far end of the search.
    z = wall()
    got = shelter.sx(z, 10.0, wind_from=270.0, dmax=300.0)[30, 40]
    assert got == pytest.approx(-np.degrees(np.arctan(50.0 / 300.0)), abs=0.1)


def test_sx_ignores_terrain_beyond_dmax():
    z = wall()
    assert shelter.sx(z, 10.0, wind_from=90.0, dmax=300.0)[30, 5] == pytest.approx(0.0, abs=1e-4)   # 350 m away


@pytest.mark.parametrize("azimuth, d_row, d_col", [(0.0, -10, 0), (90.0, 0, 10), (180.0, 10, 0), (270.0, 0, -10), (45.0, -10, 10)])
def test_search_direction_matches_the_compass(azimuth, d_row, d_col):
    z = np.zeros((61, 61), dtype=np.float32)
    z[30 + d_row, 30 + d_col] = 100.0        # a spike in the stated compass direction from the centre
    dist = np.hypot(d_row, d_col) * 10.0
    got = shelter.max_upwind_angle(z, 10.0, azimuth, np.array([dist]))[30, 30]
    assert got == pytest.approx(np.degrees(np.arctan(100.0 / dist)), abs=1.5)


def test_sky_view_is_open_on_flat_ground_and_reduced_in_a_pit():
    flat = np.zeros((81, 81), dtype=np.float32)
    assert shelter.sky_view(flat, 10.0, reach=300.0)[40, 40] == pytest.approx(1.0)
    rows, cols = np.mgrid[0:81, 0:81]
    pit = (np.hypot(rows - 40, cols - 40) * 10.0 * 0.6).astype(np.float32)    # cone, 31 degree walls
    sv = shelter.sky_view(pit, 10.0, reach=300.0)[40, 40]
    # Bilinear sampling next to the cone's sharp apex reads slightly high off-axis, hence the tolerance.
    assert sv == pytest.approx(np.cos(np.arctan(0.6)) ** 2, abs=0.04)
    peak = -pit
    assert shelter.sky_view(peak, 10.0, reach=300.0)[40, 40] == pytest.approx(1.0)


def test_horizon_distances_are_increasing_and_reach_the_limit():
    d = shelter.horizon_distances(10.0, 3000.0)
    assert d[0] == 10.0 and np.all(np.diff(d) >= 10.0) and 2700.0 < d[-1] <= 3000.0
