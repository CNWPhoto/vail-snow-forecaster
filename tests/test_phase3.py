"""Unit tests for the Phase 3 pieces: scores, hourly targets, forecast readers and model layers."""
import numpy as np
import pandas as pd
import pytest
from numcodecs import Blosc

from vailsnow.fcst import archive, grib, hrrr
from vailsnow.model import precip, slr, snowlevel, table
from vailsnow.obs import hourly
from vailsnow.upper import physics as ph
from vailsnow.verify import scores as sc


# --- scores -----------------------------------------------------------------

def test_mae_and_bias_signs():
    assert sc.mae([1, 2], [2, 4]) == pytest.approx(1.5)
    assert sc.bias([1, 2], [2, 4]) == pytest.approx(1.5)          # forecast too high -> positive


def test_crps_of_a_single_value_equals_mae():
    obs, f = np.array([0.0, 0.3, 1.0]), np.array([0.1, 0.1, 0.4])
    assert sc.crps_from_quantiles(obs, {q: f for q in sc.CRPS_LEVELS}) == pytest.approx(sc.mae(obs, f))


def test_crps_rewards_an_honest_spread():
    rng = np.random.default_rng(0)
    obs = rng.exponential(1.0, 4000)
    sharp = {q: np.full(obs.size, np.median(obs)) for q in sc.CRPS_LEVELS}
    honest = {q: np.full(obs.size, np.quantile(obs, q)) for q in sc.CRPS_LEVELS}
    assert sc.crps_from_quantiles(obs, honest) < sc.crps_from_quantiles(obs, sharp)


def test_reliability_and_coverage_of_a_perfect_forecast():
    obs = np.random.default_rng(1).normal(size=5000)
    q = {lv: np.full(obs.size, np.quantile(obs, lv)) for lv in (0.1, 0.5, 0.9)}
    rel = sc.reliability(obs, q).set_index("level")["observed"]
    assert rel[0.1] == pytest.approx(0.1, abs=0.01) and rel[0.9] == pytest.approx(0.9, abs=0.01)
    assert sc.coverage(obs, q[0.1], q[0.9]) == pytest.approx(0.8, abs=0.01)


def test_reliability_counts_dry_ties_as_half():
    rel = sc.reliability(np.zeros(10), {0.5: np.zeros(10)})
    assert rel["observed"].iat[0] == pytest.approx(0.5)


def test_sort_quantiles_orders_and_clips():
    q = sc.sort_quantiles({0.1: np.array([0.5, -0.2]), 0.9: np.array([0.2, 0.3])})
    assert q[0.1].tolist() == [0.2, 0.0] and q[0.9].tolist() == [0.5, 0.3]


def test_paired_gain_is_positive_when_the_forecast_beats_the_reference():
    obs = np.tile([0.0, 1.0], 50)
    g = sc.paired_gain(obs, obs + 0.1, obs + 0.3, np.repeat(np.arange(10), 10))
    assert g["gain"] == pytest.approx(0.2) and g["low"] > 0 and g["skill"] == pytest.approx(2 / 3)


# --- hourly targets ---------------------------------------------------------

def _gauge():
    """Four days of hourly gauge readings: 0.6 in falls between 06:00 and 12:00 on 2 January, with wobble."""
    t = pd.date_range("2020-01-01 00:00", "2020-01-05 00:00", freq="h")
    v = np.full(len(t), 5.0)
    v[30:37] = 5.0 + np.linspace(0, 0.6, 7)
    v[37:] = 5.6
    v[27] = 4.9                     # downward wobble
    v[60] = 9.9                     # one-hour spike
    return pd.Series(v, index=t)


def test_clean_increments_ignore_wobble_and_spikes():
    inc = hourly.clean_increments(_gauge())
    assert inc.sum() == pytest.approx(0.6, abs=0.06)
    assert inc.loc["2020-01-02 07:00":"2020-01-02 12:00"].sum() == pytest.approx(0.6, abs=0.06)
    assert inc.max() <= hourly.MAX_HOURLY_IN


