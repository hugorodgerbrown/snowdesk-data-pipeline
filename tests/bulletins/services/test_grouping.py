"""
tests/bulletins/services/test_grouping.py — Tests for the grouping service.

Covers:
  - Two adjacent micro-regions with shared-edge GeoJSON Polygons dissolve into
    a single boundary (Polygon or MultiPolygon) and create a BulletinGrouping row.
  - A bulletin whose regions span two countries yields a sorted, two-element
    ``countries`` list on the resulting grouping.
  - A bulletin with no boundaried regions returns None and creates no row
    (any stale row is deleted).
  - Calling compute_bulletin_grouping_boundary twice is idempotent — updates in
    place and produces exactly one BulletinGrouping row.
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from apps.bulletins.models import BulletinGrouping
from apps.bulletins.services.grouping import compute_bulletin_grouping_boundary
from tests.factories import (
    BulletinFactory,
    BulletinGroupingFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)

# Two adjacent unit squares sharing the edge x=1.
# Left square: (0,0)-(1,1). Right square: (1,0)-(2,1).
_LEFT_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
}
_RIGHT_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [[[1.0, 0.0], [2.0, 0.0], [2.0, 1.0], [1.0, 1.0], [1.0, 0.0]]],
}
# An isolated square far from the others (no shared edge).
_FAR_BOUNDARY = {
    "type": "Polygon",
    "coordinates": [
        [[10.0, 10.0], [11.0, 10.0], [11.0, 11.0], [10.0, 11.0], [10.0, 10.0]]
    ],
}

# Valid_from that resolves to 2026-01-15 under the target_day_for_valid_from
# morning rule (16:00 UTC on the 14th → morning of the 15th via SLF convention).
_VALID_FROM = datetime.datetime(2026, 1, 14, 16, 0, 0, tzinfo=UTC)


def _make_ch_hierarchy() -> tuple:
    """Return (major_ch, sub_ch) with country='CH'."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    sub = SubRegionFactory.create(prefix="CH-41", major=major)
    return major, sub


def _make_at_hierarchy() -> tuple:
    """Return (major_at, sub_at) with country='AT'."""
    major = MajorRegionFactory.create(prefix="AT-2", country="AT")
    sub = SubRegionFactory.create(prefix="AT-21", major=major)
    return major, sub


@pytest.mark.django_db
class TestComputeBulletinGroupingBoundary:
    """Tests for compute_bulletin_grouping_boundary."""

    def test_two_adjacent_regions_dissolve_into_one_boundary(self) -> None:
        """Two micro-regions with shared-edge polygons dissolve to a single shape."""
        _, sub = _make_ch_hierarchy()
        r1 = MicroRegionFactory.create(
            region_id="CH-4115", subregion=sub, boundary=_LEFT_BOUNDARY
        )
        r2 = MicroRegionFactory.create(
            region_id="CH-4116", subregion=sub, boundary=_RIGHT_BOUNDARY
        )
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(
            valid_from=_VALID_FROM, valid_to=_VALID_FROM, pipeline_run=run
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=r1)
        RegionBulletinFactory.create(bulletin=bulletin, region=r2)

        result = compute_bulletin_grouping_boundary(bulletin)

        assert result is not None
        assert BulletinGrouping.objects.count() == 1
        # Dissolved shape must be a valid GeoJSON geometry.
        assert result.boundary["type"] in {"Polygon", "MultiPolygon"}
        assert result.target_date == datetime.date(2026, 1, 15)

    def test_cross_country_bulletin_yields_sorted_countries(self) -> None:
        """A bulletin spanning CH and AT produces a sorted ['AT', 'CH'] list."""
        _, sub_ch = _make_ch_hierarchy()
        _, sub_at = _make_at_hierarchy()
        r_ch = MicroRegionFactory.create(
            region_id="CH-4115", subregion=sub_ch, boundary=_LEFT_BOUNDARY
        )
        r_at = MicroRegionFactory.create(
            region_id="AT-2100", subregion=sub_at, boundary=_RIGHT_BOUNDARY
        )
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(
            valid_from=_VALID_FROM, valid_to=_VALID_FROM, pipeline_run=run
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=r_ch)
        RegionBulletinFactory.create(bulletin=bulletin, region=r_at)

        result = compute_bulletin_grouping_boundary(bulletin)

        assert result is not None
        assert result.countries == ["AT", "CH"]

    def test_no_boundaried_regions_returns_none_and_no_row(self) -> None:
        """A bulletin whose regions carry no boundary produces None and no DB row."""
        _, sub = _make_ch_hierarchy()
        r = MicroRegionFactory.create(region_id="CH-4115", subregion=sub, boundary=None)
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(
            valid_from=_VALID_FROM, valid_to=_VALID_FROM, pipeline_run=run
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=r)

        result = compute_bulletin_grouping_boundary(bulletin)

        assert result is None
        assert BulletinGrouping.objects.count() == 0

    def test_no_boundaried_regions_deletes_stale_row(self) -> None:
        """An existing stale BulletinGrouping is deleted when all regions lose their boundary."""
        _, sub = _make_ch_hierarchy()
        r = MicroRegionFactory.create(region_id="CH-4115", subregion=sub, boundary=None)
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(
            valid_from=_VALID_FROM, valid_to=_VALID_FROM, pipeline_run=run
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=r)
        # Pre-create a stale grouping row as if it were created by a prior ingest.
        BulletinGroupingFactory.create(
            bulletin=bulletin,
            target_date=datetime.date(2026, 1, 15),
            boundary=_LEFT_BOUNDARY,
            countries=["CH"],
        )
        assert BulletinGrouping.objects.count() == 1

        result = compute_bulletin_grouping_boundary(bulletin)

        assert result is None
        assert BulletinGrouping.objects.count() == 0

    def test_idempotent_update_in_place(self) -> None:
        """Calling the function twice produces exactly one BulletinGrouping row."""
        _, sub = _make_ch_hierarchy()
        r = MicroRegionFactory.create(
            region_id="CH-4115", subregion=sub, boundary=_LEFT_BOUNDARY
        )
        run = PipelineRunFactory.create()
        bulletin = BulletinFactory.create(
            valid_from=_VALID_FROM, valid_to=_VALID_FROM, pipeline_run=run
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=r)

        result1 = compute_bulletin_grouping_boundary(bulletin)
        result2 = compute_bulletin_grouping_boundary(bulletin)

        assert result1 is not None
        assert result2 is not None
        assert result1.pk == result2.pk
        assert BulletinGrouping.objects.count() == 1
