"""Unit tests for the live pipeline's pure pieces: grid helpers, contours, wording."""
import json

import numpy as np
import pandas as pd
import pytest

from vailsnow.live import contours, forecast, grid
from vailsnow.model import final
from vailsnow.resort import ROOT


def test_block_mean_and_upsample_round_trip():
    a = np.arange(36, dtype=float).reshape(6, 6)
    small = grid.block_mean(a, 3)
    assert small.shape == (2, 2) and small[0, 0] == pytest.approx(a[:3, :3].mean())
    big = grid.upsample(small, (7, 6), 3)
    assert big.shape == (7, 6) and np.isnan(big[6]).all() and big[4, 1] == small[1, 0]
    a[0, 0] = np.nan
    assert grid.block_mean(a, 3)[0, 0] == pytest.approx(np.nanmean(a[:3, :3]))      # NaN cells are skipped, not spread


def test_lonlat_fit_recovers_cells_on_a_rotated_grid():
    rows, cols = np.mgrid[0:40, 0:60]
    lon = -106.40 + 0.00035 * cols + 0.00001 * rows           # a grid slightly rotated against longitude and latitude
    lat = 39.65 - 0.00027 * rows + 0.000008 * cols
    a, b, c, d, e, f = grid.lonlat_to_cell({"lon": lon, "lat": lat})
    assert a * lon[12, 33] + b * lat[12, 33] + c == pytest.approx(33, abs=0.01)
    assert d * lon[12, 33] + e * lat[12, 33] + f == pytest.approx(12, abs=0.01)


def test_contour_bands_cover_a_cone_in_order():
    n = 80
    yy, xx = np.mgrid[0:n, 0:n]
    x, y = 380_000 + 30.0 * np.arange(n), 4_385_000 - 30.0 * np.arange(n)
    field = 30.0 - np.hypot(xx - n / 2, yy - n / 2)           # 30 in at the centre, falling away
    field[field < 0.2] = np.nan
    gj = contours.bands(field, x, y, 26913)
    bands = [f for f in gj["features"] if f["properties"]["kind"] == "band"]
    lines = [f for f in gj["features"] if f["properties"]["kind"] == "line"]
    assert [f["properties"]["band"] for f in bands] == list(range(8))
    assert bands[-1]["properties"]["label"] == "24+" and len(lines) == 7
    lon, lat = bands[0]["geometry"]["coordinates"][0][0][0]
    assert -107 < lon < -106 and 39 < lat < 40                # written as longitude, latitude
    assert json.dumps(gj)                                      # plain JSON types only


def test_empty_field_gives_no_features():
    x, y = np.arange(5.0), np.arange(5.0)[::-1]
    assert contours.bands(np.full((5, 5), np.nan), x, y, 26913)["features"] == []


def test_wording_helpers():
    assert forecast.compass(315) == "northwest" and forecast.compass(5) == "north" and forecast.compass(269) == "west"
    assert [forecast.density_word(v) for v in (6, 9, 12, 16, 20)] == ["heavy", "dense", "average", "light", "blower"]


def test_hourly_shares_follow_hrrr_and_fall_back_to_even():
    apcp = np.zeros((48, 5, 5))
    apcp[6:12] = 2.0                                           # day 1: everything between hours 7 and 12
    raw = {"apcp": apcp, "hours": np.arange(1, 49), "gauge_cell": (2, 2)}
    s = forecast.hourly_shares(raw)
    assert s.shape == (3, 24) and np.allclose(s.sum(axis=1), 1.0)
    assert s[0, 6:12].sum() == pytest.approx(1.0) and np.allclose(s[1], 1 / 24) and np.allclose(s[2], 1 / 24)


def test_multi_day_range_sits_between_quadrature_and_the_plain_sum():
    likely, low, high = (np.array([[1.0], [1.0], [1.0]]) * k for k in (1.0, 0.4, 2.0))
    lo, hi = forecast.combine_ranges(likely, low, high)
    assert 3 * 0.4 < lo[0] < 3 - np.sqrt(3 * 0.6 ** 2) + 1e-9 or lo[0] == pytest.approx(0.75 * (3 - np.sqrt(3) * 0.6) + 0.25 * 1.2)
    assert 3 + np.sqrt(3) < hi[0] < 6.0
    one_lo, one_hi = forecast.combine_ranges(likely[:1], low[:1], high[:1])
    assert one_lo[0] == pytest.approx(0.4) and one_hi[0] == pytest.approx(2.0)            # a single day is unchanged


