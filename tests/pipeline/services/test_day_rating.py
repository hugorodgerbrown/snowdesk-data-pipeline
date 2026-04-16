"""
tests/pipeline/services/test_day_rating.py — Tests for the day_rating service.

Covers:
  - Single-bulletin day → min == max == its rating
  - Two bulletins same region+date: higher rating wins for max; lower for min
  - Two bulletins same region+date: earlier has higher rating → earlier wins (max)
  - Tied max → later valid_from chosen as source_bulletin
  - Tied min → later valid_from supplies min_subdivision
  - Bulletin with render_model_version = 0 excluded → row ends up no_rating for both
  - Bulletin spanning two calendar days → both rows touched
  - Malformed render model → both min and max fall back to no_rating
  - apply_bulletin_day_ratings exception inside upsert_bulletin is caught
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest

from pipeline.models import RegionDayRating
from pipeline.services.day_rating import (
    DAY_RATING_VERSION,
    apply_bulletin_day_ratings,
    recompute_region_day,
)
from pipeline.services.render_model import RENDER_MODEL_VERSION
from tests.factories import (
    BulletinFactory,
    PipelineRunFactory,
    RegionBulletinFactory,
    RegionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bulletin_for_region(
    region,
    valid_from: datetime.datetime,
    valid_to: datetime.datetime,
    rating: str = "low",
    subdivision: str = "",
    render_model_version: int | None = None,
):
    """
    Create a Bulletin linked to a region with a specific danger rating.

    Args:
        region: Region to link the bulletin to.
        valid_from: Validity start.
        valid_to: Validity end.
        rating: CAAML mainValue string.
        subdivision: CH subdivision string (raw, e.g. "minus") or empty.
        render_model_version: Override; defaults to RENDER_MODEL_VERSION.

    Returns:
        The created Bulletin.

    """
    if render_model_version is None:
        render_model_version = RENDER_MODEL_VERSION

    render_model = {
        "version": render_model_version,
        "danger": {
            "key": rating,
            "subdivision": subdivision,
            "number": 1,
        },
        "traits": [],
    }
    bulletin = BulletinFactory.create(
        issued_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_to,
        render_model=render_model,
        render_model_version=render_model_version,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


# ---------------------------------------------------------------------------
# recompute_region_day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecomputeRegionDay:
    """Tests for recompute_region_day()."""

    def test_single_bulletin_creates_row(self) -> None:
        """A single qualifying bulletin produces a RegionDayRating row with min == max."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(region, vf, vt, rating="moderate")

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == "moderate"
        assert rdr.min_rating == "moderate"
        assert rdr.version == DAY_RATING_VERSION

    def test_higher_rating_wins_for_max_lower_for_min(self) -> None:
        """Two bulletins: max takes the higher rating, min takes the lower."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf_early = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt_early = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        vf_late = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt_late = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(region, vf_early, vt_early, rating="considerable")
        _make_bulletin_for_region(region, vf_late, vt_late, rating="low")

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == "considerable"
        assert rdr.min_rating == "low"

    def test_later_valid_from_wins_on_same_rating(self) -> None:
        """Tied max rating → latest valid_from bulletin is source_bulletin."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf_early = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt_early = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        vf_late = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt_late = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(region, vf_early, vt_early, rating="moderate")
        b_late = _make_bulletin_for_region(region, vf_late, vt_late, rating="moderate")

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source_bulletin_id == b_late.pk

    def test_earlier_higher_rating_not_chronological(self) -> None:
        """Max policy beats chronological order — earlier but higher rating wins."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf_early = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt_early = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        vf_late = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt_late = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        b_early = _make_bulletin_for_region(region, vf_early, vt_early, rating="high")
        _make_bulletin_for_region(region, vf_late, vt_late, rating="low")

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == "high"
        assert rdr.source_bulletin_id == b_early.pk

    def test_version_zero_bulletin_excluded(self) -> None:
        """Bulletins with render_model_version=0 are excluded; both ratings are no_rating."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region, vf, vt, rating="considerable", render_model_version=0
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.source_bulletin is None

    def test_no_bulletin_writes_no_rating(self) -> None:
        """When no qualifying bulletin exists, both min and max are no_rating."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 20)

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING

    def test_dry_run_does_not_write(self) -> None:
        """commit=False logs but does not create a RegionDayRating row."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        _make_bulletin_for_region(region, vf, vt, rating="moderate")

        recompute_region_day(region, day, commit=False)

        assert not RegionDayRating.objects.filter(region=region, date=day).exists()

    def test_malformed_render_model_unrecognised_key_yields_no_rating(self) -> None:
        """A bulletin whose render model returns an unrecognised key is skipped.

        Monkeypatches _extract_max_from_render_model to return an unrecognised
        key so that _rating_rank returns -1. With best_rank initialised to -1
        and worst_rank initialised to len(_RATING_ORDER), no candidate wins either
        tracker; both must be written as no_rating / source_bulletin=None.
        """
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        _make_bulletin_for_region(region, vf, vt, rating="low")

        with patch(
            "pipeline.services.day_rating._extract_max_from_render_model",
            return_value=("__unrecognised__", ""),
        ):
            recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.source_bulletin is None

    def test_tied_min_later_valid_from_supplies_min_subdivision(self) -> None:
        """When two bulletins share the lowest rating, the later valid_from gives min_subdivision."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf_early = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt_early = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        vf_late = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt_late = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        # Both are "moderate" (equal min); late bulletin has subdivision "plus"
        _make_bulletin_for_region(
            region, vf_early, vt_early, rating="moderate", subdivision="minus"
        )
        _make_bulletin_for_region(
            region, vf_late, vt_late, rating="moderate", subdivision="plus"
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "moderate"
        # Later valid_from wins the tie → subdivision from the late bulletin
        assert rdr.min_subdivision == "+"

    def test_source_bulletin_is_max_bulletin(self) -> None:
        """source_bulletin always points to the max-rating bulletin, not the min."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf_early = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt_early = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        vf_late = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt_late = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(region, vf_early, vt_early, rating="low")
        b_high = _make_bulletin_for_region(
            region, vf_late, vt_late, rating="considerable"
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source_bulletin_id == b_high.pk
        assert rdr.min_rating == "low"
        assert rdr.max_rating == "considerable"


# ---------------------------------------------------------------------------
# apply_bulletin_day_ratings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyBulletinDayRatings:
    """Tests for apply_bulletin_day_ratings()."""

    def test_multi_day_bulletin_touches_both_days(self) -> None:
        """A bulletin spanning two days creates two RegionDayRating rows."""
        region = RegionFactory.create(region_id="CH-4115")
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        bulletin = _make_bulletin_for_region(region, vf, vt, rating="low")

        apply_bulletin_day_ratings(bulletin)

        assert RegionDayRating.objects.filter(
            region=region, date=datetime.date(2026, 1, 14)
        ).exists()
        assert RegionDayRating.objects.filter(
            region=region, date=datetime.date(2026, 1, 15)
        ).exists()

    def test_applies_to_all_linked_regions(self) -> None:
        """apply_bulletin_day_ratings touches every region linked to the bulletin."""
        region_a = RegionFactory.create(region_id="CH-1111")
        region_b = RegionFactory.create(region_id="CH-2222")
        vf = datetime.datetime(2026, 2, 10, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 2, 10, 17, 0, tzinfo=UTC)
        render_model = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "moderate", "subdivision": "", "number": 2},
            "traits": [],
        }
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model=render_model,
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(bulletin=bulletin, region=region_a)
        RegionBulletinFactory.create(bulletin=bulletin, region=region_b)

        apply_bulletin_day_ratings(bulletin)

        assert RegionDayRating.objects.filter(
            region=region_a, date=datetime.date(2026, 2, 10)
        ).exists()
        assert RegionDayRating.objects.filter(
            region=region_b, date=datetime.date(2026, 2, 10)
        ).exists()


# ---------------------------------------------------------------------------
# Exception swallowing inside upsert_bulletin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertBulletinSwallowsException:
    """apply_bulletin_day_ratings exceptions inside upsert_bulletin are swallowed."""

    def test_exception_is_caught_not_propagated(self) -> None:
        """When apply_bulletin_day_ratings raises, upsert_bulletin still returns."""
        from pipeline.services.data_fetcher import upsert_bulletin

        run = PipelineRunFactory.create()

        raw = {
            "bulletinID": "test-exc-001",
            "publicationTime": "2026-01-15T08:00:00Z",
            "validTime": {
                "startTime": "2026-01-14T17:00:00Z",
                "endTime": "2026-01-15T17:00:00Z",
            },
            "lang": "en",
            "unscheduled": False,
            "regions": [{"regionID": "CH-9999", "name": "Test Region"}],
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
        }

        with patch(
            "pipeline.services.data_fetcher.apply_bulletin_day_ratings",
            side_effect=RuntimeError("test explosion"),
        ):
            result = upsert_bulletin(raw, run)

        # upsert_bulletin should still return despite the exception (not re-raise).
        assert result is True  # created = True
