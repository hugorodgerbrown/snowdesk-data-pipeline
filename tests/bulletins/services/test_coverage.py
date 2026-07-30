"""
tests/bulletins/services/test_coverage.py — Tests for the coverage helper.

Covers:
  - A region with a RegionDayRating row is in covered_region_ids().
  - A region without any RegionDayRating row is not in covered_region_ids().
  - The result is cached: a second call issues no additional DB queries.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.coverage import covered_region_ids
from tests.factories import (
    MicroRegionFactory,
    RegionDayRatingFactory,
)


@pytest.fixture(autouse=True)
def clear_coverage_cache() -> None:
    """Clear the coverage cache before each test to avoid cross-test pollution."""
    cache.clear()


@pytest.mark.django_db
def test_covered_region_included() -> None:
    """A region with at least one RegionDayRating row is in covered_region_ids()."""
    region = MicroRegionFactory.create(region_id="CH-4115")
    RegionDayRatingFactory.create(
        region=region,
        date=datetime.date(2026, 1, 15),
        max_rating=RegionDayRating.Rating.LOW,
    )

    result = covered_region_ids()

    assert "CH-4115" in result


@pytest.mark.django_db
def test_uncovered_region_excluded() -> None:
    """A region with no RegionDayRating rows is not in covered_region_ids()."""
    MicroRegionFactory.create(region_id="CH-9999")
    # No RegionDayRating for CH-9999.

    result = covered_region_ids()

    assert "CH-9999" not in result


@pytest.mark.django_db
def test_covered_and_uncovered_coexist() -> None:
    """Covered and uncovered regions coexist correctly in a single call."""
    covered = MicroRegionFactory.create(region_id="CH-4115")
    MicroRegionFactory.create(region_id="CH-9999")
    RegionDayRatingFactory.create(
        region=covered,
        date=datetime.date(2026, 1, 15),
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    result = covered_region_ids()

    assert "CH-4115" in result
    assert "CH-9999" not in result


@pytest.mark.django_db
def test_result_is_cached_no_extra_queries() -> None:
    """A second call to covered_region_ids() issues no additional DB queries."""
    region = MicroRegionFactory.create(region_id="CH-4115")
    RegionDayRatingFactory.create(
        region=region,
        date=datetime.date(2026, 1, 15),
        max_rating=RegionDayRating.Rating.LOW,
    )

    # Prime the cache with the first call.
    first_result = covered_region_ids()
    assert "CH-4115" in first_result

    # Second call must hit the cache — zero queries.
    with CaptureQueriesContext(connection) as ctx:
        second_result = covered_region_ids()

    assert len(ctx.captured_queries) == 0
    assert second_result == first_result
