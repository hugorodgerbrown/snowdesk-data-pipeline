"""
tests/ingest/api/test_bulletins_feed.py — Tests for the bulletins feed and its paging.

Moved from tests/data_feed/test_feeds.py by SNOW-1250, when the bulletins feed
began reading ingest's ``SourceBulletin`` and the paging rule moved to
``apps/ingest/api/paging.py``. What one item carries, the keyset paging —
every row exactly once across pages, ``since`` filtering, the cursor round
trip — parameter parsing, and a page's fixed query count.

And the ceilings at SLF's 2026/27 per-region volume (SNOW-998): ~149
bulletins an issue, two issues a day, every item about the size of the
per-region sentinels.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.ingest.api.bulletins import BULLETINS_FEED
from apps.ingest.api.paging import (
    DEFAULT_LIMIT,
    Feed,
    FeedQueryError,
    decode_cursor,
    encode_cursor,
    page as feed_page,
    parse_day,
    parse_limit,
    parse_since,
)
from apps.ingest.models import SourceBulletin
from apps.ingest.normalise.render_model import build_render_model
from tests.factories import SourceBulletinFactory

_SLICE = (
    Path(__file__).resolve().parents[2]
    / "sentinels"
    / "slf"
    / "per-region-slice.ndjson"
)
# SLF's 2026/27 export: one bulletin per micro-region, two issues a day.
_PER_REGION_ISSUE = 149
_ISSUES_PER_DAY = 2
_PAGE_CEILING_BYTES = 5_000_000


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
    """The CAAML is served exactly as ingest stored it, with its PDF link.

    And the service's render model beside it (SNOW-1270).
    """
    row = SourceBulletinFactory.create(
        bulletin_id="b-1",
        pdf_url="https://example.org/b-1.pdf",
        caaml={"bulletinID": "b-1"},
        render_model={"version": 9},
    )
    (item,) = _all()
    assert item == {
        "bulletin_id": "b-1",
        "updated_at": row.updated_at.isoformat(),
        "pdf_url": "https://example.org/b-1.pdf",
        "caaml": {"bulletinID": "b-1"},
        "render_model": {"version": 9},
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


@pytest.mark.django_db
def test_a_window_reads_the_target_day() -> None:
    """from/to filter bulletins on the day they forecast (SNOW-1272)."""
    SourceBulletinFactory.create(target_date=dt.date(2026, 2, 9))
    SourceBulletinFactory.create(target_date=dt.date(2026, 3, 1))
    SourceBulletinFactory.create()
    body = feed_page(
        BULLETINS_FEED,
        since=None,
        cursor=None,
        limit=10,
        day_from=dt.date(2026, 2, 9),
        day_to=dt.date(2026, 2, 15),
    )
    assert body["count"] == 1


def test_a_window_is_refused_on_a_feed_with_no_day() -> None:
    """A feed with no day field cannot be windowed."""
    undated = Feed("x", SourceBulletinFactory._meta.model.objects.none, dict)
    with pytest.raises(FeedQueryError):
        feed_page(
            undated,
            since=None,
            cursor=None,
            limit=1,
            day_from=dt.date(2026, 1, 1),
        )


def test_parse_day() -> None:
    """An absent day is None; a bad one names its parameter."""
    assert parse_day(None, "from") is None
    assert parse_day("2026-02-09", "from") == dt.date(2026, 2, 9)
    with pytest.raises(FeedQueryError) as error:
        parse_day("2026-13-40", "to")
    assert error.value.param == "to"


# ---------------------------------------------------------------------------
# SNOW-998: ceilings at the per-region volume
# ---------------------------------------------------------------------------


def _pages_for_day(day: dt.date) -> list[dict]:
    """Walk one target day of the feed at the default page size."""
    return _pages_for_window(day, day)


def _pages_for_window(day_from: dt.date, day_to: dt.date) -> list[dict]:
    """Walk a window of target days at the default page size."""
    pages: list[dict] = []
    cursor = None
    while True:
        body = feed_page(
            BULLETINS_FEED,
            since=None,
            cursor=cursor,
            limit=DEFAULT_LIMIT,
            day_from=day_from,
            day_to=day_to,
        )
        pages.append(body)
        cursor = body["next_cursor"]
        if cursor is None:
            return pages


@pytest.mark.django_db
def test_a_de_aggregated_day_is_three_default_pages() -> None:
    """298 bulletins on one target day fit in three default pages."""
    day = dt.date(2026, 12, 20)
    rows = _PER_REGION_ISSUE * _ISSUES_PER_DAY
    SourceBulletin.objects.bulk_create(
        SourceBulletinFactory.build(target_date=day) for _ in range(rows)
    )

    pages = _pages_for_day(day)

    assert pages[0]["count"] == rows
    assert sum(len(p["results"]) for p in pages) == rows
    assert len(pages) <= 3


@pytest.mark.django_db
def test_the_golden_week_of_per_region_bulletins_is_at_most_21_pages() -> None:
    """Two real de-aggregated days page as three each; seven are 21 pages.

    Two target days of 298 rows are walked through the feed's day window,
    each on its own and then together, so the page count is measured
    rather than assumed — and the week is the same per-item rate over
    seven days, 2,086 items. Two days rather than seven keeps the rows to
    596.
    """
    days = (dt.date(2026, 12, 20), dt.date(2026, 12, 21))
    per_day = _PER_REGION_ISSUE * _ISSUES_PER_DAY
    SourceBulletin.objects.bulk_create(
        SourceBulletinFactory.build(target_date=day)
        for day in days
        for _ in range(per_day)
    )

    for day in days:
        pages = _pages_for_day(day)
        assert pages[0]["count"] == per_day
        assert len(pages) == math.ceil(per_day / DEFAULT_LIMIT) == 3

    both = _pages_for_window(days[0], days[1])
    assert sum(len(p["results"]) for p in both) == 2 * per_day
    assert len(both) == math.ceil(2 * per_day / DEFAULT_LIMIT) == 6

    week_items = 7 * per_day
    assert week_items == 2086
    assert math.ceil(week_items / DEFAULT_LIMIT) <= 21


@pytest.mark.django_db
def test_a_default_page_of_per_region_bulletins_is_under_5_mb() -> None:
    """A full page of the slice's largest bulletin serialises under 5 MB.

    Each item carries the CAAML (weather block and all) and the render
    model built from it, which is the whole of what the wire sends.
    """
    slice_bulletins = [json.loads(line) for line in _SLICE.read_text().splitlines()]
    largest = max(slice_bulletins, key=lambda b: len(json.dumps(b)))
    render_model = build_render_model(largest)
    SourceBulletin.objects.bulk_create(
        SourceBulletinFactory.build(
            caaml={**largest, "bulletinID": f"per-region-{n:03d}"},
            render_model=render_model,
        )
        for n in range(DEFAULT_LIMIT)
    )

    body = feed_page(BULLETINS_FEED, since=None, cursor=None, limit=DEFAULT_LIMIT)

    assert len(body["results"]) == DEFAULT_LIMIT
    assert len(json.dumps(body).encode("utf-8")) < _PAGE_CEILING_BYTES
