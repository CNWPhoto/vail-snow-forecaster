"""Unit tests for Phase 4: settlement, wind redistribution, sun exposure and range calibration."""
import numpy as np
import pandas as pd
import pytest

from vailsnow.model import exposure as ex
from vailsnow.model import hindcast, precip, settle


# --- settlement -------------------------------------------------------------

def _storm(hours=24, water=1.0, temp=-8.0):
    return np.full((1, hours), water / hours), np.full((1, hours), temp)


def test_new_snow_settles_and_water_is_untouched():
    w, t = _storm()
    new, old = settle.simulate(w, t, ratio=15.0, depth0_in=40.0, rho0=0.3, new_rate=0.06, old_rate=0.0)
    assert 0.5 * 15.0 < new[0] < 15.0            # shallower than as-fallen, but not collapsed
    assert old[0] == pytest.approx(40.0)         # no load term, no background term: the pack is unchanged


def test_no_rates_means_no_settling():
    w, t = _storm()
    new, old = settle.simulate(w, t, 12.0, 30.0, 0.3, 0.0, 0.0)
    assert new[0] == pytest.approx(12.0) and old[0] == pytest.approx(30.0)


def test_warm_snow_settles_faster_than_cold():
    w, _ = _storm()
    cold = settle.simulate(w, np.full((1, 24), -18.0), 15.0, 40.0, 0.3, 0.06, 0.0)[0][0]
    warm = settle.simulate(w, np.full((1, 24), -1.0), 15.0, 40.0, 0.3, 0.06, 0.0)[0][0]
    assert warm < cold


def test_early_snow_has_settled_more_than_late_snow():
    w, t = np.zeros((2, 24)), np.full((2, 24), -6.0)
    w[0, 0], w[1, 23] = 1.0, 1.0                 # same water, first hour against last hour
    new, _ = settle.simulate(w, t, 15.0, 40.0, 0.3, 0.06, 0.0)
    assert new[0] < new[1] <= 15.0


def test_old_pack_compacts_under_load_and_on_its_own():
    w, t = _storm(water=1.5)
    loaded = settle.simulate(w, t, 12.0, 60.0, 0.2, 0.0, 0.05)[1][0]
    dry = settle.simulate(np.zeros((1, 24)), t, 12.0, 60.0, 0.2, 0.0, 0.0, 0.001)[1][0]
    assert loaded < 60.0 and 58.0 < dry < 60.0


def test_sun_speeds_new_snow_settling():
    w, t = _storm()
    shade = settle.simulate(w, t, 15.0, 40.0, 0.3, 0.06, 0.0, sun=0.7)[0][0]
    sunny = settle.simulate(w, t, 15.0, 40.0, 0.3, 0.06, 0.0, sun=1.5)[0][0]
    assert sunny < shade


def test_depth_smoothing_drops_a_spike():
    t = pd.date_range("2020-01-01", periods=72, freq="h")
    d = pd.Series(40.0, index=t)
    d.iloc[30] = 140.0
    assert settle.smooth_depth(d).max() == pytest.approx(40.0)


# --- wind -------------------------------------------------------------------

