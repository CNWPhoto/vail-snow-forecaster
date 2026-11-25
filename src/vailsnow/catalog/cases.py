"""NWS Grand Junction winter-storm write-ups as a labelled case library.

The pages are US Government works. Each is fetched once, reduced to text, dated,
and linked to the catalogue storms it overlaps.
"""
from __future__ import annotations

import html
import json
import re

import pandas as pd

from ..fetch import cached_get
from ..resort import cache_dir, data_dir

INDEX = "https://www.weather.gov/gjt/events"
MONTHS = {m: i + 1 for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                           "september", "october", "november", "december"])}
WINTER_WORDS = re.compile(r"winter|snow|blizzard", re.I)


def strip_html(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", page))).strip()


def index_links(page: str) -> list[tuple[str, str]]:
    """(title, url) for every winter write-up on the events page."""
    out, seen = [], set()
    for href, label in re.findall(r'(?is)<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page):
        title = strip_html(label)
        if "/gjt/" in href and WINTER_WORDS.search(title) and "1919" not in title and href not in seen:
            seen.add(href)
            out.append((title, href.replace("http://", "https://")))
    return out


def parse_dates(title: str, url: str, text: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """Date range of a write-up from a title like 'February 4-7 Winter Storm Summary'. The year
    comes from the title, else the URL, else the first year in the page text."""
    m = re.search(r"(?i)\b(" + "|".join(MONTHS) + r")\w*\s+(\d{1,2})(?:\s*(?:-|to|through)\s*(\d{1,2}))?", title)
    if not m:
        return None
    year = None
    for source in (title, url, text):
        y = re.search(r"(?<!\d)(20[12]\d)(?!\d)", source)
        if y:
            year = int(y.group(1))
            break
    if year is None:
        return None
    month, d0, d1 = MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3) or m.group(2))
    return pd.Timestamp(year, month, d0), pd.Timestamp(year, month, d1)


def build() -> dict:
    events = pd.read_parquet(data_dir() / "catalog" / "events.parquet")
    index = cached_get(INDEX, cache_dir() / "gjt_events" / "index.html").read_text(errors="ignore")
    cases = []
    for title, url in index_links(index):
        slug = re.sub(r"[^A-Za-z0-9]+", "_", url.rsplit("/", 1)[-1])[:80]
        try:
            text = strip_html(cached_get(url, cache_dir() / "gjt_events" / f"{slug}.html").read_text(errors="ignore"))
        except Exception as e:      # a dead link should not stop the rest
            cases.append({"title": title, "url": url, "error": repr(e)[:100]})
            continue
        dates = parse_dates(title, url, text)
        case = {"title": title, "url": url, "chars": len(text)}
        if dates:
            a, b = dates
            hit = events[(events["start"] <= b + pd.Timedelta(days=1)) & (events["end"] >= a - pd.Timedelta(days=1))]
            case.update(start=str(a.date()), end=str(b.date()), events=hit["event_id"].tolist(),
                        vail_swe_in=round(float(hit["swe_in"].sum()), 1))
        case["vail_mentions"] = [s.strip() for s in re.findall(r"[^.]*\bVail\b[^.]*\.", text)][:6]
        case["text"] = text
        cases.append(case)
    out = data_dir() / "catalog" / "case_library.json"
    out.write_text(json.dumps(cases, indent=1))
    return {"cases": len(cases), "dated": sum("start" in c for c in cases), "linked": sum(bool(c.get("events")) for c in cases)}
