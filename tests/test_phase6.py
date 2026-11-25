"""Phase 6: the forecast log and scorecard, first-chair ranking, lift wind, alerts, analogs, resort checks."""
import json

import numpy as np
import pandas as pd
import pytest

from vailsnow.extras import alerts, analogs, doctor, firstchair, liftwind, scorecard
from vailsnow.obs import asos
from vailsnow.resort import data_dir, load_resort


# ---------------------------------------------------------------------------
# scorecard
# ---------------------------------------------------------------------------

def gauge(days: int = 6, wet_day: int = 2, wet_hours=range(10, 16), per_hour: float = 0.1) -> dict:
    """A synthetic gauge record: dry except for a few hours on one day. Station clock is PST."""
    start = pd.Timestamp("2026-12-01")
    hours = pd.date_range(start, periods=days * 24 + 1, freq="h")
    inc = np.zeros(len(hours))
    for h in wet_hours:
        inc[wet_day * 24 + h] = per_hour
    prec = pd.Series(5.0 + np.cumsum(inc), index=hours)
    depth = pd.Series(30.0 + 12.0 * np.cumsum(inc), index=hours)
    h = pd.DataFrame({"prec": prec, "depth": depth, "swe": 9.0 + np.cumsum(inc), "tobs": 20.0})
    daily = prec[prec.index.hour == 0].iloc[:-2]                  # the last full day has no checked daily total yet
    daily.index.name = "date"
    return scorecard.assemble(daily, h)


def test_gauge_record_is_utc_and_marks_unchecked_hours_provisional():
    rec = gauge()
    wet = rec["water"][rec["water"]["water_in"] > 0.01]
    # readings rise at 10:00 to 15:00 station time (PST) on 3 Dec: the hours ending 18z to 23z
    assert wet.index[0] == pd.Timestamp("2026-12-03 18:00") and wet.index[-1] == pd.Timestamp("2026-12-03 23:00") and len(wet) == 6
    assert wet["water_in"].sum() == pytest.approx(0.6, abs=1e-6)
    assert not rec["water"]["provisional"].iloc[0] and rec["water"]["provisional"].iloc[-1]


def test_window_obs_waits_for_every_hour_and_rejects_sensor_faults():
    rec = gauge()
    got = scorecard.window_obs(rec, pd.Timestamp("2026-12-03 12:00"))
    assert got["obs"] == pytest.approx(0.6, abs=1e-6) and got["depth_change"] == pytest.approx(7.2, abs=0.6) and not got["provisional"]
    assert scorecard.window_obs(rec, rec["water"].index[-1] - pd.Timedelta(hours=3)) is None      # window not finished
    rec["depth"].loc[pd.Timestamp("2026-12-04 08:00"):] += 60.0                                     # a jump no snowfall explains
    assert scorecard.window_obs(rec, pd.Timestamp("2026-12-03 12:00"))["depth_change"] is None


def summary_day(day: int, start: str, q50: float) -> dict:
    return {"day": day, "available": True, "start": start, "water_in": {"q10": 0.0, "q25": q50 / 2, "q50": q50, "q75": q50 * 1.5, "q90": q50 * 2, "mean": q50},
            "raw_in": q50 * 1.2, "gauge": {"ratio": 14.0, "snow_share": 1.0, "temp_c": -8.0}, "regime": "northwest", "upside_setup": False}


def test_log_entries_are_live_only_when_logged_on_the_day_and_never_overwritten(tmp_path):
    run = pd.Timestamp("2026-12-03 12:00")
    shares = np.full((3, 24), 1 / 24)
    live = scorecard.entries(run, [summary_day(1, run.isoformat(), 0.3)], shares, None, logged_at=run + pd.Timedelta(hours=5))
    late = scorecard.entries(run, [summary_day(1, run.isoformat(), 0.9)], shares, None, logged_at=run + pd.Timedelta(days=3))
    assert live[0]["mode"] == "live" and late[0]["mode"] == "replay"
    assert live[0]["snow_q50"] == pytest.approx(0.3 * 14.0, abs=0.05)
    path = tmp_path / "log.jsonl"
    assert scorecard.append(live, path) == 1 and scorecard.append(late, path) == 0          # the first forecast stands
    log = scorecard.read_log(path)
    assert len(log) == 1 and log["q50"].iat[0] == pytest.approx(0.3)


def test_depth_forecast_adds_old_pack_settling_to_new_snow():
    if not (data_dir() / "model" / "scores_settle.json").exists():
        pytest.skip("settlement constants not fitted yet")
    g, shares = {"ratio": 14.0, "snow_share": 1.0, "temp_c": -8.0}, np.full(24, 1 / 24)
    bare = scorecard.settled_change(0.5, g, shares, None)
    deep = scorecard.settled_change(0.5, g, shares, (60.0, 0.3))
    assert 0 < bare < 0.5 * 14.0                    # settles as it falls
    assert deep < bare                              # the old pack sinks under it
    assert scorecard.settled_change(0.0, g, shares, (60.0, 0.3)) < 0


