"""
tests/ingest/test_reference.py — Tests for apps.ingest.reference (SNOW-1271).

``sync_source_documents`` makes the stored passes or resorts equal the list
a host hands over: created, rewritten only on a changed hash, tombstoned
when absent and revived when back. Run once per table, since both tables
share the one function. The ski tables (SNOW-1276) share it too. An
unreadable record (SNOW-1489) is counted failed and keeps its row; one
with no uuid holds back every tombstone.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.ingest.models import (
    SourcePass,
    SourceResort,
    SourceSkiSegment,
    SourceSkiTour,
    content_hash,
)
from apps.ingest.reference import (
    DocumentRecord,
    UnreadableRecord,
    sync_source_documents,
)

pytestmark = pytest.mark.django_db

MODELS = pytest.mark.parametrize(
    "model", [SourcePass, SourceResort, SourceSkiSegment, SourceSkiTour]
)


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
    assert counts == {
        "created": 1,
        "updated": 0,
        "deleted": 0,
        "unchanged": 0,
        "failed": 0,
    }


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
def test_a_rewrite_moves_updated_at_and_saves_the_document(model: Any) -> None:
    """The rows are read slim (SNOW-1276); a rewrite must still save both."""
    record = _record()
    sync_source_documents(model, [record])
    before = model.objects.get().updated_at
    sync_source_documents(model, [_record(uuid=record.uuid, document={"name": "M"})])
    row = model.objects.get()
    assert row.updated_at > before
    assert row.document == {"name": "M"}
    assert row.content_hash == content_hash({"name": "M"})


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


@MODELS
def test_an_unreadable_record_with_a_uuid_keeps_its_row(model: Any) -> None:
    """Counted failed; its stored row is neither rewritten nor tombstoned."""
    kept, dropped = _record(), _record(slug="gone")
    sync_source_documents(model, [kept, dropped])
    before = model.objects.get(uuid=kept.uuid).updated_at
    counts = sync_source_documents(
        model, [UnreadableRecord(uuid=kept.uuid, label="kept")]
    )
    row = model.objects.get(uuid=kept.uuid)
    assert row.updated_at == before
    assert row.deleted_at is None
    assert row.document == kept.document
    assert model.objects.get(uuid=dropped.uuid).deleted_at is not None
    assert counts == {
        "created": 0,
        "updated": 0,
        "deleted": 1,
        "unchanged": 0,
        "failed": 1,
    }


@MODELS
def test_an_unreadable_new_record_creates_nothing(model: Any) -> None:
    """A uuid the service has never stored is not created from nothing."""
    counts = sync_source_documents(
        model, [UnreadableRecord(uuid=uuid.uuid4(), label="new")]
    )
    assert not model.objects.exists()
    assert counts["failed"] == 1
    assert counts["created"] == 0


@MODELS
def test_an_unreadable_record_without_a_uuid_holds_back_tombstones(
    model: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """No row is tombstoned when the sync cannot tell which one was unread."""
    absent, present = _record(), _record(slug="present")
    sync_source_documents(model, [absent, present])
    changed = _record(uuid=present.uuid, slug="present", document={"name": "P"})
    with caplog.at_level("WARNING", logger="apps.ingest.reference"):
        counts = sync_source_documents(
            model, [changed, UnreadableRecord(uuid=None, label="lost")]
        )
    assert model.objects.get(uuid=absent.uuid).deleted_at is None
    assert model.objects.get(uuid=present.uuid).document == {"name": "P"}
    assert counts == {
        "created": 0,
        "updated": 1,
        "deleted": 0,
        "unchanged": 0,
        "failed": 1,
    }
    assert f"tombstones held back for {model.__name__}" in caplog.text
