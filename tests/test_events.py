import numpy as np
import pandas as pd
import pytest

from vailsnow.catalog import events as ev
from vailsnow.obs import acis, snotel


def site(swe, prec=None, depth=None, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(swe), freq="D")
    d = {"swe": pd.Series(swe, index=idx, dtype=float)}
    if prec is not None:
        d["prec"] = pd.Series(prec, index=idx, dtype=float)
    if depth is not None:
        d["depth"] = pd.Series(depth, index=idx, dtype=float)
    return pd.DataFrame(d)


def test_gain_is_credited_to_the_day_it_fell():
    g = ev.daily_gain(site([5.0, 5.0, 5.6, 5.6])["swe"])
    assert g.tolist()[:3] == [0.0, 0.6, 0.0] and np.isnan(g.iloc[-1])


def test_one_storm_with_totals():
    df = site([10.0, 10.0, 10.5, 11.5, 11.6, 11.6, 11.6], prec=[20.0, 20.0, 20.4, 21.3, 21.4, 21.4, 21.4],
              depth=[40, 40, 46, 55, 54, 53, 52])
    e = ev.detect_events(df)
    assert len(e) == 1
    r = e.iloc[0]
    assert (r.start, r.end, r.days) == (pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-04"), 3)
    assert r.swe_in == pytest.approx(1.6) and r.prec_in == pytest.approx(1.4)
    assert r.max_1day_swe_in == pytest.approx(1.0) and r.max_2day_swe_in == pytest.approx(1.5)
    assert r.depth_gain_in == 15 and r.water_year == 2020 and r.event_id == "20200102"


def test_a_lone_tenth_needs_the_gauge_to_agree():
    jitter = site([10.0, 10.1, 10.1, 10.1], prec=[20.0, 20.0, 20.0, 20.0])
    assert ev.detect_events(jitter, min_total=0.1).empty
    real = site([10.0, 10.1, 10.1, 10.1], prec=[20.0, 20.1, 20.1, 20.1])
    assert len(ev.detect_events(real, min_total=0.1)) == 1


def test_a_lull_splits_two_storms_and_stays_with_the_first():
    #        day:  0     1     2     3     4     5     6
    df = site([10.0, 10.8, 11.4, 11.5, 12.3, 12.9, 12.9], prec=[0, 0.8, 1.4, 1.5, 2.3, 2.9, 2.9])
    e = ev.detect_events(df)
    assert e["days"].tolist() == [3, 2]
    assert e["swe_in"].tolist() == pytest.approx([1.5, 1.4])
    assert e["start"].tolist() == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-04")]


def test_a_dry_day_ends_an_event_and_small_events_are_dropped():
    df = site([10.0, 10.5, 10.5, 10.6, 10.6, 11.2, 11.2], prec=[0, 0.5, 0.5, 0.6, 0.6, 1.2, 1.2])
    e = ev.detect_events(df)           # the 0.1 inch day is below the 0.2 inch minimum
    assert e["swe_in"].tolist() == pytest.approx([0.5, 0.6])
    assert e["rank"].tolist() == [2, 1]


def test_melt_is_not_negative_snowfall_and_gaps_break_events():
    df = site([10.0, 10.6, 10.2, np.nan, 10.9, 11.5])
    e = ev.detect_events(df)
    assert e["swe_in"].tolist() == pytest.approx([0.6, 0.6])


def test_window_and_valley_totals():
    swe = site([5.0, 5.4, 5.3, 5.9, 5.9])["swe"]
    assert ev.window_gain(swe, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-03")) == pytest.approx(1.0)   # melt day ignored
    assert np.isnan(ev.window_gain(swe, pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-09")))
    snow = pd.Series([1.0, 4.0, 6.0, 2.0], index=pd.date_range("2020-01-01", periods=4))
    # event on Jan 1-2 is covered by the morning reports of Jan 2 and Jan 3
    assert ev.valley_total(snow, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")) == 10.0
    assert np.isnan(ev.valley_total(snow, pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-04")))


def test_acis_value_parsing():
    assert acis.to_number("T") == 0.0 and acis.to_number("1.25") == 1.25 and acis.to_number("3.0A") == 3.0
    assert np.isnan(acis.to_number("M"))
    df = acis.parse({"data": [["2020-02-07", "0.80", "17.0", "43"], ["2020-02-08", "M", "T", "2.0A"]]})
    assert df.loc["2020-02-07", "snow"] == 17.0 and not df.loc["2020-02-07", "multi_day"]
    assert np.isnan(df.loc["2020-02-08", "pcpn"]) and df.loc["2020-02-08", "multi_day"]


def test_snotel_parsing_and_water_year():
    import datetime as dt
    payload = [{"data": [{"stationElement": {"elementCode": "WTEQ"}, "values": [{"date": "2020-02-05", "value": 11.1}, {"date": "2020-02-06", "value": 12.1}]},
                         {"stationElement": {"elementCode": "XXXX"}, "values": []}]}]
    df = snotel.parse(payload)
    assert df.columns.tolist() == ["swe"] and df.loc["2020-02-06", "swe"] == 12.1
    assert snotel.last_complete_water_year_end(dt.date(2026, 11, 21)) == dt.date(2026, 9, 30)
    assert snotel.last_complete_water_year_end(dt.date(2026, 3, 1)) == dt.date(2025, 9, 30)


def test_hourly_shape_tells_a_burst_from_steady_snow():
    idx = pd.date_range("2020-01-01", periods=72, freq="h")
    steady = pd.Series(np.linspace(10.0, 12.4, 72).round(1), index=idx)
    burst = pd.Series(np.where(np.arange(72) < 30, 10.0, np.where(np.arange(72) < 34, 10.0 + (np.arange(72) - 29) * 0.5, 12.0)), index=idx)
    s = ev.hourly_shape(steady, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02"))
    b = ev.hourly_shape(burst, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02"))
    assert s["peak_6h_share"] < 0.25 and s["wet_hours"] > 40
    assert b["peak_6h_share"] == pytest.approx(1.0) and b["peak_1h_in"] == pytest.approx(0.5) and b["wet_hours"] < 10
    assert ev.hourly_shape(steady.iloc[:10], pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")) == {}