def test_training_winters_are_never_logged():
    assert scorecard.in_training(pd.Timestamp("2025-02-14 12:00")) and not scorecard.in_training(pd.Timestamp("2026-11-24 12:00"))


def verified(rows) -> pd.DataFrame:
    df = pd.DataFrame([{"run": pd.Timestamp(d) + pd.Timedelta(hours=12), "start": pd.Timestamp(d) + pd.Timedelta(hours=12), "day": 1, "q10": lo, "q50": mid, "q90": hi,
                        "raw": raw, "raw_model": "HRRR", "obs": obs, "provisional": False, "regime": "west", "depth_change": np.nan, "mode": "replay",
                        "snow_q10": lo * 14.0, "snow_q50": mid * 14.0, "snow_q90": hi * 14.0, "obs_snow": obs * 14.0, "raw_snow": raw * 14.0}
                       for d, lo, mid, hi, raw, obs in rows])
    return df


def test_block_scores_and_range_counts():
    g = verified([("2026-12-01", 0.0, 0.2, 0.5, 0.3, 0.4), ("2026-12-02", 0.0, 0.1, 0.2, 0.1, 0.6), ("2026-12-03", 0.0, 0.0, 0.01, 0.0, 0.0)])
    b = scorecard.block(g)
    assert b["n"] == 3 and b["mae"] == pytest.approx((0.2 + 0.5 + 0.0) / 3, abs=1e-3) and b["raw_mae"] == pytest.approx((0.1 + 0.5) / 3, abs=1e-3)
    assert b["snow_possible"] == {"n": 2, "mae": 0.35, "inside_10_90": 1, "above_90": 1, "below_10": 0}      # the dead-dry day is left out
    assert b["snow_mae"] == pytest.approx(14.0 * (0.2 + 0.5) / 3, abs=0.01) and b["snow_raw_mae"] == pytest.approx(14.0 * (0.1 + 0.5) / 3, abs=0.01)


def test_storms_are_runs_of_consecutive_wet_days():
    g = verified([("2026-12-01", 0.0, 0.2, 0.5, 0.3, 0.4), ("2026-12-02", 0.1, 0.3, 0.6, 0.3, 0.5), ("2026-12-03", 0.0, 0.0, 0.1, 0.0, 0.0),
                  ("2026-12-04", 0.0, 0.1, 0.2, 0.1, 0.12), ("2026-12-06", 0.0, 0.2, 0.4, 0.2, 0.3)])
    s = scorecard.storms(g, g)
    assert [x["start"][:10] for x in s] == ["2026-12-06", "2026-12-01"]         # newest first; 0.12 in alone is under the storm minimum
    first = s[-1]
    assert first["days"] == 2 and first["observed_in"] == pytest.approx(0.9) and first["day_ahead_in"] == pytest.approx(0.5)
    assert first["day_ahead_range"][0] < 0.5 < first["day_ahead_range"][1] < 1.1 + 1e-9      # tighter than adding the daily bounds
    assert first["observed_snow_in"] == pytest.approx(12.6) and first["day_ahead_snow_in"] == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# first chair
# ---------------------------------------------------------------------------

def test_overnight_parts_cover_closing_to_first_chair():
    even = np.full((3, 24), 1 / 24)
    assert firstchair.overnight_parts(even, 1) == pytest.approx([13 / 24, 3 / 24, 0])       # 4 pm to 5 am, then 5 am to 8 am
    assert firstchair.overnight_parts(even, 2) == pytest.approx([0, 13 / 24, 3 / 24])
    daytime = np.zeros((3, 24))
    daytime[:, :8] = 1 / 8                                                                   # all of it falls 5 am to 1 pm
    assert firstchair.overnight_parts(daytime, 1) == pytest.approx([0, 3 / 8, 0])


def zone(name, likely, high, ratio=13.0, scoured=0, lift="low"):
    return {"id": name, "name": name, "overnight": [likely, 0.0, high], "ratio": ratio, "quality": "average", "scoured_pct": scoured, "loaded_pct": 0,
            "lift": {"name": f"{name} lift", "risk": lift}}


def test_ranking_weighs_depth_quality_wind_and_lifts():
    basis, r = firstchair.rank([zone("deep", 8, 12), zone("deep but lift held", 8, 12, lift="likely"), zone("light", 7, 11, ratio=18), zone("scoured", 8, 12, scoured=60)])
    assert basis == "likely" and [z["name"] for z in r] == ["light", "deep", "scoured", "deep but lift held"]
    assert r[0]["rank"] == 1 and "wind hold likely" in r[-1]["why"] and "scoured about 60%" in r[2]["why"]


