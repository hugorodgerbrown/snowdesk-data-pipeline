"""
tests/bulletins/test_bulletin_share_model.py — Tests for BulletinShare model.

Covers factory creation, to_string(), Meta.ordering, and SET_NULL behaviour
when the linked Bulletin is deleted.
"""

from __future__ import annotations

import datetime

import pytest

from apps.bulletins.models import Bulletin, BulletinShare
from tests.factories import (
    BulletinFactory,
    BulletinShareFactory,
    MicroRegionFactory,
)


@pytest.mark.django_db
class TestBulletinShareFactory:
    """BulletinShareFactory produces valid instances."""

    def test_create_returns_instance(self) -> None:
        """Factory.create() returns a persisted BulletinShare."""
        share = BulletinShareFactory.create()
        assert isinstance(share, BulletinShare)
        assert share.pk is not None

    def test_token_is_unique(self) -> None:
        """Two shares created by the factory have distinct tokens."""
        s1 = BulletinShareFactory.create()
        s2 = BulletinShareFactory.create()
        assert s1.token != s2.token

    def test_default_target_date_is_today(self) -> None:
        """Default target_date is today."""
        share = BulletinShareFactory.create()
        assert share.target_date == datetime.date.today()

    def test_bulletin_fk_is_populated(self) -> None:
        """Factory wires the bulletin FK by default."""
        share = BulletinShareFactory.create()
        assert share.bulletin_id is not None


@pytest.mark.django_db
class TestBulletinShareToString:
    """to_string() and __str__ return the expected format."""

    def test_to_string_format(self) -> None:
        """to_string() embeds token, region_id, and target_date."""
        region = MicroRegionFactory.create(region_id="CH-4222")
        share = BulletinShareFactory.create(
            token="abc123",
            region=region,
            target_date=datetime.date(2026, 4, 8),
        )
        result = share.to_string()
        assert "abc123" in result
        assert "CH-4222" in result
        assert "2026-04-08" in result

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same as to_string()."""
        share = BulletinShareFactory.create()
        assert str(share) == share.to_string()


@pytest.mark.django_db
class TestBulletinShareOrdering:
    """BulletinShare.objects.all() is ordered by -created_at."""

    def test_ordering_newest_first(self) -> None:
        """Queryset ordering is -created_at."""
        s1 = BulletinShareFactory.create()
        s2 = BulletinShareFactory.create()
        ids = list(BulletinShare.objects.values_list("pk", flat=True))
        # s2 was created after s1, so it appears first.
        assert ids.index(s2.pk) < ids.index(s1.pk)


@pytest.mark.django_db
class TestBulletinShareSetNull:
    """Deleting the linked Bulletin nulls bulletin_id and preserves the share."""

    def test_bulletin_delete_nulls_fk(self) -> None:
        """Deleting the Bulletin sets share.bulletin to None (SET_NULL)."""
        bulletin = BulletinFactory.create()
        share = BulletinShareFactory.create(bulletin=bulletin)
        bulletin_pk = bulletin.pk

        Bulletin.objects.filter(pk=bulletin_pk).delete()

        share.refresh_from_db()
        assert share.bulletin is None

    def test_share_preserved_after_bulletin_delete(self) -> None:
        """The BulletinShare row is not deleted when its Bulletin is deleted."""
        bulletin = BulletinFactory.create()
        share = BulletinShareFactory.create(bulletin=bulletin)
        share_pk = share.pk

        Bulletin.objects.filter(pk=bulletin.pk).delete()

        assert BulletinShare.objects.filter(pk=share_pk).exists()
