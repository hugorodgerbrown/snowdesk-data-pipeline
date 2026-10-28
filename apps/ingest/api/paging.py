"""
apps/ingest/api/paging.py — The data feed's paging rule, shared by every feed.

Moved from ``apps/data_feed/feeds.py`` by SNOW-1250 so that ingest can serve
its own ``SourceBulletin`` rows without the host app; the host's feeds
(weather, locations, resorts) page through the same functions, imported from
``apps.ingest.public``.

**Paging is a keyset on (updated_at, id), ascending.** The cursor is the
last row's pair, so a row written while a client pages moves to the end of
the order and is still reached, and no page is a re-run OFFSET of the one
before. ``?since=`` filters on ``updated_at`` for an incremental pull.
``count`` — how many rows the whole pull returns — is sent on the first page
only.

**A feed with a day may be windowed by it** (SNOW-1272). ``?from=`` and
``?to=`` (ISO dates, inclusive) filter on the feed's ``day_field`` — a
bulletin's target day, a weather row's day, a day rating's date — so a
client can seed the last fortnight or one fixed week rather than the whole
archive. The paging is unchanged: the window narrows the rows, the keyset
walks them.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db.models import Q, QuerySet
from django.utils.dateparse import parse_date, parse_datetime

# Bumped on a breaking change to any feed's shape; it is also the URL's /v1/.
VERSION = 1

# Page size: the default, and the most a client may ask for. A bulletin's
# CAAML runs to tens of kilobytes, so the ceiling keeps a page to a few MB.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


class FeedQueryError(ValueError):
    """A ``since``, ``cursor`` or ``limit`` parameter the feed cannot use.

    Attributes:
        param: Which parameter — the view answers with that parameter's
            fixed message (``PARAMETER_ERRORS``), never with this
            exception's own text, so nothing raised reaches a response.

    """

    def __init__(self, param: str, message: str) -> None:
        """Record the parameter alongside the message for the log."""
        super().__init__(message)
        self.param = param


# What a client is told about each bad parameter. Fixed strings, so the
# response never echoes an exception (or the client's input) back.
PARAMETER_ERRORS: dict[str, str] = {
    "since": "since must be an ISO 8601 datetime with a UTC offset",
    "cursor": "cursor is not one this feed issued",
    "limit": "limit must be a positive integer",
    "from": "from must be an ISO 8601 date",
    "to": "to must be an ISO 8601 date",
    "window": "this feed has no day to window by",
}


@dataclass(frozen=True)
class Feed:
    """One endpoint: the rows it may read and how each becomes an item.

    Attributes:
        name: The URL segment, e.g. ``bulletins``.
        queryset: Returns the base queryset, already restricted to what may
            be served and slimmed to what ``serialise`` reads.
        serialise: Turns one row into one wire item.
        version: The version the feed is served under — the ``vN`` in its
            URL and the ``version`` in every page. ``VERSION`` unless a feed
            changed shape (SNOW-1253's v2 weather).
        day_field: The date column ``?from=``/``?to=`` filter on; None
            for a feed with no day, which refuses a window.

    """

    name: str
    queryset: Callable[[], QuerySet[Any]]
    serialise: Callable[[Any], dict[str, Any]]
    version: int = VERSION
    day_field: str | None = None


def encode_cursor(updated_at: dt.datetime, pk: int) -> str:
    """Return the opaque cursor that resumes after the row ``(updated_at, pk)``.

    Args:
        updated_at: The last row's ``updated_at``.
        pk: The last row's primary key.

    Returns:
        A URL-safe token.

    """
    raw = f"{updated_at.isoformat()}|{pk}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[dt.datetime, int]:
    """Return the ``(updated_at, pk)`` pair a cursor resumes after.

    Args:
        cursor: A token from ``encode_cursor``.

    Returns:
        The pair.

    Raises:
        FeedQueryError: The token is not one this server issued.

    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, _, pk = base64.urlsafe_b64decode(padded).decode().partition("|")
        updated_at = parse_datetime(stamp)
        if updated_at is None:
            raise ValueError(stamp)
        return updated_at, int(pk)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise FeedQueryError("cursor", f"cursor {cursor!r} is not valid") from exc


def parse_since(raw: str | None) -> dt.datetime | None:
    """Return ``?since=`` as an aware datetime, or None when absent.

    Args:
        raw: The parameter, an ISO 8601 datetime with an offset.

    Returns:
        The datetime, or None.

    Raises:
        FeedQueryError: It does not parse or carries no offset.

    """
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None or parsed.tzinfo is None:
        raise FeedQueryError(
            "since", f"since {raw!r} must be an ISO 8601 datetime with a UTC offset"
        )
    return parsed


def parse_day(raw: str | None, param: str) -> dt.date | None:
    """Return ``?from=`` or ``?to=`` as a date, or None when absent.

    Args:
        raw: The parameter, an ISO 8601 date.
        param: Its name, for the error.

    Returns:
        The date, or None.

    Raises:
        FeedQueryError: It does not parse.

    """
    if not raw:
        return None
    try:
        parsed = parse_date(raw)
    except ValueError:
        parsed = None
    if parsed is None:
        raise FeedQueryError(param, f"{param} {raw!r} must be an ISO 8601 date")
    return parsed


def parse_limit(raw: str | None) -> int:
    """Return ``?limit=``, defaulted and held to ``MAX_LIMIT``.

    Args:
        raw: The parameter.

    Returns:
        The page size.

    Raises:
        FeedQueryError: It is not a positive integer.

    """
    if not raw:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError as exc:
        raise FeedQueryError("limit", f"limit {raw!r} is not an integer") from exc
    if limit < 1:
        raise FeedQueryError("limit", "limit must be at least 1")
    return min(limit, MAX_LIMIT)


def page(
    feed: Feed,
    *,
    since: dt.datetime | None,
    cursor: str | None,
    limit: int,
    day_from: dt.date | None = None,
    day_to: dt.date | None = None,
) -> dict[str, Any]:
    """Return one page of ``feed``: its items, and the cursor to the next.

    ``count`` — how many rows the whole pull will return — is sent on the
    first page only, where it is one extra query rather than one per page.

    Args:
        feed: The feed to read.
        since: Only rows updated at or after this, when given.
        cursor: Resume after this cursor, when given.
        limit: The page size.
        day_from: Only rows whose ``day_field`` is on or after this.
        day_to: Only rows whose ``day_field`` is on or before this.

    Returns:
        The response body: ``feed``, ``version``, ``results``,
        ``next_cursor`` (None on the last page) and, on the first page,
        ``count``.

    Raises:
        FeedQueryError: ``cursor`` does not decode, or a window was asked
            of a feed with no day.

    """
    queryset = feed.queryset()
    if since is not None:
        queryset = queryset.filter(updated_at__gte=since)
    if day_from is not None or day_to is not None:
        if feed.day_field is None:
            raise FeedQueryError("window", f"{feed.name} has no day to window by")
        if day_from is not None:
            queryset = queryset.filter(**{f"{feed.day_field}__gte": day_from})
        if day_to is not None:
            queryset = queryset.filter(**{f"{feed.day_field}__lte": day_to})
    body: dict[str, Any] = {"feed": feed.name, "version": feed.version}
    if cursor is None:
        body["count"] = queryset.count()
    else:
        after_at, after_pk = decode_cursor(cursor)
        queryset = queryset.filter(
            Q(updated_at__gt=after_at) | Q(updated_at=after_at, pk__gt=after_pk)
        )
    rows = list(queryset.order_by("updated_at", "pk")[: limit + 1])
    more = len(rows) > limit
    rows = rows[:limit]
    body["results"] = [feed.serialise(row) for row in rows]
    body["next_cursor"] = (
        encode_cursor(rows[-1].updated_at, rows[-1].pk) if more else None
    )
    return body
