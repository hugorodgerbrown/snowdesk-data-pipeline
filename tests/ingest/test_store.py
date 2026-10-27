"""
tests/ingest/test_store.py — Tests for store_bulletin (SNOW-1250).

A new id is created, a changed hash updates, the same hash writes nothing —
not even ``updated_at`` — and the PDF link is resolved only when a row is
written, or when ``force`` asks for it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.ingest.models import SourceBulletin, content_hash
from apps.ingest.runs import NormalisedBulletin
from apps.ingest.store import (
    OUTCOME_CREATED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    store_bulletin,
)

pytestmark = pytest.mark.django_db


def _record(
    caaml: dict, pdf: str = "https://x/a.pdf"
) -> tuple[NormalisedBulletin, MagicMock]:
    """A walk's record, and its PDF lookup as a mock so calls can be counted."""
    lookup = MagicMock(return_value=pdf)
    return NormalisedBulletin("a", "SLF", caaml, lookup), lookup


def _rec(caaml: dict, pdf: str = "https://x/a.pdf") -> NormalisedBulletin:
    """Just the record, for a call whose lookup count does not matter."""
    return _record(caaml, pdf)[0]


def test_a_new_bulletin_is_created() -> None:
    """Every column is written, the hash from the CAAML."""
    row, outcome = store_bulletin(_rec({"bulletinID": "a"}))
    assert outcome == OUTCOME_CREATED
    stored = SourceBulletin.objects.get()
    assert stored == row
    assert (stored.provider, stored.caaml, stored.pdf_url) == (
        "SLF",
        {"bulletinID": "a"},
        "https://x/a.pdf",
    )
    assert stored.content_hash == content_hash({"bulletinID": "a"})


def test_the_same_content_writes_nothing() -> None:
    """Unchanged: no write, no PDF lookup, updated_at unmoved."""
    first, _ = store_bulletin(_rec({"bulletinID": "a"}))
    again, lookup = _record({"bulletinID": "a"})
    row, outcome = store_bulletin(again)
    assert outcome == OUTCOME_UNCHANGED
    lookup.assert_not_called()
    assert SourceBulletin.objects.get().updated_at == first.updated_at
    assert row.pk == first.pk


def test_changed_content_updates_the_row() -> None:
    """A new hash rewrites the CAAML, the link and fetched_at."""
    first, _ = store_bulletin(_rec({"bulletinID": "a"}))
    row, outcome = store_bulletin(_rec({"bulletinID": "a", "v": 2}, "https://x/b"))
    assert outcome == OUTCOME_UPDATED
    stored = SourceBulletin.objects.get()
    assert stored.caaml == {"bulletinID": "a", "v": 2}
    assert stored.pdf_url == "https://x/b"
    assert stored.fetched_at > first.fetched_at
    assert stored.updated_at > first.updated_at


def test_force_rewrites_a_moved_pdf_link_only() -> None:
    """force re-resolves the link; a moved one is written, a same one is not."""
    store_bulletin(_rec({"bulletinID": "a"}))
    same, lookup = _record({"bulletinID": "a"})
    assert store_bulletin(same, force=True)[1] == OUTCOME_UNCHANGED
    lookup.assert_called_once()
    _, outcome = store_bulletin(_rec({"bulletinID": "a"}, "https://x/new"), force=True)
    assert outcome == OUTCOME_UPDATED
    assert SourceBulletin.objects.get().pdf_url == "https://x/new"
