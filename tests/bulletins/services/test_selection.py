"""
tests/bulletins/services/test_selection.py — the batched issue pick.

``issues_for_date`` / ``select_default_issue`` are exercised through the
bulletin page in ``tests/public/test_bulletin_detail.py``; this module
covers ``select_bulletins_for_regions``, the batched form
``bulk_current_conditions`` reads (SNOW-1342).
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest

from apps.bulletins.models import Bulletin
from apps.bulletins.services.selection import (
    select_bulletin_for_date,
    select_bulletins_for_regions,
)
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

DAY = datetime.date(2026, 3, 15)


def _issue(region: MicroRegion, hour: int, day: datetime.date = DAY) -> Bulletin:
    """Create an issue valid from ``hour`` on ``day`` (UTC) for ``region``."""
    valid_from = datetime.datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_from + datetime.timedelta(hours=9),
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)
    return bulletin


@pytest.mark.django_db
class TestSelectBulletinsForRegions:
    """Tests for select_bulletins_for_regions."""

    def test_picks_what_the_single_region_rule_picks(self) -> None:
        """Each region's pick is select_bulletin_for_date's pick."""
        first = MicroRegionFactory.create(region_id="CH-1111")
        second = MicroRegionFactory.create(region_id="CH-2222")
        _issue(first, 6)
        _issue(first, 8)
        _issue(second, 7)

        picked = select_bulletins_for_regions([first, second], DAY)

        assert picked == {
            first.pk: select_bulletin_for_date(first, DAY),
            second.pk: select_bulletin_for_date(second, DAY),
        }

    def test_region_without_an_issue_is_absent(self) -> None:
        """A region with no issue forecasting the day has no entry."""
        region = MicroRegionFactory.create(region_id="CH-3333")
        _issue(region, 8, day=DAY - datetime.timedelta(days=3))

        assert select_bulletins_for_regions([region], DAY) == {}

    def test_is_one_query_and_defers_raw_data(
        self, django_assert_num_queries: Any
    ) -> None:
        """Any number of regions is one query, and no CAAML is loaded."""
        regions = [
            MicroRegionFactory.create(region_id=f"CH-4{n:03d}") for n in range(5)
        ]
        for region in regions:
            _issue(region, 8)

        with django_assert_num_queries(1):
            picked = select_bulletins_for_regions(regions, DAY)

        assert len(picked) == 5
        assert all("raw_data" in b.get_deferred_fields() for b in picked.values())
