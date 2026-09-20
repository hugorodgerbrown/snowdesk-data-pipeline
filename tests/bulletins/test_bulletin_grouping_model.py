"""
tests/bulletins/test_bulletin_grouping_model.py — Tests for BulletinGrouping.

Covers:
  - to_string() returns the expected human-readable format.
  - __str__ delegates to to_string().
  - Default ordering is by -target_date (most recent first).
  - for_date() queryset helper filters by target_date.
  - degenerate() selects exactly the rows the ingest-time guard would now
    refuse to write, and is the exact COMPLEMENT of the candidate queryset
    backfill_bulletin_groupings runs (SNOW-1001). The two share
    MIN_GROUPED_REGIONS but not the Count(..., filter=Q(...)) expression, so
    the partition test is what fails if one is edited and not the other.
  - Admin class is registered for the model.
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib import admin

from apps.bulletins.management.commands.backfill_bulletin_groupings import (
    candidate_bulletins,
)
from apps.bulletins.models import Bulletin, BulletinGrouping
from tests.factories import (
    BulletinFactory,
    BulletinGroupingFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)

_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
}

# One rung per cardinality the predicate has to judge, with the number of
# boundaried and unboundaried micro-regions the bulletin links.
_LADDER: dict[str, tuple[int, int]] = {
    "no-regions": (0, 0),
    "three-links-no-geometry": (0, 3),
    "one-boundaried": (1, 0),
    "one-boundaried-two-bare": (1, 2),
    "two-boundaried": (2, 0),
    "ten-boundaried": (10, 0),
}

# The rungs below MIN_GROUPED_REGIONS — no outline may be drawn for these.
_DEGENERATE_RUNGS = {
    "no-regions",
    "three-links-no-geometry",
    "one-boundaried",
    "one-boundaried-two-bare",
}


def _make_bulletin(rung: str, *, suffix: str) -> Bulletin:
    """Create one bulletin at the named rung of the cardinality ladder.

    Args:
        rung: A key of ``_LADDER``, naming how many regions to link.
        suffix: Distinguishes the two bulletins built per rung (one that
            carries a grouping row, one that does not) and keeps every
            region id unique.

    Returns:
        The created Bulletin, with its region links in place.

    """
    boundaried, bare = _LADDER[rung]
    token = f"{rung}-{suffix}"
    major = MajorRegionFactory.create(prefix=f"CH-{token}", country="CH")
    sub = SubRegionFactory.create(prefix=f"CH-{token}-1", major=major)
    bulletin = BulletinFactory.create(
        bulletin_id=token, pipeline_run=PipelineRunFactory.create()
    )
    for index in range(boundaried + bare):
        region = MicroRegionFactory.create(
            region_id=f"{token}-{index}",
            subregion=sub,
            boundary=_BOUNDARY if index < boundaried else None,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


@pytest.mark.django_db
class TestBulletinGroupingToString:
    """Tests for BulletinGrouping.to_string()."""

    def test_to_string_format(self) -> None:
        """to_string returns 'BulletinGrouping(<id>, <date>, <countries>)'."""
        grouping = BulletinGroupingFactory.create(
            target_date=datetime.date(2026, 1, 15),
            countries=["CH"],
        )
        s = grouping.to_string()
        assert "BulletinGrouping(" in s
        assert "2026-01-15" in s
        assert "CH" in s

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ is identical to to_string()."""
        grouping = BulletinGroupingFactory.create()
        assert str(grouping) == grouping.to_string()

    def test_to_string_includes_bulletin_id(self) -> None:
        """to_string embeds the bulletin_id from the linked bulletin."""
        grouping = BulletinGroupingFactory.create()
        assert grouping.bulletin.bulletin_id in grouping.to_string()


@pytest.mark.django_db
class TestBulletinGroupingOrdering:
    """Tests for BulletinGrouping default ordering (-target_date)."""

    def test_ordering_most_recent_first(self) -> None:
        """Rows are returned most-recent-date-first by default."""
        older = BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 14))
        newer = BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 15))

        rows = list(BulletinGrouping.objects.all())
        assert rows[0].pk == newer.pk
        assert rows[1].pk == older.pk


@pytest.mark.django_db
class TestBulletinGroupingQuerySet:
    """Tests for BulletinGroupingQuerySet helpers."""

    def test_for_date_returns_matching_rows(self) -> None:
        """for_date() returns only groupings whose target_date matches."""
        d1 = datetime.date(2026, 1, 14)
        d2 = datetime.date(2026, 1, 15)
        g1 = BulletinGroupingFactory.create(target_date=d1)
        BulletinGroupingFactory.create(target_date=d2)

        result = list(BulletinGrouping.objects.for_date(d1))

        assert len(result) == 1
        assert result[0].pk == g1.pk

    def test_for_date_returns_empty_when_no_match(self) -> None:
        """for_date() returns an empty queryset when no row matches."""
        BulletinGroupingFactory.create(target_date=datetime.date(2026, 1, 14))

        result = BulletinGrouping.objects.for_date(datetime.date(2026, 1, 16))

        assert result.count() == 0


@pytest.mark.django_db
class TestDegeneratePredicate:
    """Tests for degenerate() and its complement in backfill_bulletin_groupings."""

    def test_degenerate_selects_the_rows_below_the_threshold(self) -> None:
        """Every rung under two boundaried regions is selected; the rest are not."""
        for rung in _LADDER:
            BulletinGroupingFactory.create(
                bulletin=_make_bulletin(rung, suffix="grouped"), countries=["CH"]
            )

        selected = {
            grouping.bulletin.bulletin_id.removesuffix("-grouped")
            for grouping in BulletinGrouping.objects.degenerate().select_related(
                "bulletin"
            )
        }

        assert selected == _DEGENERATE_RUNGS

    def test_degenerate_and_backfill_candidates_partition_the_ladder(self) -> None:
        """The two queries are exact complements: union is everything, overlap is nothing.

        They share MIN_GROUPED_REGIONS but express the boundaried-region count
        separately, so this is what catches an edit to one and not the other.
        Each rung is built twice — once carrying a grouping row (only such a
        bulletin can be reached by degenerate()) and once without (only such a
        bulletin can be a backfill candidate) — and the rung name is the
        common key.
        """
        for rung in _LADDER:
            BulletinGroupingFactory.create(
                bulletin=_make_bulletin(rung, suffix="grouped"), countries=["CH"]
            )
            _make_bulletin(rung, suffix="bare")

        degenerate_rungs = {
            grouping.bulletin.bulletin_id.removesuffix("-grouped")
            for grouping in BulletinGrouping.objects.degenerate().select_related(
                "bulletin"
            )
        }
        candidate_rungs = {
            bulletin.bulletin_id.removesuffix("-bare")
            for bulletin in candidate_bulletins()
        }

        assert degenerate_rungs | candidate_rungs == set(_LADDER)
        assert degenerate_rungs & candidate_rungs == set()

    def test_backfill_candidates_never_include_a_grouped_bulletin(self) -> None:
        """A bulletin that already has a row is out of the candidate set regardless of rung."""
        for rung in _LADDER:
            BulletinGroupingFactory.create(
                bulletin=_make_bulletin(rung, suffix="grouped"), countries=["CH"]
            )

        assert not candidate_bulletins().exists()


class TestBulletinGroupingAdmin:
    """Tests for BulletinGrouping admin registration."""

    def test_admin_is_registered(self) -> None:
        """BulletinGrouping has an explicit admin class registered."""
        assert admin.site.is_registered(BulletinGrouping)
