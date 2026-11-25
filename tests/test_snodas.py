import pandas as pd
import pytest

from vailsnow.grids import snodas
from vailsnow.resort import load_resort


def test_window_covers_the_region_on_both_grid_versions():
    resort = load_resort()
    for date in (pd.Timestamp("2010-01-01"), pd.Timestamp("2020-01-01")):
        rows, cols, west, north = snodas.window(resort, date)
        assert west <= resort.region["west"] < west + snodas.CELL
        assert north >= resort.region["north"] > north - snodas.CELL
        assert (rows.stop - rows.start, cols.stop - cols.start) in {(72, 96), (73, 97), (72, 97), (73, 96)}
        assert 0 < rows.start < rows.stop < snodas.NROWS and 0 < cols.start < cols.stop < snodas.NCOLS


def test_snapshot_dates_bracket_each_event_and_respect_the_archive_start():
    ev = pd.DataFrame({"start": [pd.Timestamp("1999-01-01"), pd.Timestamp("2020-02-04")], "end": [pd.Timestamp("1999-01-03"), pd.Timestamp("2020-02-08")]})
    assert snodas.event_dates(ev) == [pd.Timestamp("2020-02-04"), pd.Timestamp("2020-02-09")]


def test_download_url():
    u = snodas.URL.format(y=2020, m=2, mon=snodas.MONTHS[1], d=4)
    assert u.endswith("/masked/2020/02_Feb/SNODAS_20200204.tar")
