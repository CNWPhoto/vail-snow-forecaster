import numpy as np
import pytest

from vailsnow.upper import physics as ph


def test_relative_humidity_round_trip():
    es = ph.saturation_vapour_pressure(263.15)                 # -10 C
    q_sat = 0.622 * es / (600.0 - 0.378 * es)
    assert ph.relative_humidity(q_sat, 263.15, 600.0) == pytest.approx(1.0, abs=1e-3)
    assert ph.relative_humidity(0.5 * q_sat, 263.15, 600.0) == pytest.approx(0.5, abs=0.01)
    assert ph.saturation_vapour_pressure(273.15) == pytest.approx(6.112)


def test_lapse_rate_and_stability():
    assert ph.lapse_rate(265.0, 250.0, 3000.0, 5500.0) == pytest.approx(6.0)
    # dry-adiabatic layer: potential temperature constant, so no stability and an unbounded Froude number
    t_hi = 265.0 * (500.0 / 700.0) ** (ph.RD / ph.CP)
    n = ph.brunt_vaisala(265.0, t_hi, 700.0, 500.0, 3000.0, 5500.0)
    assert n == pytest.approx(0.0, abs=1e-6)
    assert np.isinf(ph.froude(15.0, n, 1000.0))
    n_stable = ph.brunt_vaisala(265.0, 255.0, 700.0, 500.0, 3000.0, 5500.0)
    assert 0.008 < n_stable < 0.02
    assert ph.froude(15.0, n_stable, 1000.0) == pytest.approx(15.0 / (n_stable * 1000.0))


def test_level_of_interpolates_and_reports_no_crossing():
    assert ph.level_of(-15.0, [-5.0, -13.0, -25.0], [700, 600, 500]) == pytest.approx(600 - 100 * 2 / 12)
    assert np.isnan(ph.level_of(-15.0, [-20.0, -30.0, -40.0], [700, 600, 500]))
    assert ph.level_of(0.0, [4.0, -4.0], [1500.0, 3000.0]) == pytest.approx(2250.0)


def test_vapour_transport_of_a_uniform_layer():
    # q = 2 g/kg moving at 20 m/s through a 400 hPa deep layer: q U dp / g
    ivt = ph.integrated_vapour_transport([0.002] * 3, [20.0] * 3, [0.0] * 3, [700, 500, 300])
    assert ivt == pytest.approx(0.002 * 20.0 * 40000.0 / ph.G, rel=1e-6)


def test_mean_direction_is_weighted_and_reports_steadiness():
    d, vec, steady = ph.vector_mean_direction([10.0, 10.0], [-10.0, -10.0])
    assert d == pytest.approx(315.0) and steady == pytest.approx(1.0)
    d, vec, steady = ph.vector_mean_direction([10.0, -10.0], [0.0, 0.0])
    assert vec == pytest.approx(0.0) and steady == pytest.approx(0.0)
    d, _, _ = ph.vector_mean_direction([10.0, 0.0], [0.0, -10.0], weights=[0.0, 1.0])   # only the north wind counts
    assert d == pytest.approx(0.0)


def test_angle_difference_wraps():
    assert ph.angle_diff(10.0, 350.0) == pytest.approx(20.0)
    assert ph.angle_diff(350.0, 10.0) == pytest.approx(-20.0)