def test_explain_handles_a_dry_forecast():
    dry = {"day": 1, "start": "2026-11-24T12:00:00", "water_in": {"mean": 0.0, "q10": 0.0, "q50": 0.0, "q90": 0.02}, "wind_from": "west",
           "wind_600mb_ms": 12.0, "regime": "west", "temp_mid_f": 28, "temp_base_f": 38, "snow_level_ft": 9000.0, "quality": "average",
           "snow_to_liquid_mid": 13.0, "share_moved_by_wind": 0.0, "ridge_wind_mph": 19, "column_water_mm": 3.0, "humidity_700mb": 0.3,
           "upside_setup": False, "features": {}, "gauge": {"ratio": 13.0, "snow_share": 1.0, "temp_c": -2.0}}
    out = forecast.explain([dry], dry)
    assert out["headline"].startswith("Little or no snow") and "high end" not in out["headline"]
    wet = {**dry, "water_in": {"mean": 0.5, "q10": 0.2, "q50": 0.5, "q90": 1.0}}
    assert forecast.explain([wet], wet)["headline"].startswith("The most snow is likely on day 1: about 6 in at the mid-mountain gauge (range 3 to 13), from 0.50 in of water")
    assert "rain or wet snow" in out["points"][0]["text"] and "Snow quality" not in out["points"][0]["text"] and out["analogs"] == []


needs_models = pytest.mark.skipif(not (final.final_dir() / "precip_24.joblib").exists(), reason="run `vailsnow train` first")


@needs_models
def test_final_prediction_is_ordered_and_grows_with_the_forecast():
    cols = final.load(24)["cols"]
    base = {c: np.nan for c in cols}
    base.update(u600=10.0, v600=-10.0, spd600=14.0, steady600=0.95, t700=262.0, lapse=16.0, rh700=0.85, pwat=5.0, tw_site=262.0, wet_hours=20, peak6_share=0.4)
    def row(q):
        r = dict(base)
        r.update({c: q for c in cols if c.startswith(("qpf_", "prev_qpf", "gfs_qpf", "gefs_mean")) or c == "hrrr_snow_water"})
        return pd.DataFrame([r])[cols]
    small, big = final.predict(24, row(0.1)), final.predict(24, row(1.0))
    for p in (small, big):
        assert 0 <= p["q10"] <= p["q25"] <= p["q50"] <= p["q75"] <= p["q90"] and p["q10"] <= p["mean"] <= p["q90"]
    assert big["q50"] > small["q50"] and big["q90"] > small["q90"]


@needs_models
def test_analogs_skip_the_day_itself_and_stay_in_winter():
    lib = pd.read_parquet(final.final_dir() / "analog_library.parquet").dropna(subset=list(final.ANALOG_FEATURES))
    day = lib[lib["obs"] >= 0.5].iloc[0]
    found = final.analogs(day, k=3, not_near=day["start"])
    assert len(found) == 3
    dates = pd.to_datetime([a["date"] for a in found])
    assert all(abs((d - day["start"].normalize()).days) > 7 for d in dates) and all(d.month in (11, 12, 1, 2, 3, 4) for d in dates)
    assert all(abs((a - b).days) > 3 for i, a in enumerate(dates) for b in dates[i + 1:])        # three different storms


def test_published_forecast_files_are_complete():
    base = ROOT / "web" / "app" / "data"
    if not (base / "runs.json").exists():
        pytest.skip("no forecast built yet")
    for run in json.loads((base / "runs.json").read_text()):
        d = base / run["name"]
        meta, zones_, grid_ = (json.loads((d / f"{n}.json").read_text()) for n in ("meta", "zones", "grid"))
        assert len(zones_) == 17 and len(meta["hourly_curve_in"]) == 73 and meta["windows"] == [24, 48, 72]
        assert len(grid_["cells"]) == len(grid_["fallen_72"]) == len(grid_["elev_ft"])
        for z in zones_:
            likely, low, high = z["windows"]["72"]["fallen"]
            assert low <= likely + 0.05 and likely <= high + 0.05
        for h in meta["windows"]:
            for layer in ("fallen", "low", "high", "wind", "settled", "density"):
                assert (d / "contours" / f"{layer}_{h}.geojson").exists()