def test_ranking_falls_back_to_the_high_end_when_little_is_likely():
    basis, r = firstchair.rank([zone("a", 0.0, 1.0), zone("b", 0.2, 3.0)])
    assert basis == "high" and r[0]["name"] == "b" and "high end" in r[0]["why"]


# ---------------------------------------------------------------------------
# lift wind
# ---------------------------------------------------------------------------

def test_lift_hours_are_nine_to_four():
    r = load_resort()
    assert list(liftwind.open_hours(r, 1)) == list(range(4, 12)) and liftwind.open_hours(r, 2)[0] == 28


def test_station_hours_take_the_strongest_report_and_fall_back_to_sustained_wind():
    t = pd.to_datetime(["2026-01-01 00:15", "2026-01-01 00:35", "2026-01-01 00:55", "2026-01-01 01:15", "2026-01-01 01:35"])
    obs = pd.DataFrame({"wind_ms": [5.0, 6.0, 7.0, 9.0, 9.0], "gust_ms": [np.nan, 12.0, np.nan, np.nan, np.nan], "dir": 270.0}, index=t)
    h = asos.hourly_peak(obs)
    assert h.loc[pd.Timestamp("2026-01-01 01:00"), "peak_ms"] == 12.0 and h.loc[pd.Timestamp("2026-01-01 02:00"), "peak_ms"] == 9.0


def test_odds_rise_with_the_forecast_and_words_follow_them():
    rq = [0.8, 0.9, 1.0, 1.1, 1.3]
    odds = [liftwind.chance_at_least(f, 20.0, rq) for f in (0, 10, 16, 19, 22, 30)]
    assert odds == sorted(odds) and odds[0] == 0.0 and odds[-1] == 1.0
    assert liftwind.chance_at_least(20.0, 20.0, rq) == pytest.approx(0.6)
    assert liftwind.word(0.1, 0.0) == "low" and liftwind.word(0.3, 0.1) == "possible" and liftwind.word(0.9, 0.6) == "likely"


def test_lift_share_is_bounded_and_crosswind_counts_most():
    lift = {"bearing": 0.0, "height": 0.9, "shelter": [0.8] * 16}
    along, across = liftwind.lift_share(lift, 0.0), liftwind.lift_share(lift, 90.0)
    assert across == pytest.approx(0.9 * 0.8) and along == pytest.approx(0.9 * 0.8 * liftwind.ALONG_LINE) and 0 < along < across <= 1


def test_hold_thresholds_come_from_the_resort_file():
    r = load_resort()
    assert liftwind.thresholds(r, "gondola")[0] < liftwind.thresholds(r, "chair_lift")[0] < liftwind.thresholds(r, "platter")[0]


def test_lift_table_and_gust_check_on_real_data():
    r = load_resort()
    if not (r.out_dir() / "sx_300m.tif").exists() or not liftwind.paths()[1].exists():
        pytest.skip("terrain or wind scores not built")
    lifts = {l["name"]: l for l in liftwind.lift_table(r)}
    assert len(lifts) >= 20 and all(liftwind.SHELTER_FLOOR <= min(l["shelter"]) and max(l["shelter"]) <= 1 for l in lifts.values())
    assert lifts["Gopher Hill Lift"]["height"] < lifts["Mountain Top Express Lift"]["height"]          # low on the hill gets less of the ridge gust
    s = json.loads(liftwind.paths()[1].read_text())
    odds = s["odds_check"]["40mph"]
    assert odds["low (under 20%)"]["reached_pct"] < 20 <= odds["possible (20 to 50%)"]["reached_pct"] < 50 <= odds["likely (50% or more)"]["reached_pct"]
    assert 0.3 < s["sustained_share_of_600mb"] < 0.7


# ---------------------------------------------------------------------------
# alerts
# ---------------------------------------------------------------------------

def test_measured_uses_only_reported_hours():
    rec = gauge()
    m = alerts.measured(rec, pd.Timestamp("2026-12-03 12:00"), pd.Timestamp("2026-12-04 00:00"))
    assert m["water_in"] == pytest.approx(0.6, abs=1e-6) and m["hours_reported"] == 12 and m["depth_change_in"] == pytest.approx(7.2, abs=0.6)
    dry = alerts.measured(rec, pd.Timestamp("2026-12-01 12:00"), pd.Timestamp("2026-12-02 00:00"))
    assert dry["water_in"] == 0 and dry["depth_change_in"] == 0


def test_snow_range_adds_what_already_fell():
    q = {"q10": 0.1, "q50": 0.2, "q90": 0.5}
    assert alerts.snow_range(q, 15.0) == [3.0, 1.5, 7.5] and alerts.snow_range(q, 15.0, already=2.0) == [5.0, 3.5, 9.5]


