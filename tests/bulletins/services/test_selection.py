"""
tests/bulletins/services/test_selection.py — the batched issue pick.

``issues_for_date`` / ``select_default_issue`` are exercised through the
bulletin page in ``tests/public/test_bulletin_detail.py``; this module
covers ``select_bulletins_for_regions``, the batched form
``bulk_current_conditions`` reads (SNOW-1342), and SNOW-1368's built-only
pick (``select_default_pick``).
"""

from __future__ import annotations

import datetime
from datetime import UTC
from typing import Any

import pytest

from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.bulletins.services.selection import (
    IssuePick,
    is_built,
    select_bulletin_for_date,
    select_bulletins_for_regions,
    select_default_pick,
    select_pick_for_date,
)
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
)

DAY = datetime.date(2026, 3, 15)


def _issue(
    region: MicroRegion,
    hour: int,
    day: datetime.date = DAY,
    version: int = 0,
) -> Bulletin:
    """Create an issue valid from ``hour`` on ``day`` (UTC) for ``region``.

    ``version`` is its stored render-model version: 0 (the factory's
    default) is a failed build, ``RENDER_MODEL_VERSION`` a built one.
    """
    valid_from = datetime.datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    bulletin = BulletinFactory.create(
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_from + datetime.timedelta(hours=9),
        render_model_version=version,
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

    def test_is_one_query(self, django_assert_num_queries: Any) -> None:
        """Any number of regions is one query."""
        regions = [
            MicroRegionFactory.create(region_id=f"CH-4{n:03d}") for n in range(5)
        ]
        for region in regions:
            _issue(region, 8)

        with django_assert_num_queries(1):
            picked = select_bulletins_for_regions(regions, DAY)

        assert len(picked) == 5


@pytest.mark.django_db
class TestBuiltOnlyPick:
    """SNOW-1368: the latest issue that built, never another day's."""

    def test_a_failed_newer_issue_is_passed_over(self) -> None:
        """Evening built, morning failed: the evening, with the morning skipped."""
        region = MicroRegionFactory.create(region_id="CH-5001")
        evening = _issue(region, 2, version=RENDER_MODEL_VERSION)
        morning = _issue(region, 6)

        assert select_pick_for_date(region, DAY) == IssuePick(evening, morning)
        assert select_bulletin_for_date(region, DAY) == evening

    def test_a_stale_newer_issue_is_passed_over(self) -> None:
        """A version below current reads as unbuilt, as the rating treats it."""
        region = MicroRegionFactory.create(region_id="CH-5002")
        evening = _issue(region, 2, version=RENDER_MODEL_VERSION)
        stale = _issue(region, 6, version=RENDER_MODEL_VERSION - 1)

        assert not is_built(stale)
        assert select_pick_for_date(region, DAY) == IssuePick(evening, stale)

    def test_all_failed_keeps_the_latest_with_nothing_skipped(self) -> None:
        """No issue built: the latest, so its error card still shows."""
        region = MicroRegionFactory.create(region_id="CH-5003")
        _issue(region, 2)
        morning = _issue(region, 6)

        assert select_pick_for_date(region, DAY) == IssuePick(morning, None)

    def test_an_older_failed_issue_is_not_named(self) -> None:
        """Only a NEWER unbuilt issue is skipped; an older one is irrelevant."""
        region = MicroRegionFactory.create(region_id="CH-5004")
        _issue(region, 2)
        morning = _issue(region, 6, version=RENDER_MODEL_VERSION)

        assert select_pick_for_date(region, DAY) == IssuePick(morning, None)

    def test_never_falls_back_to_another_day(self) -> None:
        """A failed day does not borrow the built issue of the day before."""
        region = MicroRegionFactory.create(region_id="CH-5005")
        _issue(
            region,
            6,
            day=DAY - datetime.timedelta(days=1),
            version=RENDER_MODEL_VERSION,
        )
        failed = _issue(region, 6)

        assert select_pick_for_date(region, DAY) == IssuePick(failed, None)

    def test_no_issues(self) -> None:
        """An empty day picks nothing."""
        assert select_default_pick([], DAY) == IssuePick(None, None)

    def test_the_batched_pick_is_built_only_too(self) -> None:
        """bulk_current_conditions gets the same built issue."""
        region = MicroRegionFactory.create(region_id="CH-5006")
        evening = _issue(region, 2, version=RENDER_MODEL_VERSION)
        _issue(region, 6)

        assert select_bulletins_for_regions([region], DAY) == {region.pk: evening}
