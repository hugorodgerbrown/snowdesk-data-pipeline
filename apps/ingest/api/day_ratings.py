"""
apps/ingest/api/day_ratings.py — The v2 ``day-ratings`` feed (SNOW-1270).

One item per ``SourceDayRating``: a region's danger range for one day as
the service computed it from its own bulletins. Keyed by the region's EAWS
code and the day, and naming the governing bulletin by its id, so a client
resolves both against its own tables. ``version`` is the
``DAY_RATING_VERSION`` the row was computed under: a client copies an item
computed under its own version and recomputes the pair itself otherwise.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.api.reference import REFERENCE_FEED_VERSION
from apps.ingest.day_ratings import DAY_RATING_FIELDS
from apps.ingest.models import SourceDayRating


def serialise_source_day_rating(row: SourceDayRating) -> dict[str, Any]:
    """Return one day rating.

    Args:
        row: A ``SourceDayRating``.

    Returns:
        The wire item: the key, ``updated_at`` and every rated column.

    """
    return {
        "region_code": row.region_code,
        "date": row.date.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        **{name: getattr(row, name) for name in DAY_RATING_FIELDS},
    }


def _source_day_ratings() -> QuerySet[SourceDayRating]:
    """Return every day rating the service holds."""
    return SourceDayRating.objects.all()


DAY_RATINGS_FEED = Feed(
    "day-ratings",
    _source_day_ratings,
    serialise_source_day_rating,
    version=REFERENCE_FEED_VERSION,
    day_field="date",
)
