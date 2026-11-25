import numpy as np
import pandas as pd
import pytest

from vailsnow.catalog import regimes as rg


@pytest.mark.parametrize("direction, speed, name", [
    (315, 20, "northwest"), (292.5, 20, "northwest"), (289, 20, "west"), (250, 15, "west"), (225, 15, "southwest"),
    (0, 12, "north"), (350, 12, "north"), (20, 12, "north"), (90, 12, "east or south"), (180, 12, "east or south"),
    (315, 3, "weak flow"), (np.nan, 10, "unknown"),
])
def test_sector_names(direction, speed, name):
    assert rg.sector(direction, speed) == name


def test_a_nearby_closed_low_overrides_the_wind_sector():
    row = pd.Series({"dir600": 315.0, "spd600": 20.0, "steady600": 0.9, "closed_low_km": 400.0})
    assert rg.event_regime(row) == "closed low"
    row["closed_low_km"] = 1500.0
    assert rg.event_regime(row) == "northwest"
    row["closed_low_km"] = np.nan
    assert rg.event_regime(row) == "northwest"
    row["steady600"] = 0.1                       # wind boxed the compass: no single direction
    assert rg.event_regime(row) == "weak flow"


def test_ratio_interval_brackets_the_ratio_and_resamples_whole_seasons():
    rng = np.random.default_rng(1)
    den = pd.Series(rng.gamma(2.0, 0.3, 600))
    num = den * 1.5 + rng.normal(0, 0.05, 600)
    r = rg.ratio_with_interval(num, den, np.repeat(np.arange(20), 30))
    assert r["lo"] <= r["ratio"] <= r["hi"] and r["ratio"] == pytest.approx(1.5, abs=0.05)
    one_season = rg.ratio_with_interval(num, den, np.zeros(600, dtype=int))
    assert one_season["lo"] == one_season["hi"] == one_season["ratio"]          # nothing to resample


def test_site_ratios_use_all_days_not_selected_storms():
    days = pd.date_range("2019-11-01", "2020-04-30", freq="D")
    regime = pd.Series(np.where(np.arange(len(days)) % 2 == 0, "northwest", "west"), index=days, name="regime")
    gains = pd.DataFrame({"vail": np.where(regime == "northwest", 0.4, 0.2), "other": 0.2}, index=days)
    out = rg.by_regime_site_ratios(gains, regime, min_days=30).set_index("regime")
    assert out.loc["northwest", "ratio"] == pytest.approx(2.0) and out.loc["west", "ratio"] == pytest.approx(1.0)
    assert out.loc["all days", "ratio"] == pytest.approx(1.5)
    shares = rg.regime_shares(gains, regime, ["vail"]).set_index("regime")
    assert shares.loc["northwest", "vail_share"] == pytest.approx(2 / 3, abs=0.01)


def test_valley_reports_are_credited_to_the_previous_day():
    days = pd.date_range("2019-11-01", "2020-02-28", freq="D")
    regime = pd.Series("northwest", index=days, name="regime")
    rep = pd.date_range("2019-11-02", "2020-02-29", freq="D")
    v = pd.concat([pd.DataFrame({"site": s, "date": rep, "snow": x, "pcpn": x / 10, "multi_day": False})
                   for s, x in (("east_vail", 1.2), ("west_vail", 1.0))])
    out = rg.valley_east_west(v, regime)
    snow = out[(out["measure"] == "snow") & (out["regime"] == "northwest")].iloc[0]
    assert snow["ratio"] == pytest.approx(1.2) and snow["days"] == len(days)
