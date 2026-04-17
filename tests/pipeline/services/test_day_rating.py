"""
tests/pipeline/services/test_day_rating.py — Tests for the day_rating service.

Covers (v4 single-bulletin policy):
  - _target_day: morning/evening/boundary rules.
  - Single bulletin, two traits (dry=1, wet=3) → min=low, max=considerable.
  - Single bulletin, single trait (dry=3) → min=max=considerable (stable).
  - Single bulletin with empty traits but danger.key="low" → min=max=low (fallback).
  - Morning-of-X wins over prior-evening: morning chosen, prior-evening ignored.
  - Prior-evening-only fallback: only prior-evening exists, used for day X.
  - Evening-of-X is NOT included in day X (its target is X+1 → no_rating for X).
  - Bulletin with render_model_version=0 → excluded.
  - Trait with missing/invalid danger_level → skipped without crashing.
  - All traits invalid → no_rating (no fallback when traits list is non-empty).
  - Malformed render_model (entirely empty dict) → falls back to headline key "low".
  - No valid candidate → no_rating.
  - commit=False does not write.
  - apply_bulletin_day_ratings creates exactly one (region, target_day) row per region.
  - apply_bulletin_day_ratings exception inside upsert_bulletin is swallowed.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest

from pipeline.models import RegionDayRating
from pipeline.services.day_rating import (
    DAY_RATING_VERSION,
    _target_day,
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
    traits: list | None = None,
    headline_key: str = "low",
    headline_subdivision: str | None = None,
    render_model_version: int | None = None,
):
    """
    Create a Bulletin linked to a region with explicit traits and headline danger.

    Args:
        region: Region to link the bulletin to.
        valid_from: Validity start.
        valid_to: Validity end.
        traits: List of trait dicts to embed in render_model["traits"].
                Defaults to an empty list (quiet-day bulletin).
        headline_key: The aggregate danger key (render_model["danger"]["key"]).
        headline_subdivision: Raw CH subdivision string (e.g. "plus") or None.
        render_model_version: Override; defaults to RENDER_MODEL_VERSION.

    Returns:
        The created Bulletin.

    """
    if render_model_version is None:
        render_model_version = RENDER_MODEL_VERSION
    if traits is None:
        traits = []

    render_model = {
        "version": render_model_version,
        "danger": {
            "key": headline_key,
            "subdivision": headline_subdivision,
            "number": 1,
        },
        "traits": traits,
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


def _trait(danger_level: int, category: str = "dry") -> dict:
    """
    Build a minimal trait dict for use in render_model["traits"].

    Args:
        danger_level: Integer 1–5.
        category: "dry" or "wet".

    Returns:
        A trait dict with the minimum fields recompute_region_day reads.

    """
    return {
        "category": category,
        "danger_level": danger_level,
        "time_period": "all_day",
        "title": f"{category.capitalize()} avalanches",
        "problems": [],
    }


# ---------------------------------------------------------------------------
# _target_day
# ---------------------------------------------------------------------------


class TestTargetDay:
    """Unit tests for _target_day() — no DB required."""

    def test_morning_bulletin_targets_same_day(self) -> None:
        """valid_from.hour < 12 → target day is valid_from.date()."""
        b = BulletinFactory.build(
            valid_from=datetime.datetime(2026, 3, 25, 7, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 3, 25, 17, 0, tzinfo=UTC),
        )
        assert _target_day(b) == datetime.date(2026, 3, 25)

    def test_evening_bulletin_targets_next_day(self) -> None:
        """valid_from.hour >= 12 → target day is valid_from.date() + 1."""
        b = BulletinFactory.build(
            valid_from=datetime.datetime(2026, 3, 25, 16, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 3, 26, 17, 0, tzinfo=UTC),
        )
        assert _target_day(b) == datetime.date(2026, 3, 26)

    def test_noon_boundary_is_evening(self) -> None:
        """valid_from.hour == 12 (exactly noon) is treated as evening (>= 12)."""
        b = BulletinFactory.build(
            valid_from=datetime.datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 3, 26, 17, 0, tzinfo=UTC),
        )
        # noon is >= 12 so target is the NEXT day
        assert _target_day(b) == datetime.date(2026, 3, 26)

    def test_morning_boundary_11_59_targets_same_day(self) -> None:
        """valid_from.hour == 11 (just before noon) is treated as morning."""
        b = BulletinFactory.build(
            valid_from=datetime.datetime(2026, 3, 25, 11, 59, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 3, 25, 17, 0, tzinfo=UTC),
        )
        assert _target_day(b) == datetime.date(2026, 3, 25)


# ---------------------------------------------------------------------------
# recompute_region_day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRecomputeRegionDay:
    """Tests for recompute_region_day() with the v4 single-bulletin policy."""

    def test_single_bulletin_two_traits_variable(self) -> None:
        """Single bulletin with dry=1 and wet=3 → min=low, max=considerable."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        # Prior-evening issue: target day = 2026-01-15 (evening of Jan 14)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(1, "dry"), _trait(3, "wet")],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "low"
        assert rdr.max_rating == "considerable"
        assert rdr.version == DAY_RATING_VERSION

    def test_single_bulletin_single_trait_stable(self) -> None:
        """Single bulletin with one trait (dry=3) → min=max=considerable (stable)."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(3, "dry")],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "considerable"
        assert rdr.max_rating == "considerable"

    def test_empty_traits_falls_back_to_headline_key(self) -> None:
        """Bulletin with empty traits falls back to danger.key for min/max."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[],
            headline_key="low",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "low"
        assert rdr.max_rating == "low"

    def test_morning_wins_over_prior_evening(self) -> None:
        """
        When both morning-of-X and prior-evening-of-(X-1) exist, morning-of-X
        is chosen as the single bulletin. Prior-evening's traits are ignored.

        Prior-evening: dry=3 (considerable).
        Morning-of-X:  dry=2 (moderate).

        Day X's row should reflect the morning bulletin only: min=max=moderate.
        source_bulletin must be the morning bulletin.
        """
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 25)

        # Prior-evening (Mar 24 16:00) → target Mar 25, dry=3
        vf_eve = datetime.datetime(2026, 3, 24, 16, 0, tzinfo=UTC)
        vt_eve = datetime.datetime(2026, 3, 25, 17, 0, tzinfo=UTC)
        _make_bulletin_for_region(
            region,
            vf_eve,
            vt_eve,
            traits=[_trait(3, "dry")],
            headline_key="considerable",
        )

        # Morning of Mar 25 → target Mar 25, dry=2
        vf_morn = datetime.datetime(2026, 3, 25, 7, 0, tzinfo=UTC)
        vt_morn = datetime.datetime(2026, 3, 25, 17, 0, tzinfo=UTC)
        b_morning = _make_bulletin_for_region(
            region,
            vf_morn,
            vt_morn,
            traits=[_trait(2, "dry")],
            headline_key="moderate",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Only morning bulletin's traits count: dry=2 → moderate.
        assert rdr.min_rating == "moderate"
        assert rdr.max_rating == "moderate"
        assert rdr.source_bulletin_id == b_morning.pk

    def test_prior_evening_fallback_when_no_morning(self) -> None:
        """
        When only prior-evening-of-(X-1) exists (no morning-of-X), it is used
        as the single chosen bulletin for day X.
        """
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 25)

        # Prior-evening (Mar 24 16:00) → target Mar 25, dry=2 + wet=3
        vf_eve = datetime.datetime(2026, 3, 24, 16, 0, tzinfo=UTC)
        vt_eve = datetime.datetime(2026, 3, 25, 17, 0, tzinfo=UTC)
        b_eve = _make_bulletin_for_region(
            region,
            vf_eve,
            vt_eve,
            traits=[_trait(2, "dry"), _trait(3, "wet")],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "moderate"
        assert rdr.max_rating == "considerable"
        assert rdr.source_bulletin_id == b_eve.pk

    def test_evening_of_x_is_not_included_in_day_x(self) -> None:
        """
        When only an evening-of-X bulletin exists, its target is X+1, so day X
        has no candidate and must produce no_rating.
        """
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 2, 10)

        # Evening-of-Feb-10 → target is Feb 11, not Feb 10.
        vf = datetime.datetime(2026, 2, 10, 16, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 2, 11, 17, 0, tzinfo=UTC)
        _make_bulletin_for_region(
            region, vf, vt, traits=[_trait(3, "dry")], headline_key="considerable"
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.source_bulletin is None

    def test_version_zero_bulletin_excluded(self) -> None:
        """Bulletins with render_model_version=0 are excluded; both ratings are no_rating."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(3, "dry")],
            headline_key="considerable",
            render_model_version=0,
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.source_bulletin is None

    def test_trait_with_invalid_danger_level_skipped(self) -> None:
        """A trait with a missing/invalid danger_level is skipped; valid traits still count."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        bad_trait = {"category": "dry", "danger_level": None, "time_period": "all_day"}
        good_trait = _trait(2, "dry")
        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[bad_trait, good_trait],
            headline_key="moderate",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Bad trait is skipped; good trait at level 2 sets both min and max.
        assert rdr.min_rating == "moderate"
        assert rdr.max_rating == "moderate"

    def test_trait_with_out_of_range_danger_level_skipped(self) -> None:
        """A trait with danger_level=99 (out of range) is skipped without crashing."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[{"category": "dry", "danger_level": 99, "time_period": "all_day"}],
            headline_key="low",
        )

        recompute_region_day(region, day, commit=True)

        # All traits invalid and no fallback (traits list is non-empty) →
        # the bulletin contributes nothing → no_rating.
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING

    def test_empty_render_model_falls_back_to_low(self) -> None:
        """
        A bulletin whose render_model is an empty dict has no traits and no
        danger key, so _extract_headline_from_render_model defaults to "low"
        and the result is min=max=low (quiet-day fallback path).
        """
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        # Build a bulletin with a completely empty render_model.
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model={},
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=region.name,
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Empty render_model → traits=[] → fallback to danger.key → defaults to "low".
        assert rdr.min_rating == "low"
        assert rdr.max_rating == "low"

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
        _make_bulletin_for_region(
            region, vf, vt, traits=[_trait(2, "dry")], headline_key="moderate"
        )

        recompute_region_day(region, day, commit=False)

        assert not RegionDayRating.objects.filter(region=region, date=day).exists()

    def test_source_bulletin_is_chosen_bulletin(self) -> None:
        """source_bulletin is always the single chosen bulletin."""
        region = RegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 15)
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        b = _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(3, "dry")],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source_bulletin_id == b.pk


# ---------------------------------------------------------------------------
# apply_bulletin_day_ratings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyBulletinDayRatings:
    """Tests for apply_bulletin_day_ratings()."""

    def test_morning_bulletin_creates_one_row_for_target_day(self) -> None:
        """A morning bulletin (valid_from.hour < 12) creates exactly one row for its date."""
        region = RegionFactory.create(region_id="CH-4115")
        # Morning issue: target day = Jan 15
        vf = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        bulletin = _make_bulletin_for_region(
            region, vf, vt, traits=[_trait(1, "dry")], headline_key="low"
        )

        apply_bulletin_day_ratings(bulletin)

        # Exactly one row for Jan 15.
        assert RegionDayRating.objects.filter(
            region=region, date=datetime.date(2026, 1, 15)
        ).exists()
        # No row created for any other date.
        assert RegionDayRating.objects.filter(region=region).count() == 1

    def test_evening_bulletin_creates_one_row_for_next_day(self) -> None:
        """An evening bulletin (valid_from.hour >= 12) creates exactly one row for day+1."""
        region = RegionFactory.create(region_id="CH-4115")
        # Evening issue: valid_from Jan 14 17:00 → target day = Jan 15
        vf = datetime.datetime(2026, 1, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)

        bulletin = _make_bulletin_for_region(
            region, vf, vt, traits=[_trait(2, "dry")], headline_key="moderate"
        )

        apply_bulletin_day_ratings(bulletin)

        # Row for Jan 15 (the target day), not Jan 14.
        assert RegionDayRating.objects.filter(
            region=region, date=datetime.date(2026, 1, 15)
        ).exists()
        assert not RegionDayRating.objects.filter(
            region=region, date=datetime.date(2026, 1, 14)
        ).exists()

    def test_applies_to_all_linked_regions(self) -> None:
        """apply_bulletin_day_ratings touches every region linked to the bulletin."""
        region_a = RegionFactory.create(region_id="CH-1111")
        region_b = RegionFactory.create(region_id="CH-2222")
        # Morning issue → target Feb 10
        vf = datetime.datetime(2026, 2, 10, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 2, 10, 17, 0, tzinfo=UTC)
        render_model = {
            "version": RENDER_MODEL_VERSION,
            "danger": {"key": "moderate", "subdivision": None, "number": 2},
            "traits": [_trait(2, "dry")],
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
