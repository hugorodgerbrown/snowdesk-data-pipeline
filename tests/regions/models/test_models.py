"""
tests/regions/models/test_models.py — Tests for the BulletinQuerySet
valid-from date helpers.

SNOW-1342 removed the Bulletin helpers that parsed ``raw_data``
(``region_count``, ``get_danger_ratings``, ``get_avalanche_problems``,
``highest_danger_rating``) along with their tests.
"""

from datetime import UTC, date, datetime

import pytest

from apps.bulletins.models import Bulletin
from tests.factories import BulletinFactory


@pytest.mark.django_db
class TestBulletinLatestValidFromDate:
    """Tests for ``BulletinQuerySet.latest_valid_from_date()``."""

    def test_returns_none_when_empty(self) -> None:
        """Empty queryset returns ``None`` rather than raising."""
        assert Bulletin.objects.latest_valid_from_date() is None

    def test_returns_max_valid_from_day(self) -> None:
        """Returns the ``valid_from`` day of the newest bulletin."""
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 8, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 17, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.latest_valid_from_date() == date(2026, 4, 15)

    def test_honours_queryset_filters(self) -> None:
        """Works off the chained queryset rather than the full table."""
        BulletinFactory.create(
            lang="en",
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            lang="de",
            issued_at=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 17, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.filter(lang="en").latest_valid_from_date() == date(
            2026, 4, 15
        )


@pytest.mark.django_db
class TestBulletinEarliestValidFromDate:
    """Tests for ``BulletinQuerySet.earliest_valid_from_date()``."""

    def test_returns_none_when_empty(self) -> None:
        """Empty queryset returns ``None`` rather than raising."""
        assert Bulletin.objects.earliest_valid_from_date() is None

    def test_returns_min_valid_from_day(self) -> None:
        """Returns the ``valid_from`` day of the oldest bulletin."""
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 12, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 13, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.earliest_valid_from_date() == date(2026, 4, 10)

    def test_honours_queryset_filters(self) -> None:
        """Works off the chained queryset rather than the full table."""
        BulletinFactory.create(
            lang="en",
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            lang="de",
            issued_at=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 5, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 6, 12, 0, tzinfo=UTC),
        )

        assert Bulletin.objects.filter(lang="en").earliest_valid_from_date() == date(
            2026, 4, 10
        )
