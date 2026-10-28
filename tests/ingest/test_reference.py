"""
tests/ingest/test_reference.py — Tests for apps.ingest.reference (SNOW-1271).

``sync_source_documents`` makes the stored passes or resorts equal the list
a host hands over: created, rewritten only on a changed hash, tombstoned
when absent and revived when back. Run once per table, since both tables
share the one function.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.ingest.models import SourcePass, SourceResort, content_hash
from apps.ingest.reference import DocumentRecord, sync_source_documents

pytestmark = pytest.mark.django_db

MODELS = pytest.mark.parametrize("model", [SourcePass, SourceResort])


def _record(**overrides: Any) -> DocumentRecord:
    """Return a record, with any field overridden."""
    values: dict[str, Any] = {
        "uuid": uuid.uuid4(),
        "slug": "magic-pass",
        "document": {"name": "Magic Pass", "website": ""},
    }
    values.update(overrides)
    return DocumentRecord(**values)


@MODELS
def test_a_new_record_is_created_under_the_hosts_uuid(model: Any) -> None:
    """The uuid is the host's, and the hash is the document's."""
    record = _record()
    counts = sync_source_documents(model, [record])
    row = model.objects.get()
    assert row.uuid == record.uuid
    assert row.document == record.document
    assert row.content_hash == content_hash(record.document)
    assert counts == {"created": 1, "updated": 0, "deleted": 0, "unchanged": 0}


@MODELS
def test_an_unchanged_record_is_not_written(model: Any) -> None:
    """``updated_at`` stays put, so a client paging "since" sees nothing."""
    record = _record()
    sync_source_documents(model, [record])
    before = model.objects.get().updated_at
    counts = sync_source_documents(model, [record])
    assert model.objects.get().updated_at == before
    assert counts["unchanged"] == 1


@MODELS
def test_a_changed_document_or_slug_is_rewritten(model: Any) -> None:
    """Either change is an update of the same row."""
    record = _record()
    sync_source_documents(model, [record])
    counts = sync_source_documents(
        model, [_record(uuid=record.uuid, document={"name": "Magic"})]
    )
    assert model.objects.get().document == {"name": "Magic"}
    counts = sync_source_documents(
        model, [_record(uuid=record.uuid, slug="magic", document={"name": "Magic"})]
    )
    assert model.objects.get().slug == "magic"
    assert counts["updated"] == 1


@MODELS
def test_an_absent_record_is_tombstoned_once_and_revived_when_back(
    model: Any,
) -> None:
    """Nothing is deleted; a second sync leaves the tombstone's time alone."""
    record = _record()
    sync_source_documents(model, [record])
    assert sync_source_documents(model, [])["deleted"] == 1
    first = model.objects.get().deleted_at
    assert first is not None
    assert sync_source_documents(model, [])["deleted"] == 0
    assert model.objects.get().deleted_at == first

    assert sync_source_documents(model, [record])["updated"] == 1
    assert model.objects.get().deleted_at is None
