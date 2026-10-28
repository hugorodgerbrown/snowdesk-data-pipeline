"""
tests/ingest/api/test_day_ratings_feed.py — The v2 day-ratings feed (SNOW-1270).

An item is the key, updated_at and every rated column, served under v2.
"""

from __future__ import annotations

import datetime

import pytest

from apps.ingest.api.day_ratings import DAY_RATINGS_FEED
from apps.ingest.api.paging import page as feed_page
from tests.factories import SourceDayRatingFactory

pytestmark = pytest.mark.django_db


def test_an_item_is_the_stored_rating() -> None:
    """The key and every column a client copies."""
    row = SourceDayRatingFactory.create(bulletin_id="b-1", bands=[{"band_id": "x"}])
    body = feed_page(DAY_RATINGS_FEED, since=None, cursor=None, limit=10)
    assert body["version"] == 2
    (item,) = body["results"]
    assert item == {
        "region_code": "CH-4115",
        "date": "2026-01-02",
        "updated_at": row.updated_at.isoformat(),
        "bulletin_id": "b-1",
        "min_rating": "considerable",
        "min_subdivision": "",
        "max_rating": "considerable",
        "max_subdivision": "",
        "am_rating": None,
        "am_subdivision": "",
        "pm_rating": None,
        "pm_subdivision": "",
        "source": "SLF",
        "bands": [{"band_id": "x"}],
        "version": row.version,
    }


def test_a_window_narrows_the_feed_by_day() -> None:
    """from/to are inclusive and filter on the rating's date (SNOW-1272)."""
    for day in (1, 2, 3):
        SourceDayRatingFactory.create(date=datetime.date(2026, 1, day))
    body = feed_page(
        DAY_RATINGS_FEED,
        since=None,
        cursor=None,
        limit=10,
        day_from=datetime.date(2026, 1, 2),
        day_to=datetime.date(2026, 1, 2),
    )
    assert body["count"] == 1
    assert [item["date"] for item in body["results"]] == ["2026-01-02"]
