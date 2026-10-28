"""
apps/ingest/api/bulletins.py — The ``bulletins`` data feed, read from SourceBulletin.

``serialise_source_bulletin`` turns one row into one wire item: the
provider's CAAML exactly as normalised, plus its PDF link. Nothing derived
(region links, day ratings, groupings, the render model) is sent; a client
rebuilds those with its own ``upsert_bulletin``, so they are always its own
code's output. ``BULLETINS_FEED`` is the feed ``/api/data/v1/bulletins/``
pages through.

Moved from ``apps/data_feed/feeds.py`` by SNOW-1250, which also changed what
it reads: ingest's own ``SourceBulletin`` rather than the app's ``Bulletin``.
The wire item is the same shape, so a client cannot tell. The fetch pipelines
apply what they fetch through this same serialiser, so production writes its
``Bulletin`` rows from exactly the items staging receives.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.models import SourceBulletin


def serialise_source_bulletin(row: SourceBulletin) -> dict[str, Any]:
    """Return a bulletin as the provider's CAAML plus its PDF link.

    Args:
        row: A row loaded with ``bulletin_id``, ``pdf_url``, ``caaml`` and
            ``updated_at``.

    Returns:
        The wire item.

    """
    return {
        "bulletin_id": row.bulletin_id,
        "updated_at": row.updated_at.isoformat(),
        "pdf_url": row.pdf_url,
        "caaml": row.caaml,
    }


def _source_bulletins() -> QuerySet[SourceBulletin]:
    """Return every stored bulletin, loading only what the item carries."""
    return SourceBulletin.objects.only(
        "id", "bulletin_id", "updated_at", "pdf_url", "caaml"
    )


BULLETINS_FEED = Feed("bulletins", _source_bulletins, serialise_source_bulletin)
