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

A host that could not read one of its records hands an ``UnreadableRecord``
in its place (SNOW-1489). It is counted ``failed`` and writes nothing. When
it still carries its ``uuid``, that row is kept as it was, neither rewritten
nor tombstoned. When it does not, the sync cannot tell which stored row it
was, so no row is tombstoned on that run: a lost identity must not delete a
row a client still holds.

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


@dataclass(frozen=True)
class UnreadableRecord:
    """A record the host holds but could not read, handed in its place.

    Attributes:
        uuid: The service's id for the record, when the host could still
            read it; None when the record's identity is lost too.
        label: A readable label for the log, such as ``tour 5956``.

    """

    uuid: uuid_module.UUID | None
    label: str


def _tombstone_absent(
    existing: dict[uuid_module.UUID, SourceDocument],
    seen: set[uuid_module.UUID | None],
) -> int:
    """Tombstone every live row whose uuid the host did not hand over.

    Args:
        existing: The stored rows, by uuid.
        seen: Every uuid the host handed over, readable or not.

    Returns:
        How many rows were tombstoned.

    """
    gone = [
        row
        for key, row in existing.items()
        if key not in seen and row.deleted_at is None
    ]
    now = timezone.now()
    for row in gone:
        row.deleted_at = now
        row.save(update_fields=["deleted_at", "updated_at"])
    return len(gone)


def sync_source_documents(
    model: type[SourceDocument],
    records: Iterable[DocumentRecord | UnreadableRecord],
) -> dict[str, int]:
    """Make the live rows of ``model`` equal ``records``.

    Args:
        model: ``SourcePass``, ``SourceResort``, ``SourceSkiSegment`` or
            ``SourceSkiTour``.
        records: Every record of that kind, as the host holds them now. An
            ``UnreadableRecord`` keeps its stored row as it was; one with no
            uuid holds back every tombstone on this run.

    Returns:
        Counts: ``created``, ``updated``, ``deleted``, ``unchanged``,
        ``failed``.

    """
    counts = {
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "unchanged": 0,
        "failed": 0,
    }
    # An unreadable record with no uuid adds None: the sync then cannot
    # tell which stored row it was, so it tombstones none.
    seen: set[uuid_module.UUID | None] = set()
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
            if isinstance(record, UnreadableRecord):
                counts["failed"] += 1
                continue
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

        if None in seen:
            logger.warning(
                "sync_source_documents: tombstones held back for %s, since "
                "an unreadable record had no uuid",
                model.__name__,
            )
        else:
            counts["deleted"] = _tombstone_absent(existing, seen)

    logger.info("sync_source_documents(%s): %s", model.__name__, counts)
    return counts
