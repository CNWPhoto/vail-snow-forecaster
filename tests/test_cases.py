import pandas as pd

from vailsnow.catalog import cases


def test_strip_html_drops_scripts_and_tags():
    assert cases.strip_html("<p>Heavy&nbsp;snow <b>fell</b></p><script>var x=1;</script>") == "Heavy snow fell"


def test_index_links_keep_winter_write_ups_only():
    page = ('<a href="https://www.weather.gov/gjt/WinterStormSummary_Feb04-07_2020">February 4-7 Winter Storm Summary</a>'
            '<a href="https://www.weather.gov/gjt/2018_October4_SevereThunderstorms">October 4 Severe Thunderstorms</a>'
            '<a href="https://www.weather.gov/gjt/HistoricThanksgiving1919SnowStorm_112719">Thanksgiving 1919 Record Snow Storm</a>'
            '<a href="https://example.com/other">Snow elsewhere</a>')
    assert cases.index_links(page) == [("February 4-7 Winter Storm Summary", "https://www.weather.gov/gjt/WinterStormSummary_Feb04-07_2020")]


def test_dates_take_the_year_from_title_then_url_then_text():
    a, b = cases.parse_dates("February 4-7 Winter Storm Summary", "https://www.weather.gov/gjt/WinterStormSummary_Feb04-07_2020", "")
    assert (a, b) == (pd.Timestamp("2020-02-04"), pd.Timestamp("2020-02-07"))
    a, b = cases.parse_dates("February 2-4, 2021 Winter Storm", "https://www.weather.gov/gjt/x", "written in 2022")
    assert (a, b) == (pd.Timestamp("2021-02-02"), pd.Timestamp("2021-02-04"))
    a, b = cases.parse_dates("December 27-28 Winter Storm Summary", "https://www.weather.gov/gjt/December27-28WinterStormSummary", "On December 27, 2016 a storm")
    assert a == pd.Timestamp("2016-12-27")
    assert cases.parse_dates("Winter Storm Summary", "u", "2016") is None
    one = cases.parse_dates("January 9th Rare Ice Storm and Snow Summary", "u", "in 2017")
    assert one == (pd.Timestamp("2017-01-09"), pd.Timestamp("2017-01-09"))
