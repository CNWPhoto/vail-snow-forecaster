"""The 10-day outlook: long-range inputs, odds, multi-day ranges, the refresh clock and the log."""
import json

import numpy as np
import pandas as pd
import pytest

from vailsnow.live import outlook
from vailsnow.model import extended, precip
from vailsnow.model.table import MM_PER_IN


def run_dict(wet_from: int = 96, wet_to: int = 120, mm: float = 2.54) -> dict:
    """A synthetic GEFS run on a 3 x 4 grid: dry except 0.1 in per six hours in one window."""
    d = {"lat": np.zeros((3, 4)), "lon": np.zeros((3, 4))}
    for f in range(6, 265, 6):
        wet = wet_from < f <= wet_to
        d[f"mean_{f:03d}"] = np.full((3, 4), mm if wet else 0.0)
        d[f"spread_{f:03d}"] = np.full((3, 4), 3.0)
        d[f"t700_{f:03d}"] = np.full((3, 4), 265.0)
    return d


def test_window_features_sum_the_day_and_its_neighbours():
    f = extended.window_features(run_dict(), 1, 1, 96)
    assert f["gefs_mean"] == pytest.approx(0.4) and f["gefs_mean_prev"] == 0 and f["gefs_mean_next"] == 0 and f["lead_day"] == 5
    assert f["gefs_spread"] == pytest.approx(np.sqrt(4 * 9.0) / MM_PER_IN) and f["t700"] == 265.0
    before = extended.window_features(run_dict(), 1, 1, 72)
    assert before["gefs_mean"] == 0 and before["gefs_mean_next"] == pytest.approx(0.4) and before["lead_day"] == 4
    assert extended.window_features(run_dict(), 1, 1, 264) is None                      # past the end of the files
    assert np.isnan(extended.window_features(run_dict(), 1, 1, 0)["gefs_mean_prev"])    # nothing before the run began


def test_lead_classes_stay_inside_the_fitted_range():
    assert [extended.lead_day(h) for h in (60, 96, 102, 114, 240, 300)] == [4, 4, 4, 5, 10, 10]


def test_chance_at_least_reads_the_quantiles():
    row = {f"q{int(round(lv * 100)):02d}": v for lv, v in zip(sorted(precip.LEVELS), np.linspace(0.0, 1.2, len(precip.LEVELS)))}
    odds = [extended.chance_at_least(row, t) for t in (0.0, 0.3, 0.6, 0.9, 5.0)]
    assert odds == sorted(odds, reverse=True) and odds[0] > 0.95 and odds[-1] < 0.05
    assert outlook.chance([0.1, 0.5, 0.9], [0.0, 0.2, 0.6], 0.4) == pytest.approx(0.3)
    assert outlook.chance([0.1, 0.5, 0.9], [0.0, 0.0, 0.0], 0.1) == pytest.approx(0.05)


def test_multi_day_ranges_come_from_past_outcomes_and_widen_with_the_forecast():
    rng = np.random.default_rng(0)
    fc = rng.uniform(0.0, 1.0, 600)
    tot = pd.DataFrame({"fc": fc, "obs": fc * rng.uniform(0.0, 2.0, 600)})
    fit = extended.block_fit(tot)
    small, big = extended.block_range(fit, 0.1), extended.block_range(fit, 0.8)
    assert small[0] <= small[1] < big[1] and big[0] < 0.8 < big[1]
    inside = np.mean([lo <= o <= hi for o, (lo, hi) in zip(tot["obs"], (extended.block_range(fit, f) for f in tot["fc"]))])
    assert 0.7 < inside < 0.9
    far = extended.block_range(fit, 3.0)                                                 # bigger than anything on record
    assert far[1] > 2.5 * big[1] * 0.8


def test_stretch_widens_only_the_upper_quantiles():
    p = pd.DataFrame({"q10": [0.0], "q25": [0.05], "q50": [0.1], "q75": [0.2], "q90": [0.3]})
    s = extended.apply_stretch(p, {0.75: 1.5, 0.9: 2.0}).iloc[0]
    assert s["q75"] == pytest.approx(0.25) and s["q90"] == pytest.approx(0.5) and s["q10"] == 0.0 and s["q50"] == 0.1


def test_day_one_starts_at_the_latest_5_am():
    assert outlook.reference(pd.Timestamp("2026-11-24 18:40")) == pd.Timestamp("2026-11-24 12:00")
    assert outlook.reference(pd.Timestamp("2026-11-24 11:40")) == pd.Timestamp("2026-11-23 12:00")


def test_log_keeps_only_true_long_leads_and_never_overwrites(tmp_path):
    run = pd.Timestamp("2026-11-24 06:00")
    base = {"q10": 0.0, "q50": 0.1, "q90": 0.4, "mean": 0.15, "p_wet": 0.2, "raw": 0.12, "lead_day": 4, "per_inch": 14.0}
    days = {run + pd.Timedelta(hours=30): {**base, "lead_h": 54}, run + pd.Timedelta(hours=78): {**base, "lead_h": 102}}
    entries = outlook.log_entries(run, days, run + pd.Timedelta(hours=6))
    assert len(entries) == 1 and entries[0]["lead_h"] == 102 and entries[0]["mode"] == "live"
    assert outlook.log_entries(run, days, run + pd.Timedelta(days=5))[0]["mode"] == "replay"
    path = tmp_path / "outlook.jsonl"
    assert outlook.append(entries, path) == 1
    assert outlook.append([{**entries[0], "q50": 9.9}], path) == 0
    assert outlook.read_log(path)["q50"].iat[0] == pytest.approx(0.1)


