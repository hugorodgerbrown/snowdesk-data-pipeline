"""
tests/ingest/api/test_reference_feeds.py — Tests for the v2 places, passes and resorts feeds (SNOW-1271).

What an item carries — the service's uuid, ``deleted_at``, the document as
stored — that tombstones are served, and that a page costs the same
queries at any size.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.ingest.api.paging import MAX_LIMIT, page as feed_page
from apps.ingest.api.reference import (
    LOCATIONS_FEED,
    PASSES_FEED,
    RESORTS_FEED,
    SKI_TOURS_FEED,
    SKI_TOURS_MAX_LIMIT,
)
from tests.factories import (
    SourceLocationFactory,
    SourcePassFactory,
    SourceResortFactory,
    SourceSkiTourFactory,
)

pytestmark = pytest.mark.django_db


def test_a_place_item_carries_its_columns_and_document() -> None:
    """The four fetch columns under ``fields``; the host's extras as stored."""
    row = SourceLocationFactory.create(
        name="Mont Fort",
        latitude=46.10361,
        longitude=7.29889,
        elevation_m=3328.0,
        document={"kind": "PEAK", "centroid_of": []},
    )
    body = feed_page(LOCATIONS_FEED, since=None, cursor=None, limit=10)
    (item,) = body["results"]
    assert (body["feed"], body["version"]) == ("locations", 2)
    assert item == {
        "uuid": str(row.uuid),
        "updated_at": row.updated_at.isoformat(),
        "deleted_at": None,
        "fields": {
            "name": "Mont Fort",
            "latitude": 46.10361,
            "longitude": 7.29889,
            "elevation_m": 3328.0,
        },
        "document": {"kind": "PEAK", "centroid_of": []},
    }


@pytest.mark.parametrize(
    ("feed", "factory_class"),
    [(PASSES_FEED, SourcePassFactory), (RESORTS_FEED, SourceResortFactory)],
)
def test_a_document_item_is_served_as_stored_tombstones_included(
    feed: Any, factory_class: Any
) -> None:
    """A tombstone is a change like any other, so a client learns of it."""
    gone = factory_class.create(deleted_at=timezone.now())
    body = feed_page(feed, since=None, cursor=None, limit=10)
    (item,) = body["results"]
    assert body["version"] == 2
    assert item == {
        "uuid": str(gone.uuid),
        "slug": gone.slug,
        "updated_at": gone.updated_at.isoformat(),
        "deleted_at": gone.deleted_at.isoformat(),
        "document": gone.document,
    }


@pytest.mark.parametrize(
    ("feed", "factory_class"),
    [
        (LOCATIONS_FEED, SourceLocationFactory),
        (PASSES_FEED, SourcePassFactory),
        (RESORTS_FEED, SourceResortFactory),
    ],
)
def test_a_page_costs_the_same_at_any_size(feed: Any, factory_class: Any) -> None:
    """No relation is read per row."""
    factory_class.create_batch(2)
    with CaptureQueriesContext(connection) as small:
        feed_page(feed, since=None, cursor=None, limit=500)
    factory_class.create_batch(10)
    with CaptureQueriesContext(connection) as large:
        feed_page(feed, since=None, cursor=None, limit=500)
    assert len(large) == len(small)


def test_a_ski_tours_page_holds_no_more_than_its_own_ceiling(
    settings: Any,
) -> None:
    """A client asking for the most it may still gets 25 tours a page.

    500 rendered tours on one page killed production's web worker on every
    staging pull from 1 November 2026; the cursor carries a client the rest
    of the way.
    """
    del settings.FEED_MAX_LIMIT, settings.FEED_SKI_TOURS_MAX_LIMIT
    assert SKI_TOURS_MAX_LIMIT < MAX_LIMIT
    SourceSkiTourFactory.create_batch(SKI_TOURS_MAX_LIMIT + 1)
    first = feed_page(SKI_TOURS_FEED, since=None, cursor=None, limit=MAX_LIMIT)
    assert len(first["results"]) == SKI_TOURS_MAX_LIMIT
    assert first["count"] == SKI_TOURS_MAX_LIMIT + 1
    rest = feed_page(
        SKI_TOURS_FEED,
        since=None,
        cursor=first["next_cursor"],
        limit=MAX_LIMIT,
    )
    assert len(rest["results"]) == 1
    assert rest["next_cursor"] is None


def test_the_ski_tours_ceiling_is_read_from_settings(settings: Any) -> None:
    """FEED_SKI_TOURS_MAX_LIMIT moves the tours' ceiling with no code change."""
    settings.FEED_SKI_TOURS_MAX_LIMIT = "2"
    SourceSkiTourFactory.create_batch(3)
    body = feed_page(SKI_TOURS_FEED, since=None, cursor=None, limit=MAX_LIMIT)
    assert len(body["results"]) == 2


def test_the_shared_ceiling_holds_a_feed_whose_own_is_higher(settings: Any) -> None:
    """FEED_MAX_LIMIT caps every feed, the tours' own ceiling included."""
    settings.FEED_MAX_LIMIT = 1
    settings.FEED_SKI_TOURS_MAX_LIMIT = 25
    SourceSkiTourFactory.create_batch(2)
    body = feed_page(SKI_TOURS_FEED, since=None, cursor=None, limit=MAX_LIMIT)
    assert len(body["results"]) == 1


def test_a_feed_without_its_own_ceiling_is_held_to_the_shared_one(
    settings: Any,
) -> None:
    """The passes feed has no ceiling of its own; FEED_MAX_LIMIT holds it."""
    assert PASSES_FEED.max_limit is None
    settings.FEED_MAX_LIMIT = 2
    SourcePassFactory.create_batch(3)
    body = feed_page(PASSES_FEED, since=None, cursor=None, limit=MAX_LIMIT)
    assert len(body["results"]) == 2
