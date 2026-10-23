"""
tests/data_feed/test_feeds.py — Tests for apps.data_feed.feeds (SNOW-1197).

Three things: what each feed may read (the allowlist — no favourite's
weather, no anonymous location, no resort without a slug), what an item
looks like, and the keyset paging — every row exactly once across pages,
``since`` filtering, and the cursor round trip.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.data_feed import feeds
from apps.data_feed.feeds import FEEDS, FeedQueryError
from apps.locations.models import ResortLocation
from apps.regions.models import Resort
from apps.weather.models import Weather
from tests.factories import (
    BulletinFactory,
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    PassFactory,
    ResortFactory,
    ResortLocationFactory,
    WeatherFactory,
)


def _all(name: str, **kwargs: object) -> list[dict]:
    """Return every item of feed ``name``, following the cursor to the end."""
    items: list[dict] = []
    cursor = None
    while True:
        body = feeds.page(FEEDS[name], since=None, cursor=cursor, limit=2, **kwargs)
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

    def test_bulletin_carries_its_caaml_and_pdf(self) -> None:
        """The CAAML is the Feature envelope's properties, as stored."""
        BulletinFactory.create(
            bulletin_id="b-1",
            pdf_url="https://example.org/b-1.pdf",
            raw_data={"type": "Feature", "properties": {"bulletinID": "b-1"}},
        )
        (item,) = _all("bulletins")
        assert item["bulletin_id"] == "b-1"
        assert item["pdf_url"] == "https://example.org/b-1.pdf"
        assert item["caaml"] == {"bulletinID": "b-1"}

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
class TestPaging:
    """The keyset walk."""

    def test_every_row_once_across_pages(self) -> None:
        """Five rows over pages of two: each once, oldest update first."""
        made = [BulletinFactory.create() for _ in range(5)]
        assert [i["bulletin_id"] for i in _all("bulletins")] == [
            b.bulletin_id for b in made
        ]

    def test_count_is_on_the_first_page_only(self) -> None:
        """count is one query on the first page, absent after."""
        for _ in range(3):
            BulletinFactory.create()
        first = feeds.page(FEEDS["bulletins"], since=None, cursor=None, limit=2)
        assert first["count"] == 3
        second = feeds.page(
            FEEDS["bulletins"], since=None, cursor=first["next_cursor"], limit=2
        )
        assert "count" not in second
        assert second["next_cursor"] is None

    def test_since_filters_on_updated_at(self) -> None:
        """Only rows updated at or after since are served."""
        old = BulletinFactory.create()
        new = BulletinFactory.create()
        cut = timezone.now() - dt.timedelta(hours=1)
        type(old).objects.filter(pk=old.pk).update(
            updated_at=cut - dt.timedelta(days=1)
        )
        body = feeds.page(FEEDS["bulletins"], since=cut, cursor=None, limit=10)
        assert [i["bulletin_id"] for i in body["results"]] == [new.bulletin_id]

    def test_rows_sharing_a_timestamp_are_split_by_id(self) -> None:
        """Equal updated_at values page by id, so none is skipped or repeated."""
        rows = [BulletinFactory.create() for _ in range(3)]
        stamp = timezone.now()
        type(rows[0]).objects.update(updated_at=stamp)
        assert len(_all("bulletins")) == 3

    def test_cursor_round_trips(self) -> None:
        """A cursor decodes to the pair it was made from."""
        stamp = dt.datetime(2026, 10, 23, 6, 30, 1, 5, tzinfo=dt.UTC)
        assert feeds.decode_cursor(feeds.encode_cursor(stamp, 42)) == (stamp, 42)

    @pytest.mark.parametrize("cursor", ["!!!", "bm90LWEtY3Vyc29y", "eHw0Mg"])
    def test_a_foreign_cursor_is_refused(self, cursor: str) -> None:
        """Garbage, a missing separator and a bad timestamp all raise."""
        with pytest.raises(FeedQueryError):
            feeds.decode_cursor(cursor)


class TestParameters:
    """since and limit parsing."""

    def test_since(self) -> None:
        """An aware datetime parses; absent is None; naive is refused."""
        assert feeds.parse_since(None) is None
        assert feeds.parse_since("2026-10-01T00:00:00+00:00") == dt.datetime(
            2026, 10, 1, tzinfo=dt.UTC
        )
        with pytest.raises(FeedQueryError):
            feeds.parse_since("2026-10-01T00:00:00")

    def test_limit(self) -> None:
        """Defaulted, capped at MAX_LIMIT, refused below one."""
        assert feeds.parse_limit(None) == 100
        assert feeds.parse_limit("9999") == 500
        assert feeds.parse_limit("7") == 7
        for bad in ("0", "-1", "ten"):
            with pytest.raises(FeedQueryError):
                feeds.parse_limit(bad)


def test_weather_queryset_is_not_a_favourites_query() -> None:
    """The weather feed's filter reads the public() set, nothing wider."""
    sql = str(FEEDS["weather"].queryset().query)
    assert "favourites_favourite" not in sql
    assert Weather._meta.db_table in sql