def test_morning_alert_fires_on_measured_depth(monkeypatch, tmp_path):
    monkeypatch.setattr(alerts, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(alerts, "planning", lambda top=3: {})
    r = load_resort()
    wet = alerts.morning(r, pd.Timestamp("2026-12-04 12:05"), gauge(wet_hours=range(18, 24)))          # snow from 6 pm to midnight station time
    assert wet["fire"] and wet["overnight"]["water_in"] == pytest.approx(0.6, abs=1e-6) and "depth sensor gained" in wet["reasons"][0]
    dry = alerts.morning(r, pd.Timestamp("2026-12-03 12:05"), gauge())
    assert not dry["fire"] and dry["overnight"]["water_in"] == 0


def test_alerts_send_once_a_day_and_only_when_a_threshold_is_met(monkeypatch, tmp_path):
    monkeypatch.setattr(alerts, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(alerts, "web_data_dir", lambda: tmp_path)
    sent = []
    monkeypatch.setitem(alerts.SEND, "file", lambda a: sent.append(a["title"]))
    r = load_resort()
    quiet = {"kind": "morning", "time": "2026-12-04T12:05", "zone": "z", "fire": False, "title": "quiet", "lines": [], "reasons": [], "thresholds": {}}
    loud = {**quiet, "fire": True, "title": "loud"}
    assert alerts.deliver(r, quiet)["skipped"] == "no threshold met" and sent == []
    assert alerts.deliver(r, loud, dry_run=True)["skipped"] == "dry run" and sent == []
    assert alerts.deliver(r, loud)["sent"] == ["file"] and alerts.deliver(r, loud)["skipped"] == "already sent today" and sent == ["loud"]
    assert json.loads((tmp_path / "alert.json").read_text())["title"] == "loud"


def test_default_channel_keeps_alerts_on_this_computer():
    assert alerts.settings(load_resort())["channels"] == ["file"]


def test_backtest_counts_are_consistent():
    if not (data_dir() / "model" / "hindcast_tier24.parquet").exists():
        pytest.skip("hindcast not built")
    loose, strict = alerts.backtest(likely_in=2, high_end_in=4), alerts.backtest(likely_in=6, high_end_in=10)
    assert loose["alerts_per_season"] > strict["alerts_per_season"] and loose["powder_days_per_season"] > strict["powder_days_per_season"]
    assert 0 <= strict["powder_days_alerted_pct"] <= 100


# ---------------------------------------------------------------------------
# analogs and resort checks
# ---------------------------------------------------------------------------

def test_storm_labels():
    assert analogs.storm_label(pd.Timestamp("2020-02-05"), pd.Timestamp("2020-02-09")) == "Feb 5-9, 2020"
    assert analogs.storm_label(pd.Timestamp("2014-01-29"), pd.Timestamp("2014-02-01")) == "Jan 29 - Feb 1, 2014"
    assert analogs.storm_label(pd.Timestamp("2020-02-06"), pd.Timestamp("2020-02-06")) == "Feb 6, 2020"


def test_storm_of_finds_the_storm_a_day_belongs_to():
    events = pd.DataFrame({"start": pd.to_datetime(["2020-02-05"]), "end": pd.to_datetime(["2020-02-09"]), "days": [5], "prec_in": [2.9], "swe_in": [2.6], "depth_gain_in": [17.0]})
    s = analogs.storm_of(pd.Timestamp("2020-02-06 12:00"), events)
    assert s["label"] == "Feb 5-9, 2020" and s["water_in"] == 2.9 and analogs.storm_of(pd.Timestamp("2020-02-11"), events) is None


def test_sentence_says_how_good_the_match_is():
    a = {"date": "2020-02-06", "water_in": 1.1, "snow_in": 16.5, "match": "fair", "storm": {"label": "Feb 5-9, 2020", "days": 5, "water_in": 2.9}}
    assert analogs.sentence([a]).startswith("This setup most resembles Feb 6, 2020, part of the Feb 5-9, 2020 storm")
    assert analogs.sentence([a]).endswith("about 16.5 in of snow at mid-mountain (1.1 in of water at the gauge).")
    assert analogs.sentence([{**a, "match": "loose"}]).startswith("Nothing on record is close.")
    assert analogs.sentence([]) is None


def test_resort_file_carries_view_alerts_and_lift_settings():
    r = load_resort()
    assert r.view["headline_zone"] in {z.id for z in r.zones} and r.view["town_site"] in {v["id"] for v in r.valley}
    assert set(alerts.DEFAULTS) <= set(alerts.settings(r)) and r.wind_check["station"]


def test_resort_check_reports_a_missing_file_and_passes_config_for_vail():
    assert doctor.check("no_such_resort")[0]["ok"] is False
    rows = [x for x in doctor.check("vail") if x["step"] == "config"]
    assert rows and all(x["ok"] for x in rows)
