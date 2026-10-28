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

from apps.ingest.api.paging import page as feed_page
from apps.ingest.api.reference import LOCATIONS_FEED, PASSES_FEED, RESORTS_FEED
from tests.factories import (
    SourceLocationFactory,
    SourcePassFactory,
    SourceResortFactory,
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