def test_warm_air_cuts_snow_low_on_the_hill_first():
    fit = {"t700_c": [-12.0, 0.0], "ratio": [16.0, 12.0], "tw_ref_slope": 0.85, "tw_ref_intercept": 38.5, "tw_per_km": -5.5}
    cells = {"prism": np.array([0.7, 1.1]), "elev": np.array([2600.0, 3400.0])}                # a low cell and a high one
    cold, ratio_cold = outlook.snow_per_inch(fit, cells, 261.0)
    warm, ratio_warm = outlook.snow_per_inch(fit, cells, 276.0)
    assert cold == pytest.approx(np.array([0.7, 1.1]) * ratio_cold) and ratio_warm < ratio_cold          # all snow everywhere when cold
    assert warm[0] < 0.5 * cold[0] and warm[1] / cold[1] > warm[0] / cold[0]
    flat, ratio = outlook.snow_per_inch(fit, cells, float("nan"))
    assert ratio == 13.0 and flat == pytest.approx(np.array([0.7, 1.1]) * 13.0)


def test_held_out_scores_fade_with_lead_and_ranges_hold():
    if not extended.paths()[2].exists():
        pytest.skip("long-range model not scored yet")
    s = json.loads(extended.paths()[2].read_text())
    skill = [s["by_lead"][str(k)]["skill_pct"] for k in extended.LEADS]
    assert skill[0] > skill[-1] >= 0 and all(a >= b - 2 for a, b in zip(skill, skill[1:]))        # no better far out than close in
    assert all(75 <= s["by_lead"][str(k)]["inside_10_90_pct"] <= 95 for k in extended.LEADS)
    assert all(s["by_lead"][str(k)]["mae"] <= s["by_lead"][str(k)]["average_for_the_month_mae"] for k in extended.LEADS)
    assert all(70 <= b["inside_10_90_pct"] <= 92 for b in s["blocks"].values())


def test_outlook_file_is_ten_consecutive_days():
    from vailsnow.resort import web_data_dir
    path = web_data_dir() / "outlook.json"
    if not path.exists():
        pytest.skip("no outlook built")
    o = json.loads(path.read_text())
    starts = [pd.Timestamp(d["start"]) for d in o["days"]]
    assert len(starts) == 10 and all(b - a == pd.Timedelta(days=1) for a, b in zip(starts, starts[1:])) and starts[0].hour == 12
    for d in o["days"]:
        if d["available"]:
            likely, low, high = d["zones"][o["headline_zone"]][:3]
            lo, hi = d["mid"][o["headline_zone"]]
            assert low <= lo <= hi <= max(high, hi)
            assert low <= likely <= high and 0 <= d["chance"]["6in"] <= d["chance"]["3in"] <= 100


def test_status_flags_stale_and_missing_pieces(tmp_path):
    from vailsnow.extras import status
    now = pd.Timestamp("2026-11-25 18:00")
    (tmp_path / "20261124").mkdir()
    (tmp_path / "runs.json").write_text(json.dumps([{"name": "20261124", "run": "2026-11-24T12:00:00"}]))
    (tmp_path / "20261124" / "meta.json").write_text(json.dumps({"built": "2026-11-24 17:50Z"}))
    (tmp_path / "outlook.json").write_text(json.dumps({"updated": "2026-11-25T17:41", "ensemble_run": "2026-11-25T12:00:00"}))
    rows = {r["piece"]: r for r in status.pieces(now, tmp_path)}
    assert not rows["map forecast"]["stale"] and rows["map forecast"]["age"] == "24.2 h ago"
    assert not rows["10-day outlook"]["stale"] and rows["10-day outlook"]["age"] == "19 min ago"
    assert rows["scorecard"]["stale"] and rows["scorecard"]["when"] is None
    late = {r["piece"]: r for r in status.pieces(now + pd.Timedelta(days=2), tmp_path)}
    assert late["map forecast"]["stale"] and late["10-day outlook"]["stale"]


def test_blend_features_read_the_day_and_its_neighbours_on_the_fine_grid():
    d = {"lat": np.zeros((9, 9)), "lon": np.zeros((9, 9))}
    for f in range(47, 258, 6):                                  # a 19z cycle: six-hour totals end at 00, 06, 12, 18 UTC
        field = np.zeros((9, 9))
        if 65 < f <= 89:
            field[:] = 2.54
            field[4, 5] = 5.08                                   # one wetter cell beside the gauge
        d[f"apcp_{f:03d}"] = field
    f = extended.blend_features(d, 4, 4, 65)
    assert f["nbm_qpf"] == pytest.approx(0.4) and f["nbm_max"] == pytest.approx(0.8) and 0.4 < f["nbm_area"] < 0.45
    assert f["nbm_prev"] == 0 and f["nbm_next"] == 0
    assert np.isnan(extended.blend_features(d, 4, 4, 300)["nbm_qpf"]) and np.isnan(extended.blend_features(None, 0, 0, 65)["nbm_qpf"])
    assert np.isnan(extended.blend_features(d, 4, 4, 41)["nbm_prev"])       # the day before starts ahead of the run's first file


