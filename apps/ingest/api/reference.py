"""
apps/ingest/api/reference.py — The v2 places, passes and resorts feeds (SNOW-1271).

Every item is keyed by the service's ``uuid``, which a client holds as the
``source_uuid`` of its own row, and carries ``deleted_at``: a tombstone is
served like any other change, so a client paging "since" learns of a
removal. All three serve tombstoned rows too.

* ``locations`` — ``SourceLocation``: the four columns the weather fetch
  reads under ``fields``, and the host's ``document`` (kind, what3words,
  the regions it is the centre of) as stored.
* ``passes`` and ``resorts`` — ``SourcePass`` and ``SourceResort``: the
  ``slug`` and the ``document`` as stored.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.models import SourceDocument, SourceLocation, SourcePass, SourceResort

# The version these feeds are served under — the /v2/ in their URLs.
REFERENCE_FEED_VERSION = 2


def _iso(value: Any) -> str | None:
    """Return a datetime as ISO 8601, or None.

    Args:
        value: A datetime or None.

    Returns:
        The ISO string, or None.

    """
    return value.isoformat() if value is not None else None


def serialise_source_location(row: SourceLocation) -> dict[str, Any]:
    """Return one reference place.

    Args:
        row: A ``SourceLocation``.

    Returns:
        The wire item.

    """
    return {
        "uuid": str(row.uuid),
        "updated_at": row.updated_at.isoformat(),
        "deleted_at": _iso(row.deleted_at),
        "fields": {
            "name": row.name,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "elevation_m": row.elevation_m,
        },
        "document": row.document,
    }


def serialise_source_document(row: SourceDocument) -> dict[str, Any]:
    """Return one pass or resort.

    Args:
        row: A ``SourcePass`` or ``SourceResort``.

    Returns:
        The wire item.

    """
    return {
        "uuid": str(row.uuid),
        "slug": row.slug,
        "updated_at": row.updated_at.isoformat(),
        "deleted_at": _iso(row.deleted_at),
        "document": row.document,
    }


def _locations() -> QuerySet[SourceLocation]:
    """Return every reference place, tombstones included."""
    return SourceLocation.objects.all()


def _passes() -> QuerySet[SourcePass]:
    """Return every pass, tombstones included."""
    return SourcePass.objects.all()


def _resorts() -> QuerySet[SourceResort]:
    """Return every resort, tombstones included."""
    return SourceResort.objects.all()


LOCATIONS_FEED = Feed(
    "locations",
    _locations,
    serialise_source_location,
    version=REFERENCE_FEED_VERSION,
)
PASSES_FEED = Feed(
    "passes",
    _passes,
    serialise_source_document,
    version=REFERENCE_FEED_VERSION,
)
RESORTS_FEED = Feed(
    "resorts",
    _resorts,
    serialise_source_document,
    version=REFERENCE_FEED_VERSION,
)
