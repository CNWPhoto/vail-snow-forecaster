import numpy as np
import pandas as pd
import pytest

from vailsnow.upper import download, features, wb2


class FakeStore(wb2.Store):
    """A store with a known grid and no network."""
    lat = np.arange(-90.0, 90.1, 1.5)
    lon = np.arange(0.0, 360.0, 1.5)


FAKE = FakeStore("fake", "none", 8, 240, 121)


def test_time_and_chunk_indexing():
    assert wb2.time_index(pd.Timestamp("1959-01-01 00:00")) == 0
    assert wb2.time_index(pd.Timestamp("1959-01-03 06:00")) == 9
    assert FAKE.chunk_of(pd.Timestamp("1959-01-03 06:00")) == 1
    assert FAKE.chunk_times(1)[0] == pd.Timestamp("1959-01-03 00:00") and len(FAKE.chunk_times(1)) == 8
    assert FAKE.key("temperature", 7, True) == "temperature/7.0.0.0" and FAKE.key("mslp", 7, False) == "mslp/7.0.0"


def test_nearest_point_and_bilinear_weights():
    assert FAKE.nearest(39.6, -106.35) == (169, 86)            # 253.5 E, 39.0 N
    w = FAKE.weights(39.6, -106.35)
    assert sum(x[2] for x in w) == pytest.approx(1.0)
    lon = sum(FAKE.lon[i] * wt for i, _, wt in w)
    lat = sum(FAKE.lat[j] * wt for _, j, wt in w)
    assert lon == pytest.approx(253.65) and lat == pytest.approx(39.6)
    assert FAKE.weights(39.0, 253.5) == [(169, 86, 1.0)]


@pytest.mark.parametrize("u, v, direction, name", [(10, 0, 270, "W"), (-10, 0, 90, "E"), (0, 10, 180, "S"), (0, -10, 0, "N"), (10, -10, 315, "NW")])
def test_wind_direction_is_where_it_blows_from(u, v, direction, name):
    speed, d = wb2.wind_from(float(u), float(v))
    assert d == pytest.approx(direction) and wb2.compass(d) == name and speed == pytest.approx(np.hypot(u, v))


def test_snotel_day_is_pacific_standard_time():
    t = pd.DatetimeIndex(["2020-02-06 06:00", "2020-02-06 12:00", "2020-02-07 06:00"])
    assert features.snotel_day(t).tolist() == [pd.Timestamp("2020-02-05"), pd.Timestamp("2020-02-06"), pd.Timestamp("2020-02-06")]


def test_event_chunks_cover_the_storm_and_skip_those_past_the_end_of_the_store():
    ev = pd.DataFrame({"start": [pd.Timestamp("2020-02-04"), pd.Timestamp("2023-01-09")], "end": [pd.Timestamp("2020-02-08"), pd.Timestamp("2023-01-12")]})
    chunks = download.event_chunks(FAKE, ev)
    assert chunks[0] == FAKE.chunk_of(pd.Timestamp("2020-02-03 06:00")) and chunks[-1] == FAKE.chunk_of(pd.Timestamp("2020-02-09 18:00"))
    assert chunks == list(range(chunks[0], chunks[-1] + 1))


def test_closed_low_detection():
    lon, lat = np.meshgrid(FAKE.lon, FAKE.lat, indexing="ij")
    flat = np.full(lon.shape, 5600.0)
    assert np.isnan(features._closed_low(flat, FAKE)[0])
    low_lon, low_lat = 253.5 - 4.5, 39.0                                     # a low three grid points west of Vail
    low = 5600.0 - 150.0 * np.exp(-(((lon - low_lon) * 0.78) ** 2 + (lat - low_lat) ** 2) / (2 * 3.0 ** 2))
    dist, bearing = features._closed_low(low, FAKE)
    assert 300 < dist < 500 and 240 < bearing < 290
    assert features._trough_lon(low, FAKE) == pytest.approx(low_lon - 360.0)
