"""
tests/ingest/api/test_weather_feed.py — Tests for the v2 weather feed (SNOW-1253).

What one item carries — keyed by the service's place uuid, the same columns
as v1 — that the feed says it is version 2, that a tombstoned place's rows
are still served, and that a page costs the same queries at any size.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.ingest.api.paging import page as feed_page
from apps.ingest.api.weather import WEATHER_FEED
from apps.ingest.models import WEATHER_COLUMNS
from tests.factories import SourceLocationFactory, SourceWeatherFactory

pytestmark = pytest.mark.django_db


def test_an_item_is_keyed_by_the_places_service_uuid() -> None:
    """``location`` is the SourceLocation's uuid; the columns are all sent."""
    row = SourceWeatherFactory.create(weather_code=71, hourly=[{"t": 1}])
    body = feed_page(WEATHER_FEED, since=None, cursor=None, limit=10)
    (item,) = body["results"]
    assert body["version"] == 2
    assert body["feed"] == "weather"
    assert item["location"] == str(row.location.uuid)
    assert item["observed_on"] == row.observed_on.isoformat()
    assert item["updated_at"] == row.updated_at.isoformat()
    assert set(item["fields"]) == set(WEATHER_COLUMNS)
    assert item["fields"]["weather_code"] == 71
    assert item["fields"]["hourly"] == [{"t": 1}]
    assert item["fields"]["sunrise"] == row.sunrise.isoformat()


def test_a_tombstoned_places_rows_are_still_served() -> None:
    """A tombstone stops the fetch, not the record of what was fetched."""
    SourceWeatherFactory.create(
        location=SourceLocationFactory.create(deleted_at=timezone.now())
    )
    body = feed_page(WEATHER_FEED, since=None, cursor=None, limit=10)
    assert body["count"] == 1


def test_a_page_costs_the_same_queries_at_any_size() -> None:
    """No N+1: the place is prefetched, so a page is two queries at any size."""
    for _ in range(13):
        SourceWeatherFactory.create()
    first = feed_page(WEATHER_FEED, since=None, cursor=None, limit=1)

    def queries(limit: int) -> int:
        """Count the queries for one cursor page of ``limit`` rows."""
        with CaptureQueriesContext(connection) as ctx:
            body = feed_page(
                WEATHER_FEED, since=None, cursor=first["next_cursor"], limit=limit
            )
        assert len(body["results"]) == limit
        return len(ctx.captured_queries)

    assert queries(2) == queries(12) == 2
