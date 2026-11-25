"""Late gauge ticks, the base-depth estimate, and the ridge wind direction."""
import numpy as np
import pandas as pd
import pytest

from vailsnow.live import depth
from vailsnow.model import extended, zones
from vailsnow.obs import hourly


def record(gains, rises):
    """Daily gauge gains and an hourly depth record that rises by `rises` inches on each day (station clock)."""
    days = pd.date_range("2026-01-01", periods=len(gains), freq="D")
    hours = pd.date_range(days[0], days[-1] + pd.Timedelta(days=1), freq="h")
    level = 40.0 + np.concatenate([[0.0], np.cumsum(np.repeat(np.array(rises, float) / 24.0, 24))])
    return pd.Series(gains, index=days, dtype=float), pd.Series(level, index=hours)


def test_a_tick_with_no_new_snow_goes_back_to_the_storm():
    gain, dep = record([0.0, 0.4, 0.1, 0.0, 0.1, 0.0], [0, 6, 0, -1, 0, 0])
    out = hourly.settle_ticks(gain, dep)
    assert out.tolist() == pytest.approx([0.0, 0.6, 0.0, 0.0, 0.0, 0.0])           # both late ticks credited to the storm day
    assert out.sum() == pytest.approx(gain.sum())


def test_a_tick_with_new_snow_stays_and_a_stray_one_is_dropped():
    gain, dep = record([0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1], [2, 0, 0, 0, 0, 0, 0, 0])
    out = hourly.settle_ticks(gain, dep)
    assert out.iloc[0] == pytest.approx(0.1)                                        # the depth sensor rose: real snow
    assert out.iloc[-1] == 0.0 and out.sum() == pytest.approx(0.1)                  # a week later with nothing behind it: not snowfall


def test_snow_that_settles_back_by_evening_still_counts():
    gain, dep = record([0.1, 0.0], [0, 0])
    dep.iloc[6:14] += 1.5                                                           # an inch and a half in the morning, gone by night
    assert hourly.settle_ticks(gain, dep).iloc[0] == pytest.approx(0.1)


def test_days_without_a_depth_reading_are_left_alone():
    gain, dep = record([0.3, 0.1, 0.1], [4, 0, 0])
    dep.loc[dep.index >= "2026-01-02"] = np.nan
    assert hourly.settle_ticks(gain, dep).tolist() == pytest.approx([0.3, 0.1, 0.1])


def test_base_depth_is_tied_to_the_sensor_by_ratio_when_deep_and_shift_when_thin():
    field = np.array([20.0, 40.0, 60.0])
    scaled, how = depth.tie_to_sensor(field, 40.0, 50.0)
    assert how["method"] == "scaled" and scaled == pytest.approx(field * 1.25)
    thin, how = depth.tie_to_sensor(np.array([2.0, 5.0, 9.0]), 5.0, 3.0)
    assert how["method"] == "shifted" and thin == pytest.approx([0.0, 3.0, 7.0])
    same, how = depth.tie_to_sensor(field, 40.0, float("nan"))
    assert how["method"].startswith("model only") and same is field
    capped, how = depth.tie_to_sensor(field, 40.0, 400.0)
    assert how["factor"] == depth.SCALE_LIMITS[1]


def test_elevation_detail_adds_depth_with_height_and_never_goes_negative():
    rows, cols = np.mgrid[0:120, 0:120]
    elev = 2800.0 + 4.0 * cols + 60.0 * np.sin(rows / 3.0)                           # a slope with ridges and gullies across it
    coarse = 0.02 * (2800.0 + 4.0 * cols - 2700.0)                                   # the model sees only the broad slope
    inside = np.ones_like(elev, bool)
    fine, per_1000ft = depth.elevation_detail(coarse, elev, inside, 30.0)
    assert per_1000ft == pytest.approx(0.02 * 304.8, rel=0.15)
    ridge, gully = fine[np.sin(rows / 3.0) > 0.9].mean(), fine[np.sin(rows / 3.0) < -0.9].mean()
    assert ridge > gully and fine.min() >= 0
    flat, rate = depth.elevation_detail(np.full_like(elev, 5.0), elev, inside, 30.0)
    assert rate == 0 and flat == pytest.approx(5.0)                                  # no gradient in the model: nothing added


