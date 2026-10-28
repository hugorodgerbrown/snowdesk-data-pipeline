"""
tests/data_feed/test_feeds.py — Tests for apps.data_feed.feeds (SNOW-1197).

What each host feed may read (the allowlist — no favourite's weather, no
anonymous location, no resort without a slug), what an item looks like, and
a page's fixed query count. The bulletins feed and the paging rule moved to
apps.ingest with SNOW-1250 and are tested in tests/ingest/api/.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.data_feed.feeds import FEEDS
from apps.ingest.public import feed_page
from apps.locations.models import ResortLocation
from apps.regions.models import Resort
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    PassFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)


def _all(name: str, **kwargs: Any) -> list[dict]:
    """Return every item of feed ``name``, following the cursor to the end."""
    items: list[dict] = []
    cursor = None
    while True:
        body = feed_page(FEEDS[name], since=None, cursor=cursor, limit=2, **kwargs)
        items += body["results"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


@pytest.mark.django_db
class TestAllowlist:
    """Each feed reads only what may leave production."""

    def test_weather_is_only_for_curated_locations(self) -> None:
        """A favourite's pin is fetched for weather, but its weather never leaves."""
        curated = ResortLocationFactory.create().location
        WeatherFactory.create(location=curated)
        WeatherFactory.create(location=FavouriteFactory.create().location)
        assert [i["location"] for i in _all("weather")] == [str(curated.uuid)]

    def test_locations_are_only_the_curated_estate(self) -> None:
        """An anonymous point (a favourite, a report) is never served."""
        linked = ResortLocationFactory.create().location
        centroid = LocationFactory.create()
        MicroRegionFactory.create(region_id="CH-9001", centroid_location=centroid)
        FavouriteFactory.create()
        LocationFactory.create(name="Named but reached by nothing")
        served = {i["uuid"] for i in _all("locations")}
        assert served == {str(linked.uuid), str(centroid.uuid)}

    def test_a_resort_without_a_slug_is_not_served(self) -> None:
        """The slug is the key on the wire; a row with none cannot be sent."""
        ResortFactory.create(name="Keyed")
        unkeyed = ResortFactory.create(name="Unkeyed")
        Resort.objects.filter(pk=unkeyed.pk).update(slug=None)
        assert [i["slug"] for i in _all("resorts")] == ["keyed"]


@pytest.mark.django_db
class TestItems:
    """What one item carries."""

    def test_weather_carries_its_columns(self) -> None:
        """A weather item is keyed by location uuid and day."""
        location = ResortLocationFactory.create().location
        WeatherFactory.create(location=location, observed_on=dt.date(2026, 10, 1))
        (item,) = _all("weather")
        assert item["observed_on"] == "2026-10-01"
        assert item["fields"]["temperature_2m_max"] == 4.5
        assert item["fields"]["forecast"] == []

    def test_location_names_the_regions_it_is_centroid_of(self) -> None:
        """centroid_of carries region codes, not ids."""
        location = LocationFactory.create(name="Centre")
        MicroRegionFactory.create(region_id="CH-9002", centroid_location=location)
        (item,) = _all("locations")
        assert item["centroid_of"] == ["CH-9002"]
        assert item["fields"]["name"] == "Centre"

    def test_resort_carries_its_relations_as_natural_keys(self) -> None:
        """Region, pin, passes and links all travel as natural keys."""
        resort = ResortFactory.create(
            name="Verbier", geocoded=True, region__region_id="CH-4224"
        )
        resort.passes.add(PassFactory.create(slug="magic-pass", name="Magic Pass"))
        link = ResortLocationFactory.create(
            resort=resort, role=ResortLocation.ROLE.BASE, is_primary=True
        )
        (item,) = _all("resorts")
        assert item["slug"] == "verbier"
        assert item["region"] == "CH-4224"
        assert resort.location is not None
        assert item["location"] == str(resort.location.uuid)
        assert item["passes"] == [
            {"slug": "magic-pass", "name": "Magic Pass", "website": ""}
        ]
        assert item["locations"] == [
            {"location": str(link.location.uuid), "role": "BASE", "is_primary": True}
        ]
        assert item["fields"]["latitude"] == 46.1


@pytest.mark.django_db
def test_weather_queryset_is_not_a_favourites_query() -> None:
    """The weather feed's filter reads the public() set, nothing wider."""
    sql = str(FEEDS["weather"].queryset().query)
    assert "favourites_favourite" not in sql
    assert Weather._meta.db_table in sql


def _build(name: str, n: int) -> None:
    """Make ``n`` rows that feed ``name`` serves, each with every relation."""
    for _ in range(n):
        if name == "weather":
            WeatherFactory.create(location=ResortLocationFactory.create().location)
        elif name == "locations":
            MicroRegionFactory.create(centroid_location=LocationFactory.create())
        else:
            resort = ResortFactory.create(geocoded=True)
            resort.passes.add(PassFactory.create())
            ResortLocationFactory.create(resort=resort)


@pytest.mark.django_db
@pytest.mark.parametrize("name", [name for name in FEEDS if name != "bulletins"])
def test_a_page_costs_the_same_queries_at_any_size(name: str) -> None:
    """No N+1: a page of 12 rows reads the database as often as a page of 2."""
    _build(name, 13)
    first = feed_page(FEEDS[name], since=None, cursor=None, limit=1)

    def queries(limit: int) -> int:
        """Count the queries for one cursor page of ``limit`` rows."""
        with CaptureQueriesContext(connection) as ctx:
            body = feed_page(
                FEEDS[name], since=None, cursor=first["next_cursor"], limit=limit
            )
        assert len(body["results"]) == limit
        return len(ctx.captured_queries)

    assert queries(2) == queries(12)