def _hill(n=80):
    """Snow field, shelter angles and canopy for a ridge running north-south with a west wind:
    the west half looks down on its upwind terrain (exposed), the east half is in the lee."""
    snow = np.full((n, n), 10.0)
    sx = np.where(np.arange(n)[None, :] < n // 2, -12.0, 12.0) * np.ones((n, 1))
    return snow, sx, np.zeros((n, n))


def test_calm_air_moves_nothing():
    snow, sx, canopy = _hill()
    assert np.allclose(ex.redistribute(snow, sx, canopy, 270.0, ex.mobility(3.0)), snow)
    assert ex.mobility(3.0) == 0.0 and ex.mobility(30.0) == 1.0


def test_wind_scours_the_exposed_side_loads_the_lee_and_conserves_mass():
    snow, sx, canopy = _hill()
    after = ex.redistribute(snow, sx, canopy, 270.0, 1.0)
    assert after.sum() == pytest.approx(snow.sum(), rel=1e-9)
    assert after[:, :35].mean() < 10.0 < after[:, 45:].mean()
    assert after.min() >= 10.0 * (1 - ex.MAX_LOSS) - 1e-9 and after.max() <= 10.0 * (1 + ex.MAX_GAIN) + 1e-9


def test_trees_stop_scouring():
    snow, sx, _ = _hill()
    forest = np.ones_like(snow)
    assert np.allclose(ex.redistribute(snow, sx, forest, 270.0, 1.0), snow)


def test_cells_outside_the_field_stay_empty():
    snow, sx, canopy = _hill()
    snow[:10] = np.nan
    after = ex.redistribute(snow, sx, canopy, 270.0, 1.0)
    assert np.isnan(after[:10]).all() and np.nansum(after) == pytest.approx(np.nansum(snow), rel=1e-9)


def test_light_cold_snow_moves_more_than_dense_or_sticky_snow():
    assert ex.mobility(12.0, 18.0, -10.0) > ex.mobility(12.0, 8.0, -10.0)
    assert ex.mobility(12.0, 14.0, -10.0) > ex.mobility(12.0, 14.0, -0.5)


def test_nearest_stored_direction():
    assert ex.nearest_direction(316.0) == 14 and ex.nearest_direction(355.0) == 0 and ex.nearest_direction(100.0) == 4


# --- sun --------------------------------------------------------------------

def test_sun_index_by_aspect():
    flat = ex.sun_index(np.array([0.0]), np.array([0.0]), 39.6, 35)[0]
    south = ex.sun_index(np.array([30.0]), np.array([180.0]), 39.6, 35)[0]
    north = ex.sun_index(np.array([30.0]), np.array([0.0]), 39.6, 35)[0]
    east, west = (ex.sun_index(np.array([30.0]), np.array([a]), 39.6, 35)[0] for a in (90.0, 270.0))
    assert flat == pytest.approx(1.0) and south > 1.5 and north < 0.2 and east == pytest.approx(west, rel=0.02)
    # by April the sun is high enough to reach a north slope
    assert ex.sun_index(np.array([30.0]), np.array([0.0]), 39.6, 105)[0] > 0.4


def test_sun_factor_needs_clear_sky():
    assert ex.sun_factor(1.6, 0.0) == pytest.approx(1.0)
    assert ex.sun_factor(1.6, 1.0) > 1.0 > ex.sun_factor(0.2, 1.0)


# --- range calibration ------------------------------------------------------

def test_calibration_widens_ranges_that_are_too_narrow():
    rng = np.random.default_rng(0)
    n = 4000
    centre = rng.uniform(0.2, 1.0, n)
    obs = centre + rng.normal(0, 0.3, n)
    h = pd.DataFrame({"obs": obs, "season": rng.integers(2019, 2027, n), "q50": centre})
    for lv in precip.LEVELS:                     # a forecast that claims half the true spread
        h[hindcast.qcol(lv)] = centre + 0.15 * np.sqrt(2) * _erfinv(2 * lv - 1)
    before = (h["obs"] > h["q90"]).mean()
    cal = hindcast.calibrate(h, 24)
    after = (cal["obs"] > cal["q90"]).mean()
    assert before > 0.2 and after == pytest.approx(0.10, abs=0.02)
    assert (cal["q95"] >= cal["q90"] - 1e-9).all() and (cal["q55"] >= cal["q50"] - 1e-9).all()
    assert np.allclose(cal["q10"], h["q10"])     # the lower end is left alone
    assert cal.attrs["stretch"]["0.9"] == pytest.approx(2.0, abs=0.25)


def _erfinv(x):
    from scipy.special import erfinv
    return erfinv(x)