def test_model_grid_is_read_between_cell_centres():
    field = np.array([[0.0, 10.0], [20.0, 30.0]])
    west, north, c = -106.5, 39.7, depth.snodas.CELL
    lat, lon = np.array([north - 0.5 * c, north - c]), np.array([west + 0.5 * c, west + c])
    got = depth.on_cells(field, west, north, lat, lon)
    assert got[0] == pytest.approx(0.0, abs=1e-6) and got[1] == pytest.approx(15.0, abs=1e-6)             # a cell centre, then the middle of all four


def test_multi_day_middle_outcome_sits_inside_its_range():
    rng = np.random.default_rng(1)
    fc = rng.uniform(0.0, 1.0, 600)
    fit = extended.block_fit(pd.DataFrame({"fc": fc, "obs": fc * rng.uniform(0.0, 2.0, 600)}))
    for total in (0.1, 0.5, 0.9, 2.5):
        low, high = extended.block_range(fit, total)
        assert low <= extended.block_mid(fit, total) <= high


def test_ridge_wind_is_backed_from_the_wind_aloft():
    assert zones.ridge_direction(310.0) == pytest.approx(275.0) and zones.ridge_direction(20.0) == pytest.approx(345.0)


def test_lee_loading_adds_below_the_ridge_strips_the_exposed_side_and_keeps_the_total():
    from vailsnow.model import exposure
    n = 40
    sx = np.where(np.arange(n)[None, :] < n // 2, -20.0, 20.0) * np.ones((n, n))        # left half exposed, right half in the lee
    near, far = np.full((n, n), 20.0), np.full((n, n), 2000.0)
    open_ground, trees, top = np.zeros((n, n)), np.ones((n, n)), np.ones((n, n))
    snow = np.full((n, n), 10.0)
    out = exposure.lee_load(snow, sx, near, open_ground, top, 1.0)
    assert out.sum() == pytest.approx(snow.sum()) and out[:, -1].mean() > 12.5 > 10 > out[:, 0].mean()
    assert out[:, -1].mean() / out[:, 0].mean() > 1.8                                    # roughly twice the windward side
    assert exposure.lee_load(snow, sx, far, open_ground, top, 1.0) == pytest.approx(snow, rel=0.02)      # far from any ridge: no effect
    wooded = exposure.lee_factor(sx, near, trees, top, 1.0)
    assert wooded[:, 0].min() == pytest.approx(1.0) and 1.0 < wooded[:, -1].mean() < exposure.lee_factor(sx, near, open_ground, top, 1.0)[:, -1].mean()
    assert exposure.lee_load(snow, sx, near, open_ground, top, 0.0) is snow              # calm: snow falls where it falls
    low = exposure.lee_factor(sx, near, open_ground, np.zeros((n, n)), 1.0)
    assert low[:, -1].mean() < exposure.lee_factor(sx, near, open_ground, top, 1.0)[:, -1].mean()      # weaker low on the mountain


def test_lee_strength_grows_with_wind_and_fades_for_wet_snow():
    from vailsnow.model import exposure
    assert exposure.lee_strength(1.5) == 0 and exposure.lee_strength(exposure.LEE_TYPICAL_MS) == pytest.approx(1.0)
    assert exposure.lee_strength(30.0) == pytest.approx(exposure.LEE_MAX_STRENGTH)
    assert exposure.lee_strength(8.0, 9.0, -0.5) < exposure.lee_strength(8.0, 14.0, -8.0) < exposure.lee_strength(8.0, 18.0, -12.0) * 1.0001
