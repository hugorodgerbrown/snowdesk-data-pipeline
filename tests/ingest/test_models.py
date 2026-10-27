"""
tests/ingest/test_models.py — Tests for SourceBulletin and content_hash (SNOW-1250).

The model kit CLAUDE.md asks of every model — declared rather than inherited
here, because ingest may not import ``apps.core`` — plus the canonical hash.
"""

from __future__ import annotations

import pytest

from apps.ingest.models import SourceBulletin, content_hash
from tests.factories import SourceBulletinFactory


def test_the_hash_ignores_key_order() -> None:
    """Two dicts that compare equal hash equal."""
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})


def test_the_hash_sees_any_change() -> None:
    """A changed value changes the hash; non-ASCII text hashes stably."""
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    assert content_hash({"t": "Météo"}) == content_hash({"t": "Météo"})
    assert len(content_hash({})) == 64


@pytest.mark.django_db
class TestSourceBulletin:
    """The model kit."""

    def test_to_string_names_the_provider_and_id(self) -> None:
        """``__str__`` delegates to ``to_string``."""
        row = SourceBulletinFactory.create(bulletin_id="abc", provider="ALBINA")
        assert row.to_string() == "ALBINA abc"
        assert str(row) == "ALBINA abc"

    def test_declares_what_base_model_would_give(self) -> None:
        """An id, a uuid and both timestamps, set on create."""
        row = SourceBulletinFactory.create()
        assert row.pk is not None
        assert row.uuid is not None
        assert row.created_at is not None
        assert row.updated_at is not None

    def test_newest_first(self) -> None:
        """Ordered by -created_at, as every model is by default."""
        older = SourceBulletinFactory.create()
        newer = SourceBulletinFactory.create()
        assert list(SourceBulletin.objects.all()) == [newer, older]

    def test_queryset_filters_by_provider(self) -> None:
        """``for_provider`` keeps one provider's rows."""
        slf = SourceBulletinFactory.create(provider="SLF")
        SourceBulletinFactory.create(provider="METEOFRANCE")
        assert list(SourceBulletin.objects.for_provider("SLF")) == [slf]

    def test_the_factory_hashes_its_caaml(self) -> None:
        """A factory row compares as a stored one would."""
        row = SourceBulletinFactory.create()
        assert row.content_hash == content_hash(row.caaml)