def test_housekeeping_drops_old_forecasts_but_keeps_marked_ones_and_trims_logs(tmp_path):
    from vailsnow.extras import housekeeping
    for name, run in (("20261101", "2026-11-01T12:00:00"), ("20261120", "2026-11-20T12:00:00"), ("20261124", "2026-11-24T12:00:00"), ("20250214", "2025-02-14T12:00:00")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "meta.json").write_text(json.dumps({"run": run}))
    (tmp_path / "20250214" / housekeeping.KEEP_MARKER).write_text("")
    (tmp_path / "outlook").mkdir()                                                    # not a forecast folder: left alone
    assert [p.name for p in housekeeping.old_runs(tmp_path)] == ["20261101"]
    log = tmp_path / "job.log"
    log.write_text("".join(f"line {i}\n" for i in range(5000)))
    removed = housekeeping.trim_log(log, 2000)
    kept = log.read_text().splitlines()
    assert removed > 0 and kept[-1] == "line 4999" and kept[0].startswith("line ") and log.stat().st_size <= 2000
    assert housekeeping.trim_log(log, 2000) == 0


def test_housekeeping_keeps_the_blend_cycles_training_uses(tmp_path):
    from vailsnow.extras import housekeeping
    (tmp_path / "2026").mkdir()
    for name in ("20261001_12z", "20261001_19z", "20261001_13z", "20261001_01z", "20261123_13z"):
        (tmp_path / "2026" / f"{name}.npz").write_text("x")
    gone = sorted(p.stem for p in housekeeping.off_cycle_blend(tmp_path, pd.Timestamp("2026-11-24 20:00")))
    assert gone == ["20261001_01z", "20261001_13z"]                                   # old and off-cycle only


def test_a_day_leads_with_snow_if_it_snows_or_with_zero():
    levels = [0.1, 0.25, 0.5, 0.75, 0.9]
    inch = 0.06                                                       # water that makes an inch of snow
    firm = outlook.lead_range(levels, [0.10, 0.20, 0.35, 0.50, 0.70], 0.95, inch)
    assert firm == (0.20, 0.50, False)                                # a firm storm: the plain likely range
    maybe = outlook.lead_range(levels, [0.0, 0.0, 0.02, 0.20, 0.35], 0.60, inch)
    assert maybe[2] and maybe[0] >= inch and maybe[0] < maybe[1] <= 0.35      # uncertain: the range if it snows, never from zero
    unlikely = outlook.lead_range(levels, [0.0, 0.0, 0.0, 0.01, 0.10], 0.15, inch)
    assert unlikely == (0.0, 0.0, False)                              # a low chance reads zero
    outside = outlook.lead_range(levels, [0.0, 0.0, 0.0, 0.01, 0.27], 0.27, inch)
    assert outside[2] and inch <= outside[0] < outside[1] == 0.27       # better than 1 in 5: from an inch or more up to the high end, not zero
    edge = outlook.lead_range(levels, [0.0, 0.0, 0.0, 0.0, 0.0], 0.05, inch)
    assert edge == (0.0, 0.0, False)


def test_the_first_morning_total_is_what_the_gauge_measured(monkeypatch):
    from vailsnow.extras import scorecard
    five_am = pd.Timestamp("2026-01-10T12:00:00")
    hours = pd.date_range(five_am - pd.Timedelta(hours=24), five_am, freq="h")
    rec = {"water": pd.DataFrame({"water_in": 0.01, "provisional": False}, index=hours), "depth": pd.Series(np.linspace(30.0, 33.0, len(hours)), index=hours)}
    monkeypatch.setattr(scorecard, "gauge_record", lambda resort, max_age_h=3.0, today=None: rec)
    monkeypatch.setattr(scorecard, "read_log", lambda path=None: pd.DataFrame([{"run": five_am - pd.Timedelta(hours=24), "start": five_am - pd.Timedelta(hours=24), "ratio": 15.0, "snow_share": 1.0}]))
    monkeypatch.setattr(scorecard, "target", lambda resort: {"name": "Gauge", "elev_ft": 10000})
    got = outlook.morning_measured(None, five_am)
    assert got["water_in"] == 0.24 and got["snow_in"] == 3.6          # 24 hours of 0.01 in at the ratio forecast for that day
    assert got["depth_change_in"] == 3.0 and got["hours_reported"] == 24

    def broken(resort, max_age_h=3.0, today=None):
        raise OSError("gauge service down")
    monkeypatch.setattr(scorecard, "gauge_record", broken)
    assert outlook.morning_measured(None, five_am) is None            # no reading: the line is left out, the build carries on
