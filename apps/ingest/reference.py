"""
apps/ingest/reference.py — The service's passes and resorts (SNOW-1271).

A host hands the service its whole list of one kind of record, and
``sync_source_documents`` makes the stored list equal it, the way
``sync_source_locations`` does for the weather stations:

* a record whose ``uuid`` is new is created;
* a known one is rewritten only when its document's hash differs, so
  ``updated_at`` moves only on a real change and the feed stays quiet;
* a live row absent from the list is tombstoned (``deleted_at``), and a
  tombstoned one that is back is revived. Nothing is ever deleted, so a
  client learns of a removal from the feed.

The service never reads inside a document. What a resort is — its fields,
its region, its places — belongs to whoever curates it; the service keeps
and serves it, as it keeps a bulletin's CAAML.
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.ingest.models import SourceDocument, content_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DocumentRecord:
    """One curated record as a host hands it to ``sync_source_documents``.

    Attributes:
        uuid: The service's id for the record.
        slug: A readable label.
        document: The record, in the shape a client applies.

    """

    uuid: uuid_module.UUID
    slug: str
    document: dict[str, Any]


def sync_source_documents(
    model: type[SourceDocument], records: Iterable[DocumentRecord]
) -> dict[str, int]:
    """Make the live rows of ``model`` equal ``records``.

    Args:
        model: ``SourcePass``, ``SourceResort``, ``SourceSkiSegment`` or
            ``SourceSkiTour``.
        records: Every record of that kind, as the host holds them now.

    Returns:
        Counts: ``created``, ``updated``, ``deleted``, ``unchanged``.

    """
    counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    seen: set[uuid_module.UUID] = set()
    # Slim: a document is not read to decide, only its hash; the ski
    # network runs to tens of thousands of lines (SNOW-1276). A deferred
    # row saves only its loaded fields, so ``updated_at`` is loaded for
    # auto_now to move it, and a rewrite assigns ``document``, which loads
    # it for the save.
    existing = {
        row.uuid: row
        for row in model.objects.only(
            "id", "uuid", "slug", "content_hash", "deleted_at", "updated_at"
        )
    }

    with transaction.atomic():
        for record in records:
            seen.add(record.uuid)
            digest = content_hash(record.document)
            row = existing.get(record.uuid)
            if row is None:
                model.objects.create(
                    uuid=record.uuid,
                    slug=record.slug,
                    document=record.document,
                    content_hash=digest,
                )
                counts["created"] += 1
                continue
            if (
                row.content_hash == digest
                and row.slug == record.slug
                and row.deleted_at is None
            ):
                counts["unchanged"] += 1
                continue
            row.slug = record.slug
            if row.content_hash != digest:
                for field, value in model.changed_document_resets().items():
                    setattr(row, field, value)
            row.document = record.document
            row.content_hash = digest
            row.deleted_at = None
            row.save()
            counts["updated"] += 1

        gone = [
            row
            for key, row in existing.items()
            if key not in seen and row.deleted_at is None
        ]
        now = timezone.now()
        for row in gone:
            row.deleted_at = now
            row.save(update_fields=["deleted_at", "updated_at"])
        counts["deleted"] = len(gone)

    logger.info("sync_source_documents(%s): %s", model.__name__, counts)
    return counts
