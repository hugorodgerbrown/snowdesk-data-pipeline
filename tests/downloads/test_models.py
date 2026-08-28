"""
tests/downloads/test_models.py — Tests for apps.downloads.models.

Covers:
  DownloadArea to_string / __str__ — the three-step label fallback.
  DownloadAreaQuerySet.for_user / .regions / .custom.
  Meta.ordering — newest-first.
  The (user, area_id) unique constraint, and that it is scoped PER USER —
    two users may each hold the same area id.
  user FK is CASCADE — deleting the owner removes their areas.
"""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.downloads.models import DownloadArea
from tests.factories import DownloadAreaFactory, UserFactory


class TestDownloadAreaToString:
    """to_string and __str__ coverage."""

    def test_to_string_uses_name_when_set(self) -> None:
        """A named custom area is identified by its name."""
        area = DownloadAreaFactory.build(
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            name="Verbier bowl",
        )
        assert "Verbier bowl" in area.to_string()

    def test_to_string_falls_back_to_region_id(self) -> None:
        """An unnamed region area is identified by its region id."""
        area = DownloadAreaFactory.build(region_id="ch-4115", name="")
        assert "ch-4115" in area.to_string()

    def test_to_string_falls_back_again_to_area_id(self) -> None:
        """With neither a name nor a region id, the area id names the row."""
        area = DownloadAreaFactory.build(
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            name="",
        )
        assert "custom-abc" in area.to_string()

    def test_to_string_includes_the_kind(self) -> None:
        """The label says which of the two shapes this row is."""
        area = DownloadAreaFactory.build(region_id="ch-4115", name="")
        assert "Region" in area.to_string()

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ is to_string."""
        area = DownloadAreaFactory.build(region_id="ch-4115")
        assert str(area) == area.to_string()


@pytest.mark.django_db
class TestDownloadAreaQuerySet:
    """Queryset filter coverage."""

    def test_for_user_isolates_by_owner(self) -> None:
        """One user's areas are never visible through another's queryset."""
        mine = DownloadAreaFactory.create()
        theirs = DownloadAreaFactory.create()

        result = DownloadArea.objects.for_user(mine.user)

        assert list(result) == [mine]
        assert theirs not in result

    def test_regions_and_custom_partition_the_set(self) -> None:
        """Every row is in exactly one of the two kind filters."""
        user = UserFactory.create()
        region = DownloadAreaFactory.create(user=user)
        custom = DownloadAreaFactory.create(
            user=user,
            area_id="custom-abc",
            kind=DownloadArea.KIND.CUSTOM,
            region_id="",
            bbox=[7.0, 45.9, 7.3, 46.1],
        )

        owned = DownloadArea.objects.for_user(user)

        assert list(owned.regions()) == [region]
        assert list(owned.custom()) == [custom]

    def test_ordering_is_newest_first(self) -> None:
        """Meta.ordering puts the most recently created area first."""
        user = UserFactory.create()
        older = DownloadAreaFactory.create(user=user)
        newer = DownloadAreaFactory.create(user=user)
        # created_at is auto_now_add, so pin the older row explicitly rather
        # than relying on two writes landing in distinguishable microseconds.
        DownloadArea.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - datetime.timedelta(days=1)
        )

        assert list(DownloadArea.objects.for_user(user)) == [newer, older]


@pytest.mark.django_db
class TestDownloadAreaConstraints:
    """Uniqueness and cascade coverage."""

    def test_area_id_is_unique_per_user(self) -> None:
        """The same user cannot hold two rows for one area."""
        area = DownloadAreaFactory.create()

        with pytest.raises(IntegrityError):
            DownloadAreaFactory.create(user=area.user, area_id=area.area_id)

    def test_two_users_may_hold_the_same_area_id(self) -> None:
        """The constraint is per user — two people downloading one region is normal."""
        mine = DownloadAreaFactory.create()
        theirs = DownloadAreaFactory.create(area_id=mine.area_id)

        assert mine.area_id == theirs.area_id
        assert mine.user != theirs.user

    def test_deleting_the_user_removes_their_areas(self) -> None:
        """The FK is CASCADE — no orphaned rows survive an account deletion."""
        area = DownloadAreaFactory.create()

        area.user.delete()

        assert not DownloadArea.objects.filter(pk=area.pk).exists()
