"""
tests/ingest/api/test_bulletins_feed.py — Tests for the bulletins feed and its paging.

Moved from tests/data_feed/test_feeds.py by SNOW-1250, when the bulletins feed
began reading ingest's ``SourceBulletin`` and the paging rule moved to
``apps/ingest/api/paging.py``. What one item carries, the keyset paging —
every row exactly once across pages, ``since`` filtering, the cursor round
trip — parameter parsing, and a page's fixed query count.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.ingest.api.bulletins import BULLETINS_FEED
from apps.ingest.api.paging import (
    FeedQueryError,
    decode_cursor,
    encode_cursor,
    page as feed_page,
    parse_limit,
    parse_since,
)
from tests.factories import SourceBulletinFactory


def _all() -> list[dict]:
    """Return every bulletins item, following the cursor to the end."""
    items: list[dict] = []
    cursor = None
    while True:
        body = feed_page(BULLETINS_FEED, since=None, cursor=cursor, limit=2)
        items += body["results"]
        cursor = body["next_cursor"]
        if cursor is None:
            return items


@pytest.mark.django_db
def test_an_item_is_the_stored_caaml_and_pdf() -> None:
    """The CAAML is served exactly as ingest stored it, with its PDF link."""
    row = SourceBulletinFactory.create(
        bulletin_id="b-1",
        pdf_url="https://example.org/b-1.pdf",
        caaml={"bulletinID": "b-1"},
    )
    (item,) = _all()
    assert item == {
        "bulletin_id": "b-1",
        "updated_at": row.updated_at.isoformat(),
        "pdf_url": "https://example.org/b-1.pdf",
        "caaml": {"bulletinID": "b-1"},
    }


@pytest.mark.django_db
class TestPaging:
    """The keyset walk."""

    def test_every_row_once_across_pages(self) -> None:
        """Five rows over pages of two: each once, oldest update first."""
        made = [SourceBulletinFactory.create() for _ in range(5)]
        assert [i["bulletin_id"] for i in _all()] == [b.bulletin_id for b in made]

    def test_count_is_on_the_first_page_only(self) -> None:
        """count is one query on the first page, absent after."""
        for _ in range(3):
            SourceBulletinFactory.create()
        first = feed_page(BULLETINS_FEED, since=None, cursor=None, limit=2)
        assert first["count"] == 3
        second = feed_page(
            BULLETINS_FEED, since=None, cursor=first["next_cursor"], limit=2
        )
        assert "count" not in second
        assert second["next_cursor"] is None

    def test_since_filters_on_updated_at(self) -> None:
        """Only rows updated at or after since are served."""
        old = SourceBulletinFactory.create()
        new = SourceBulletinFactory.create()
        cut = timezone.now() - dt.timedelta(hours=1)
        type(old).objects.filter(pk=old.pk).update(
            updated_at=cut - dt.timedelta(days=1)
        )
        body = feed_page(BULLETINS_FEED, since=cut, cursor=None, limit=10)
        assert [i["bulletin_id"] for i in body["results"]] == [new.bulletin_id]

    def test_rows_sharing_a_timestamp_are_split_by_id(self) -> None:
        """Equal updated_at values page by id, so none is skipped or repeated."""
        rows = [SourceBulletinFactory.create() for _ in range(3)]
        stamp = timezone.now()
        type(rows[0]).objects.update(updated_at=stamp)
        assert len(_all()) == 3

    def test_cursor_round_trips(self) -> None:
        """A cursor decodes to the pair it was made from."""
        stamp = dt.datetime(2026, 10, 23, 6, 30, 1, 5, tzinfo=dt.UTC)
        assert decode_cursor(encode_cursor(stamp, 42)) == (stamp, 42)

    @pytest.mark.parametrize("cursor", ["!!!", "bm90LWEtY3Vyc29y", "eHw0Mg"])
    def test_a_foreign_cursor_is_refused(self, cursor: str) -> None:
        """Garbage, a missing separator and a bad timestamp all raise."""
        with pytest.raises(FeedQueryError):
            decode_cursor(cursor)


class TestParameters:
    """since and limit parsing."""

    def test_since(self) -> None:
        """An aware datetime parses; absent is None; naive is refused."""
        assert parse_since(None) is None
        assert parse_since("2026-10-01T00:00:00+00:00") == dt.datetime(
            2026, 10, 1, tzinfo=dt.UTC
        )
        with pytest.raises(FeedQueryError):
            parse_since("2026-10-01T00:00:00")

    def test_limit(self) -> None:
        """Defaulted, capped at MAX_LIMIT, refused below one."""
        assert parse_limit(None) == 100
        assert parse_limit("9999") == 500
        assert parse_limit("7") == 7
        for bad in ("0", "-1", "ten"):
            with pytest.raises(FeedQueryError):
                parse_limit(bad)


@pytest.mark.django_db
def test_a_page_costs_the_same_queries_at_any_size() -> None:
    """No N+1: a page of 12 rows reads the database as often as a page of 2."""
    for _ in range(13):
        SourceBulletinFactory.create()
    first = feed_page(BULLETINS_FEED, since=None, cursor=None, limit=1)

    def queries(limit: int) -> int:
        """Count the queries for one cursor page of ``limit`` rows."""
        with CaptureQueriesContext(connection) as ctx:
            body = feed_page(
                BULLETINS_FEED, since=None, cursor=first["next_cursor"], limit=limit
            )
        assert len(body["results"]) == limit
        return len(ctx.captured_queries)

    assert queries(2) == queries(12) == 1
