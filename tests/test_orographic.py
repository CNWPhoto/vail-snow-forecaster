import numpy as np
import pytest

from vailsnow.model import orographic as og


def ridge(n=128, cell=1000.0, height=1000.0, half_width=10_000.0):
    """A north-south ridge in the middle of the grid."""
    x = (np.arange(n) - (n - 1) / 2) * cell          # centred, so the taper and the ridge share a midline
    return np.tile(height * np.exp(-(x / half_width) ** 2), (n, 1)), x


PAR = dict(cw=0.004, hw=2500.0, nm=0.005)


def test_parameters_are_physical():
    p = og.parameters(265.0, 16.0)
    assert 0.004 < og.moist_adiabatic_lapse(265.0, 700.0) < 0.0098        # between very moist and dry adiabatic
    assert 0.001 < p["cw"] < 0.02 and 1500 < p["hw"] < 5000 and 0.002 <= p["nm"] <= 0.015
    assert og.layer_depth(256.0) == pytest.approx(2520, rel=0.03)


def test_west_wind_wets_the_west_slope_and_dries_the_lee():
    h, x = ridge()
    p = og.Terrain(h, 1000.0).precipitation(15.0, 0.0, tau_c=0.0, tau_f=0.0, **PAR)[64]
    assert p[(x > -15_000) & (x < -3_000)].mean() > 0.2 > 0 > p[(x > 3_000) & (x < 15_000)].mean()
    assert abs(p.mean()) < 1e-6                                           # lift and descent cancel over the domain


def test_reversing_the_wind_mirrors_the_pattern():
    h, _ = ridge()
    t = og.Terrain(h, 1000.0)
    a = t.precipitation(15.0, 0.0, **PAR)[64]
    b = t.precipitation(-15.0, 0.0, **PAR)[64]
    assert np.allclose(a, b[::-1], atol=1e-6)


def test_delay_carries_the_maximum_downwind_and_flattens_it():
    h, x = ridge()
    t = og.Terrain(h, 1000.0)
    fast = t.precipitation(15.0, 0.0, tau_c=0.0, tau_f=0.0, **PAR)[64]
    slow = t.precipitation(15.0, 0.0, tau_c=1000.0, tau_f=1000.0, **PAR)[64]
    assert x[slow.argmax()] > x[fast.argmax()] + 5_000                    # 2000 s at 15 m/s is tens of km of drift
    assert slow.max() < fast.max()


def test_wind_along_the_ridge_gives_nothing():
    h, _ = ridge()
    p = og.Terrain(h, 1000.0).precipitation(0.0, 5.0, tau_c=0.0, tau_f=0.0, **PAR)
    across = og.Terrain(h, 1000.0).precipitation(5.0, 0.0, tau_c=0.0, tau_f=0.0, **PAR)
    mid = slice(54, 74)                               # away from the tapered north and south ends
    assert np.abs(p[mid]).max() < 0.02 * np.abs(across[mid]).max()


def test_row_zero_is_north():
    """An east-west ridge with wind from the south: the south side (high row numbers) is wet."""
    h, _ = ridge()
    p = og.Terrain(h.T, 1000.0).precipitation(0.0, 15.0, tau_c=0.0, tau_f=0.0, **PAR)[:, 64]
    assert p[70:80].mean() > 0 > p[48:58].mean()


def test_bilinear_sample():
    f = np.arange(12, dtype=float).reshape(3, 4)
    assert og.sample(f, np.array([0.5]), np.array([1.5]))[0] == pytest.approx(3.5)
    assert og.sample(f, np.array([1.0]), np.array([2.0]))[0] == pytest.approx(6.0)