def test_hourly_water_adds_up_to_the_daily_gain_and_is_in_utc():
    daily = pd.Series([5.0, 5.0, 5.6, 5.6, 5.6], index=pd.date_range("2020-01-01", periods=5))
    hw = hourly.hourly_water(daily, _gauge())
    assert hw["water_in"].sum() == pytest.approx(0.6)
    # it fell 06:00 to 12:00 station time, which is 14:00 to 20:00 UTC
    assert hw.loc["2020-01-02 15:00":"2020-01-02 20:00", "water_in"].sum() == pytest.approx(0.6, abs=0.06)
    assert not hw["spread"].any()
    w = hourly.window_sum(hw, pd.DatetimeIndex(["2020-01-02 12:00"]), 12)
    assert w["obs_in"].iat[0] == pytest.approx(0.6, abs=0.06)


def test_days_without_an_hourly_signal_are_spread_and_flagged():
    daily = pd.Series([5.0, 5.48, 5.48], index=pd.date_range("2020-01-01", periods=3))
    flat = pd.Series(5.0, index=pd.date_range("2020-01-01 00:00", "2020-01-03 00:00", freq="h"))
    hw = hourly.hourly_water(daily, flat)
    day1 = hw.loc["2020-01-01 09:00":"2020-01-02 08:00"]
    assert day1["water_in"].sum() == pytest.approx(0.48) and day1["spread"].all()
    assert day1["water_in"].max() == pytest.approx(0.02)


# --- forecast readers -------------------------------------------------------

def test_hrrr_cell_lookup_and_window():
    assert hrrr.cell_of(39.61765, -106.38019) == (583, 646)
    r, c = hrrr.window_cell(39.61765, -106.38019)
    lat, lon = hrrr.window_latlon()
    assert abs(lat[r, c] - 39.61765) < 0.02 and abs(lon[r, c] + 106.38019) < 0.02
    with pytest.raises(ValueError):
        hrrr.window_cell(45.0, -100.0)


def test_hrrr_chunk_decoding_round_trip():
    a = np.arange(2 * 150 * 150, dtype="<f2").reshape(2, 150, 150)
    raw = Blosc(cname="lz4", clevel=5).encode(a.tobytes())
    assert np.array_equal(hrrr.decode_chunk(raw, "<f2", (2, 150, 150)), a)


IDX = """1:0:d=2021011512:TMP:2 m above ground:24 hour fcst:
2:1000:d=2021011512:APCP:surface:18-24 hour acc fcst:prob >0.254
3:2500:d=2021011512:APCP:surface:23-24 hour acc fcst:
4:4000:d=2021011512:APCP:surface:18-24 hour acc fcst:
5:5200:d=2021011512:ASNOW:surface:18-24 hour acc fcst:
"""


def test_grib_index_parsing_picks_the_plain_six_hour_field():
    entries = grib.parse_idx(IDX)
    assert len(entries) == 5 and entries[0].end == 999 and entries[-1].end is None
    hit = grib.select(entries, r"^APCP:surface:18-24 hour acc fcst:$")
    assert len(hit) == 1 and (hit[0].start, hit[0].end) == (4000, 5199)


def test_nws_issue_selection_takes_the_file_stamped_for_the_run_hour():
    keys = [f"wmo/qpf/2021/01/15/YIUZ98_KWBN_20210115{t}" for t in ("1019", "1049", "1119", "1149", "1219")]
    assert archive.pick_issue(keys, pd.Timestamp("2021-01-15 12:00")).endswith("1149")
    assert archive.pick_issue(keys[:1], pd.Timestamp("2021-01-15 12:00")) is None


def test_alternate_row_scanning_is_undone():
    stored = np.array([[1, 2, 3], [6, 5, 4], [7, 8, 9], [12, 11, 10]])
    assert grib.unscramble(stored).tolist() == [[1, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]]
    assert stored[1].tolist() == [6, 5, 4]                    # the input is left alone


def test_nearest_grid_cell():
    lat, lon = np.meshgrid([39.0, 39.5, 40.0], [-107.0, -106.5, -106.0], indexing="ij")
    assert grib.nearest(lat, lon, 39.6, -106.4) == (1, 1)


# --- physics and model layers -----------------------------------------------

