"""
apps/ingest/store.py — Keeping what a provider published, once per change.

``store_bulletin(record, *, force=False)`` writes one ``NormalisedBulletin``
from a provider walk (``apps.ingest.runs``) into ``SourceBulletin`` and says
what it did (SNOW-1250):

* ``created`` — a bulletin id ingest has not seen before;
* ``updated`` — a known id whose CAAML hash has changed;
* ``unchanged`` — a known id with the same hash: nothing is written, so
  ``updated_at`` stays put and the feed does not send it again.

The PDF link is resolved only when a row is written, because for
Météo-France it costs a request to the archive index (``NormalisedBulletin``
resolves it lazily for exactly this reason). ``force`` rewrites the link of
an unchanged row — the one thing a re-run can learn that the hash cannot
see — and leaves the row alone when the link has not moved either.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.ingest.models import SourceBulletin, content_hash
from apps.ingest.normalise.fetcher_common import OUTCOME_CREATED, OUTCOME_UPDATED
from apps.ingest.runs import NormalisedBulletin

logger = logging.getLogger(__name__)

OUTCOME_UNCHANGED = "unchanged"


def store_bulletin(
    record: NormalisedBulletin, *, force: bool = False
) -> tuple[SourceBulletin, str]:
    """Write one fetched bulletin, if it is new or its content has changed.

    Args:
        record: A bulletin from ``fetch_bulletins``.
        force: Re-resolve the PDF link of an unchanged bulletin, and write
            it when it differs.

    Returns:
        ``(row, outcome)``, the outcome one of ``OUTCOME_CREATED``,
        ``OUTCOME_UPDATED`` or ``OUTCOME_UNCHANGED``.

    """
    digest = content_hash(record.caaml)
    row = SourceBulletin.objects.filter(bulletin_id=record.bulletin_id).first()

    if row is None:
        row = SourceBulletin.objects.create(
            bulletin_id=record.bulletin_id,
            provider=record.provider,
            caaml=record.caaml,
            pdf_url=record.pdf_url(),
            content_hash=digest,
            fetched_at=timezone.now(),
        )
        logger.debug("Stored new source bulletin %s", row)
        return row, OUTCOME_CREATED

    if row.content_hash == digest:
        if force:
            pdf_url = record.pdf_url()
            if pdf_url != row.pdf_url:
                row.pdf_url = pdf_url
                row.save(update_fields=["pdf_url", "updated_at"])
                return row, OUTCOME_UPDATED
        return row, OUTCOME_UNCHANGED

    row.provider = record.provider
    row.caaml = record.caaml
    row.pdf_url = record.pdf_url()
    row.content_hash = digest
    row.fetched_at = timezone.now()
    row.save()
    logger.debug("Updated source bulletin %s", row)
    return row, OUTCOME_UPDATED
