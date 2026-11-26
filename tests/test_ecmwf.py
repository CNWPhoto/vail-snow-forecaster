"""The European ensemble: which steps are kept, which members, and the model inputs made from them."""
import json

import numpy as np
import pandas as pd

from vailsnow.fcst import ecmwf


def test_steps_end_at_5am():
    assert ecmwf.steps_for(pd.Timestamp("2026-11-25 00:00"))[:3] == [12, 36, 60]      # a 00z run: 12z is twelve hours in
    assert ecmwf.steps_for(pd.Timestamp("2026-11-25 12:00"))[:3] == [24, 48, 72]
    assert ecmwf.steps_for(pd.Timestamp("2026-11-25 00:00"))[-1] == 276
    assert ecmwf.steps_for(pd.Timestamp("2026-11-25 12:00"))[-1] == 264


def test_only_the_first_perturbed_members_precipitation_is_taken():
    rows = [{"type": "cf", "param": "tp", "_offset": 0, "_length": 10},
            {"type": "pf", "number": "3", "param": "tp", "_offset": 100, "_length": 10},
            {"type": "pf", "number": "1", "param": "tp", "_offset": 200, "_length": 12},
            {"type": "pf", "number": "1", "param": "2t", "_offset": 300, "_length": 10},
            {"type": "pf", "number": "40", "param": "tp", "_offset": 400, "_length": 10}]
    text = "\n".join(json.dumps(r) for r in rows)
    assert ecmwf.member_ranges(text, members=25) == [(1, 200, 12), (3, 100, 10)]


def _run(members: int = 4) -> dict:
    lat, lon = np.meshgrid([40.0, 39.75, 39.5], [-106.5, -106.25], indexing="ij")
    steps = np.array([12, 36, 60, 84])
    tp = np.zeros((4, members, 3, 2), dtype=np.float32)
    for m in range(members):                                   # member m: 25.4 mm (one inch) times (m + 1) in the second day
        tp[1:, m] = 25.4 * (m + 1)
    tp[3:] += 12.7                                             # and half an inch for everyone in the fourth
    return {"steps": steps, "tp": tp, "lat": lat.astype(np.float32), "lon": lon.astype(np.float32)}


def test_weights_are_bilinear_and_do_not_care_which_way_the_grid_runs():
    d = _run()
    w = ecmwf.weights(d["lat"], d["lon"], 39.875, -106.375)
    assert w.sum() == 1.0 and np.allclose(w[:2], 0.25) and not w[2].any()
    flipped = ecmwf.weights(d["lat"][::-1], d["lon"][::-1], 39.875, -106.375)
    assert np.allclose(flipped[::-1], w)
    edge = ecmwf.weights(d["lat"], d["lon"], 41.0, -106.5)     # outside the window: the nearest edge, never an extrapolation
    assert edge[0, 0] == 1.0


def test_day_totals_are_differences_of_the_running_total():
    d = _run()
    assert ecmwf.day_totals(d, 0) is None                      # a 00z run holds no 0 to 24 h day here
    assert np.allclose(ecmwf.day_totals(d, 12)[:, 0, 0], [25.4, 50.8, 76.2, 101.6])
    assert np.allclose(ecmwf.day_totals(d, 36), 0.0)
    assert np.allclose(ecmwf.day_totals(d, 60), 12.7)
    assert ecmwf.day_totals(d, 84) is None                     # past the end of the run


def test_inputs_are_inches_at_the_gauge():
    d = _run(members=12)
    w = ecmwf.weights(d["lat"], d["lon"], 39.75, -106.4)
    f = ecmwf.features(d, w, 12)
    assert np.isclose(f["ec_mean"], 6.5) and np.isclose(f["ec_area"], 6.5)       # members 1..12 inches: mean 6.5
    assert np.isnan(f["ec_prev"]) and np.isclose(f["ec_next"], 0.0)
    assert np.isclose(f["ec_p90"], np.quantile(np.arange(1, 13), 0.9)) and f["ec_spread"] > 3
    nothing = ecmwf.features(None, None, 12)
    assert all(np.isnan(v) for v in nothing.values())
    mean_only = ecmwf.features({**d, "tp": d["tp"].mean(axis=1, keepdims=True)}, w, 12)      # the 2018 to 2022 archive
    assert np.isclose(mean_only["ec_mean"], 6.5) and np.isnan(mean_only["ec_spread"]) and np.isnan(mean_only["ec_p90"])