def test_wet_bulb_sits_between_dew_point_and_temperature():
    assert ph.wet_bulb(293.15, 283.15, 1000.0) - 273.15 == pytest.approx(14.1, abs=0.3)
    assert ph.wet_bulb(270.0, 270.0, 700.0) == pytest.approx(270.0, abs=0.01)       # saturated air
    dry = ph.wet_bulb(275.0, 255.0, 700.0)
    assert 255.0 < dry < 271.0                                                       # dry air: wet-bulb well below freezing
    assert ph.standard_pressure(3000.0) == pytest.approx(701, abs=3)


def test_fit_line_recovers_a_lapse_rate():
    z = np.array([2400.0, 2800.0, 3200.0, 3600.0])
    values = np.vstack([270.0 - 6.5 * (z - 3000.0) / 1000.0, 268.0 - 4.0 * (z - 3000.0) / 1000.0])
    ref, slope = table.fit_line(z, values)
    assert ref == pytest.approx([270.0, 268.0]) and slope == pytest.approx([-6.5, -4.0])


def test_elevation_matched_cell():
    terrain = np.full((7, 7), 2800.0)
    terrain[2, 4] = 3130.0
    assert table.elevation_matched(terrain, 3, 3, 3136.0) == (2, 4)


def test_snow_fraction_and_snow_level():
    assert snowlevel.snow_fraction([-3.0, 0.0, 1.0, 2.0, 5.0]).tolist() == [1.0, 1.0, 0.5, 0.0, 0.0]
    # wet-bulb -2.5 C at 3000 m, cooling 5 C per km: +0.5 C is found 600 m lower
    assert snowlevel.snow_level_m(270.65, -5.0) == pytest.approx(2400.0)
    assert np.isnan(snowlevel.snow_level_m(270.65, 1.0))                             # inversion: no level
    assert snowlevel.at_elevation(270.0, -6.0, 2500.0) == pytest.approx(273.0)


def test_kuchera_ratio():
    assert slr.kuchera(271.16) == pytest.approx(12.0)
    assert slr.kuchera(261.16) == pytest.approx(32.0)        # colder: two per degree
    assert slr.kuchera(273.16) == pytest.approx(10.0)        # warmer: one per degree


def test_spread_zeros_only_touches_dry_days():
    y = precip.spread_zeros(np.array([0.0, 0.0, 0.3]))
    assert (y[:2] < 0).all() and (y[:2] >= -0.02).all() and y[2] == 0.3


def _synthetic(seasons=6, days=150, seed=0):
    rng = np.random.default_rng(seed)
    n = seasons * days
    truth = np.where(rng.random(n) < 0.3, rng.gamma(1.5, 0.2, n), 0.0)
    years = np.arange(2019, 2019 + seasons)
    starts = np.concatenate([pd.date_range(f"{y - 1}-11-01", periods=days, freq="D").to_numpy() for y in years])    # winter days only
    t = pd.DataFrame({"tier": 24, "season": np.repeat(years, days), "start": starts, "obs": truth})
    for c in precip.QPF:
        t[c] = np.clip(0.6 * truth + rng.normal(0, 0.03, n), 0, None)      # a model that runs 40% low
    for c in precip.FLOW:
        t[c] = rng.normal(size=n)
    return t


def test_hindcast_is_out_of_sample_and_removes_a_bias():
    t = _synthetic()
    h = precip.hindcast(t, 24)
    assert len(h) == len(t)
    assert sc.mae(h["obs"], h["q50"]) < sc.mae(h["obs"], h["qpf_3x3"]) < sc.mae(h["obs"], h["clim_mean"])
    assert abs(sc.bias(h["obs"], h["mean"])) < 0.02 < abs(sc.bias(h["obs"], h["qpf_3x3"]))
    assert (h["q10"] <= h["q50"] + 1e-9).all() and (h["q50"] <= h["q90"] + 1e-9).all() and (h["q10"] >= 0).all()
    # changing one season's observations must not change that season's forecasts
    t2 = t.copy()
    t2.loc[t2["season"] == 2021, "obs"] = 9.0
    h2 = precip.hindcast(t2, 24)
    same = h["season"] == 2021
    assert np.allclose(h.loc[same, "q50"], h2.loc[same, "q50"]) and np.allclose(h.loc[same, "mean"], h2.loc[same, "mean"])
