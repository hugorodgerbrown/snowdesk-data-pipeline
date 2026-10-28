"""
apps/ingest/api/bulletins.py — The ``bulletins`` data feed, read from SourceBulletin.

``serialise_source_bulletin`` turns one row into one wire item: the
provider's CAAML exactly as normalised, its PDF link, and since SNOW-1270
the service's render model of it. A client copies the render model when it
was built at the client's own ``RENDER_MODEL_VERSION`` and builds its own
otherwise, so a client running a newer builder is never held to the
service's. The other derived tables (region links, day ratings, groupings)
are still rebuilt by the client's ``upsert_bulletin``. ``BULLETINS_FEED``
is the feed ``/api/data/v1/bulletins/`` pages through.

Moved from ``apps/data_feed/feeds.py`` by SNOW-1250, which also changed what
it reads: ingest's own ``SourceBulletin`` rather than the app's ``Bulletin``.
The wire item is the same shape, so a client cannot tell. The fetch pipelines
apply what they fetch through this same serialiser, so production writes its
``Bulletin`` rows from exactly the items staging receives.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.db.models import QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.models import SourceBulletin


def serialise_source_bulletin(row: SourceBulletin) -> dict[str, Any]:
    """Return a bulletin as the provider's CAAML, its PDF link and render model.

    Args:
        row: A row loaded with ``bulletin_id``, ``pdf_url``, ``caaml``,
            ``render_model`` and ``updated_at``.

    Returns:
        The wire item.

    """
    return {
        "bulletin_id": row.bulletin_id,
        "updated_at": row.updated_at.isoformat(),
        "pdf_url": row.pdf_url,
        "caaml": row.caaml,
        "render_model": row.render_model,
    }


def _source_bulletins() -> QuerySet[SourceBulletin]:
    """Return every stored bulletin, loading only what the item carries."""
    return SourceBulletin.objects.only(
        "id", "bulletin_id", "updated_at", "pdf_url", "caaml", "render_model"
    )


BULLETINS_FEED = Feed(
    "bulletins", _source_bulletins, serialise_source_bulletin, day_field="target_date"
)

# The same feed under /api/data/v2/ (SNOW-1272), so one token bound to v2
# reads everything the reference-data service serves. Same items.
BULLETINS_FEED_V2 = replace(BULLETINS_FEED, version=2)
