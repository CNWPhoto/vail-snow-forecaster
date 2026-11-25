"""Checks against the real catalogue. Skipped until `vailsnow catalog` (and later steps) have run."""
import json

import pandas as pd
import pytest

from vailsnow.resort import data_dir

pytestmark = pytest.mark.data
CAT = data_dir() / "catalog"
needs_catalog = pytest.mark.skipif(not (CAT / "events.parquet").exists(), reason="run `vailsnow catalog` first")
needs_storms = pytest.mark.skipif(not (CAT / "top_storms.parquet").exists(), reason="run `vailsnow upper` and `vailsnow climatology` first")


@needs_catalog
def test_catalogue_holds_both_benchmark_storms():
    ev = pd.read_parquet(CAT / "events.parquet").set_index("event_id")
    feb2020, jan2014 = ev.loc["20200205"], ev.loc["20140129"]
    assert feb2020["top"] and jan2014["top"]
    assert feb2020["swe_in"] == pytest.approx(3.3, abs=0.2) and feb2020["end"] == pd.Timestamp("2020-02-09")
    assert jan2014["swe_in"] == pytest.approx(2.5, abs=0.2)
    assert feb2020["swe_in"] > 2 * feb2020["swe_beaver_creek"]          # northwest flow: Vail far ahead of Beaver Creek
    assert jan2014["swe_beaver_creek"] >= jan2014["swe_in"] - 0.1       # flow backed west: Beaver Creek level with Vail


@needs_catalog
def test_catalogue_is_plausible():
    ev = pd.read_parquet(CAT / "events.parquet")
    assert 1200 < len(ev) < 2000 and 195 <= ev["top"].sum() <= 230
    assert (ev["swe_in"] >= 0.2 - 1e-9).all() and (ev["end"] >= ev["start"]).all()
    assert ev["start"].is_monotonic_increasing and ev["event_id"].is_unique
    per_season = ev[ev["water_year"].between(1980, 2025)].groupby("water_year").size()
    assert per_season.min() >= 15 and per_season.max() <= 50
    assert (ev["max_1day_swe_in"] <= 3.0).all()                         # nothing physically absurd in one day


@needs_storms
def test_benchmark_storm_regimes_and_feature_ranges():
    t = pd.read_parquet(CAT / "top_storms.parquet").set_index("event_id")
    assert t.loc["20200205", "regime"] == "northwest" and 295 < t.loc["20200205", "dir600"] < 330
    assert t.loc["20140129", "regime"] == "west" and 255 < t.loc["20140129", "dir600"] < 285
    assert t["spd600"].between(0, 60).all() and t["t700_c"].between(-30, 10).all()
    assert t["rh600"].between(0, 1.3).all() and t["tcwv_mm"].between(0.5, 30).all()
    assert len(t) >= 190


@needs_storms
def test_climatology_tables_exist_and_shares_sum_to_one():
    c = json.loads((CAT / "climatology.json").read_text())
    shares = pd.DataFrame(c["all_days"]["regime_shares"])
    assert shares["day_share"].sum() == pytest.approx(1.0, abs=0.01)
    assert shares["vail_share"].sum() == pytest.approx(1.0, abs=0.01)


@needs_catalog
def test_daily_readings_are_start_of_day():
    """The daily record must equal the hourly record at midnight of the same date. If the
    service's end-of-day default slips back in, every date is off by one and this fails."""
    obs = data_dir() / "obs"
    if not (obs / "snotel_target_hourly.parquet").exists():
        pytest.skip("no hourly record yet")
    daily = pd.read_parquet(obs / "snotel_daily.parquet")
    daily = daily[daily["site"] == "vail"].set_index("date")["prec"]
    hourly = pd.read_parquet(obs / "snotel_target_hourly.parquet")["prec"]
    midnight = hourly[hourly.index.hour == 0]
    both = pd.concat([daily, midnight], axis=1, keys=["daily", "hourly"]).dropna()
    both = both[both.index >= "2015-10-01"]
    assert len(both) > 3000
    assert ((both["daily"] - both["hourly"]).abs() < 0.05).mean() > 0.98
