"""
tests/bulletins/services/test_day_rating.py — Tests for the day_rating service.

Covers (v8 elevation-band split + AM/PM split + ALBINA bands policy):
  - _target_day: morning/evening/boundary rules.
  - Single bulletin, two traits (dry=1, wet=3) → min=max=headline_key (considerable).
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
  - _resolve_min_max_keys: split when afternoon > morning.
  - _resolve_min_max_keys: no split when afternoon == morning.
  - _resolve_min_max_keys: no split when no afternoon levels.
  - _resolve_min_max_keys: no split when no morning levels.
  - recompute_region_day: afternoon-elevated split produces min<max.
  - recompute_region_day: equal afternoon/morning stays headline-only.
  - recompute_region_day: later-only traits (no morning) stay headline-only.

SNOW-252 — peak semantics:
  - max_rating is the day's peak across all validTimePeriods (morning + afternoon).
  - Two-period escalating day (morning=2, afternoon=3) → max_rating=3 (considerable).
  - max_rating is NEVER the morning rating alone when afternoon is higher.

SNOW-293 — elevation-band split (Météo-France):
  - Two all_day ratings, different keys → min = lower band, max = upper band.
  - Two all_day ratings, same key → headline-only (no spurious split).
  - Elevation split present AND afternoon-elevated traits → elevation split wins.
  - No danger.ratings in render_model → existing fallback preserved.
  - Empty traits with band split → still picks up the split.
"""

from __future__ import annotations

import datetime
from datetime import UTC
from unittest.mock import patch

import pytest

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.day_rating import (
    DAY_RATING_VERSION,
    _derive_albina_bands,
    _detect_elevation_band_split,
    _elevation_to_band_id,
    _resolve_am_pm_keys,
    _resolve_min_max_keys,
    _target_day,
    apply_bulletin_day_ratings,
    recompute_region_day,
)
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.regions.models import MicroRegion
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bulletin_for_region(
    region: MicroRegion,
    valid_from: datetime.datetime,
    valid_to: datetime.datetime,
    traits: list | None = None,
    headline_key: str = "low",
    headline_subdivision: str | None = None,
    render_model_version: int | None = None,
    source: str = "slf",
    danger_ratings: list | None = None,
) -> Bulletin:
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
        source: Bulletin source string ("slf", "albina", "meteofrance").
        danger_ratings: Optional list of per-rating dicts for
                        render_model["danger"]["ratings"]. Defaults to empty list.

    Returns:
        The created Bulletin.

    """
    if render_model_version is None:
        render_model_version = RENDER_MODEL_VERSION
    if traits is None:
        traits = []
    if danger_ratings is None:
        danger_ratings = []

    render_model = {
        "version": render_model_version,
        "source": source,
        "danger": {
            "key": headline_key,
            "subdivision": headline_subdivision,
            "number": 1,
            "ratings": danger_ratings or [],
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


def _split_trait(danger_level: int, time_period: str, category: str = "dry") -> dict:
    """
    Build a trait dict with an explicit time_period for split-logic tests.

    Args:
        danger_level: Integer 1–5.
        time_period: "all_day", "earlier", or "later".
        category: "dry" or "wet".

    Returns:
        A trait dict shaped for recompute_region_day.

    """
    return {
        "category": category,
        "danger_level": danger_level,
        "time_period": time_period,
        "title": f"{category.capitalize()} avalanches",
        "problems": [],
    }


# ---------------------------------------------------------------------------
# _resolve_min_max_keys (unit — no DB)
# ---------------------------------------------------------------------------


class TestResolveMinMaxKeys:
    """Unit tests for _resolve_min_max_keys() — no DB required."""

    def test_split_when_afternoon_strictly_higher(self) -> None:
        """Afternoon max > morning max → split: min=morning key, max=afternoon key."""
        min_key, min_sub, max_key, max_sub = _resolve_min_max_keys(
            morning_levels=[2],
            afternoon_levels=[3],
            headline_key="considerable",
            headline_subdivision="+",
        )
        assert min_key == "moderate"
        assert max_key == "considerable"
        # Both subdivisions mirror the headline.
        assert min_sub == "+"
        assert max_sub == "+"

    def test_no_split_when_afternoon_equals_morning(self) -> None:
        """Afternoon max == morning max → fall back to headline-only."""
        min_key, min_sub, max_key, max_sub = _resolve_min_max_keys(
            morning_levels=[3],
            afternoon_levels=[3],
            headline_key="considerable",
            headline_subdivision="",
        )
        assert min_key == "considerable"
        assert max_key == "considerable"

    def test_no_split_when_afternoon_lower_than_morning(self) -> None:
        """Afternoon max < morning max → fall back to headline-only."""
        min_key, min_sub, max_key, max_sub = _resolve_min_max_keys(
            morning_levels=[4],
            afternoon_levels=[2],
            headline_key="high",
            headline_subdivision="",
        )
        assert min_key == "high"
        assert max_key == "high"

    def test_no_split_when_no_afternoon_levels(self) -> None:
        """Empty afternoon_levels → fall back to headline-only."""
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[3],
            afternoon_levels=[],
            headline_key="considerable",
            headline_subdivision="",
        )
        assert min_key == "considerable"
        assert max_key == "considerable"

    def test_no_split_when_no_morning_levels(self) -> None:
        """Empty morning_levels → fall back to headline-only (cannot split)."""
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[],
            afternoon_levels=[4],
            headline_key="high",
            headline_subdivision="",
        )
        assert min_key == "high"
        assert max_key == "high"

    def test_split_uses_highest_from_each_bucket(self) -> None:
        """Multiple levels in each bucket — uses max(), not first/last."""
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[1, 2, 3],
            afternoon_levels=[4, 5],
            headline_key="very_high",
            headline_subdivision="",
        )
        assert min_key == "considerable"  # max of [1,2,3] = 3
        assert max_key == "very_high"  # max of [4,5] = 5


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

    def test_single_bulletin_two_traits_uses_headline_key(self) -> None:
        """Single bulletin with dry=1 and wet=3 → min=max=headline_key (considerable)."""
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        assert rdr.min_rating == "considerable"
        assert rdr.max_rating == "considerable"
        assert rdr.version == DAY_RATING_VERSION

    def test_single_bulletin_single_trait_stable(self) -> None:
        """Single bulletin with one trait (dry=3) → min=max=considerable (stable)."""
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        assert rdr.min_rating == "considerable"
        assert rdr.max_rating == "considerable"
        assert rdr.source_bulletin_id == b_eve.pk

    def test_evening_of_x_is_not_included_in_day_x(self) -> None:
        """
        When only an evening-of-X bulletin exists, its target is X+1, so day X
        has no candidate and must produce no_rating.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 1, 20)

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.NO_RATING
        assert rdr.min_rating == RegionDayRating.Rating.NO_RATING

    def test_dry_run_does_not_write(self) -> None:
        """commit=False logs but does not create a RegionDayRating row."""
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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

    def test_split_when_afternoon_elevated(self) -> None:
        """
        Bulletin with all_day=moderate (2) and later=considerable (3) →
        min_rating=moderate, max_rating=considerable.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 10)
        vf = datetime.datetime(2026, 3, 9, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 10, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "all_day", "dry"),
                _split_trait(3, "later", "wet"),
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "moderate"
        assert rdr.max_rating == "considerable"

    def test_no_split_when_afternoon_same_as_morning(self) -> None:
        """
        Bulletin with all_day=considerable (3) and later=considerable (3) →
        both ratings stay at the headline key (considerable).
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 11)
        vf = datetime.datetime(2026, 3, 10, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 11, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(3, "all_day", "dry"),
                _split_trait(3, "later", "wet"),
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "considerable"
        assert rdr.max_rating == "considerable"

    def test_no_split_when_only_later_traits(self) -> None:
        """
        Bulletin with only later traits (no all_day/earlier) → no morning_levels →
        fall back to headline-only.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 12)
        vf = datetime.datetime(2026, 3, 11, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 12, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(4, "later", "dry"),
            ],
            headline_key="high",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "high"
        assert rdr.max_rating == "high"

    def test_split_with_earlier_time_period(self) -> None:
        """
        ``earlier`` time_period is treated the same as ``all_day`` for morning_levels.
        Bulletin with earlier=low (1) and later=high (4) → min=low, max=high.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 13)
        vf = datetime.datetime(2026, 3, 12, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 13, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(1, "earlier", "dry"),
                _split_trait(4, "later", "dry"),
            ],
            headline_key="high",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == "low"
        assert rdr.max_rating == "high"


# ---------------------------------------------------------------------------
# apply_bulletin_day_ratings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestApplyBulletinDayRatings:
    """Tests for apply_bulletin_day_ratings()."""

    def test_morning_bulletin_creates_one_row_for_target_day(self) -> None:
        """A morning bulletin (valid_from.hour < 12) creates exactly one row for its date."""
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region = MicroRegionFactory.create(region_id="CH-4115")
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
        region_a = MicroRegionFactory.create(region_id="CH-1111")
        region_b = MicroRegionFactory.create(region_id="CH-2222")
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

    def test_recompute_failure_invalidates_stale_rating_and_counts(self) -> None:
        """SNOW-461: a failed recompute deletes the stale row and is counted.

        A bulletin that flips a region from low to high, but whose recompute
        raises, must not leave the old low rating live. The row is invalidated
        (absence renders as ``no_rating`` downstream) and the failure counted
        so the caller can mark the run failed.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        target = datetime.date(2026, 1, 15)
        RegionDayRatingFactory.create(
            region=region,
            date=target,
            min_rating="low",
            max_rating="low",
        )
        vf = datetime.datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 1, 15, 17, 0, tzinfo=UTC)
        bulletin = _make_bulletin_for_region(
            region, vf, vt, traits=[_trait(4, "dry")], headline_key="high"
        )

        with patch(
            "apps.bulletins.services.day_rating.recompute_region_day",
            side_effect=RuntimeError("boom"),
        ):
            failures = apply_bulletin_day_ratings(bulletin)

        assert failures == 1
        # Stale low rating invalidated — the public API can no longer serve it.
        assert not RegionDayRating.objects.filter(region=region, date=target).exists()

    def test_partial_failure_invalidates_only_the_failed_region(self) -> None:
        """SNOW-461: one region's failure neither stops nor invalidates others."""
        region_ok = MicroRegionFactory.create(region_id="CH-1111")
        region_bad = MicroRegionFactory.create(region_id="CH-2222")
        target = datetime.date(2026, 2, 10)
        RegionDayRatingFactory.create(
            region=region_ok, date=target, min_rating="low", max_rating="low"
        )
        RegionDayRatingFactory.create(
            region=region_bad, date=target, min_rating="low", max_rating="low"
        )
        vf = datetime.datetime(2026, 2, 10, 8, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 2, 10, 17, 0, tzinfo=UTC)
        # Link both regions to one high-danger bulletin.
        bulletin = _make_bulletin_for_region(
            region_ok, vf, vt, traits=[_trait(4, "dry")], headline_key="high"
        )
        RegionBulletinFactory.create(
            bulletin=bulletin, region=region_bad, region_name_at_time=region_bad.name
        )

        real_recompute = recompute_region_day

        def _fail_only_bad_region(
            region: MicroRegion, day: datetime.date, *, commit: bool = True
        ) -> None:
            """Raise for region_bad, run the real recompute for the rest."""
            if region.region_id == "CH-2222":
                raise RuntimeError("boom")
            real_recompute(region, day, commit=commit)

        with patch(
            "apps.bulletins.services.day_rating.recompute_region_day",
            side_effect=_fail_only_bad_region,
        ):
            failures = apply_bulletin_day_ratings(bulletin)

        assert failures == 1
        # Failed region invalidated; healthy region recomputed to the new rating.
        assert not RegionDayRating.objects.filter(
            region=region_bad, date=target
        ).exists()
        ok_row = RegionDayRating.objects.get(region=region_ok, date=target)
        assert ok_row.max_rating == "high"


# ---------------------------------------------------------------------------
# Exception swallowing inside upsert_bulletin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertBulletinSwallowsException:
    """apply_bulletin_day_ratings exceptions inside upsert_bulletin are swallowed."""

    def test_exception_is_caught_not_propagated(self) -> None:
        """When apply_bulletin_day_ratings raises, upsert_bulletin still returns."""
        from apps.bulletins.services.slf_fetcher import upsert_bulletin

        run = PipelineRunFactory.create()
        # Seed the region first — regions are fixture-backed (no auto-create).
        MicroRegionFactory.create(region_id="CH-9999", name="Test Region")

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
            "apps.bulletins.services.slf_fetcher.apply_bulletin_day_ratings",
            side_effect=RuntimeError("test explosion"),
        ):
            result = upsert_bulletin(raw, run)

        # upsert_bulletin should still return despite the exception (not re-raise).
        assert result is True  # created = True


# ---------------------------------------------------------------------------
# SNOW-252 — peak semantics regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPeakSemantics:
    """
    Regression tests asserting that max_rating is always the day's peak
    across all validTimePeriods (morning + afternoon + elevation bands).

    The two-period escalating fixture (morning=2, afternoon=3 → peak=3) is
    the canonical proof shape. See docs/compressed-views-rating-rule.md.
    """

    def test_two_period_escalating_day_max_rating_is_peak(self) -> None:
        """
        Two-period escalating day: morning=2 (moderate), afternoon=3 (considerable).

        max_rating must be 3 (considerable — the afternoon peak).
        min_rating must be 2 (moderate — the morning level).

        This is the canonical SNOW-252 regression fixture.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        day = datetime.date(2026, 3, 20)
        vf = datetime.datetime(2026, 3, 19, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 20, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "all_day", "dry"),  # morning: moderate
                _split_trait(3, "later", "wet"),  # afternoon: considerable
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Peak is the afternoon level — considerable.
        assert rdr.max_rating == RegionDayRating.Rating.CONSIDERABLE
        # Morning level is preserved in min_rating.
        assert rdr.min_rating == RegionDayRating.Rating.MODERATE

    def test_max_rating_is_never_morning_alone_when_afternoon_higher(self) -> None:
        """
        Regression: max_rating must not be capped at the morning level when the
        afternoon is higher. This test would have caught any implementation that
        took only all_day/earlier traits and ignored 'later' traits for max_rating.
        """
        region = MicroRegionFactory.create(region_id="CH-4116")
        day = datetime.date(2026, 3, 21)
        vf = datetime.datetime(2026, 3, 20, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 21, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "earlier", "dry"),  # morning: moderate (2)
                _split_trait(4, "later", "dry"),  # afternoon: high (4) — peak
            ],
            headline_key="high",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # max_rating is the afternoon peak, NOT the morning level.
        assert rdr.max_rating == RegionDayRating.Rating.HIGH
        # A morning-only implementation would have returned moderate here.
        assert rdr.max_rating != RegionDayRating.Rating.MODERATE

    def test_uniform_day_max_rating_equals_single_level(self) -> None:
        """
        When all traits share the same time period (all_day) there is no split;
        max_rating equals the headline danger key.
        """
        region = MicroRegionFactory.create(region_id="CH-4117")
        day = datetime.date(2026, 3, 22)
        vf = datetime.datetime(2026, 3, 21, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 22, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(3, "all_day", "dry"),
                _split_trait(3, "all_day", "wet"),
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.max_rating == RegionDayRating.Rating.CONSIDERABLE
        assert rdr.min_rating == RegionDayRating.Rating.CONSIDERABLE


# ---------------------------------------------------------------------------
# Version 7 — source and bands fields
# ---------------------------------------------------------------------------


class TestElevationToBandId:
    """Unit tests for _elevation_to_band_id (no DB)."""

    def test_none_returns_all_elevations(self) -> None:
        """None elevation returns 'all-elevations'."""
        assert _elevation_to_band_id(None) == "all-elevations"

    def test_treeline_lower(self) -> None:
        """treeline_side='lower' returns 'above-treeline'."""
        elev = {
            "treeline": True,
            "treeline_side": "lower",
            "lower": None,
            "upper": None,
        }
        assert _elevation_to_band_id(elev) == "above-treeline"

    def test_treeline_upper(self) -> None:
        """treeline_side='upper' returns 'below-treeline'."""
        elev = {
            "treeline": True,
            "treeline_side": "upper",
            "lower": None,
            "upper": None,
        }
        assert _elevation_to_band_id(elev) == "below-treeline"

    def test_numeric_lower_only(self) -> None:
        """Numeric lower only returns 'above-{lower}'."""
        elev = {"treeline": False, "treeline_side": None, "lower": 2200, "upper": None}
        assert _elevation_to_band_id(elev) == "above-2200"

    def test_numeric_upper_only(self) -> None:
        """Numeric upper only returns 'below-{upper}'."""
        elev = {"treeline": False, "treeline_side": None, "lower": None, "upper": 2200}
        assert _elevation_to_band_id(elev) == "below-2200"


class TestDeriveAlbinaBands:
    """Unit tests for _derive_albina_bands (no DB)."""

    def test_no_ratings_returns_none(self) -> None:
        """Render model with empty ratings list returns None."""
        rm: dict = {"danger": {"ratings": []}}
        result = _derive_albina_bands(rm)
        assert result == [] or result is None

    def test_elevation_split_produces_two_bands(self) -> None:
        """Two ratings with different elevations produce two band entries."""
        rm: dict = {
            "danger": {
                "ratings": [
                    {
                        "period": "all_day",
                        "key": "considerable",
                        "elevation": {
                            "lower": 2200,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "all_day",
                        "key": "low",
                        "elevation": {
                            "lower": None,
                            "upper": 2200,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                ]
            }
        }
        bands = _derive_albina_bands(rm)
        assert bands is not None
        assert len(bands) == 2
        band_ids = {b["band_id"] for b in bands}
        assert "above-2200" in band_ids
        assert "below-2200" in band_ids

    def test_band_entry_has_required_keys(self) -> None:
        """Each band entry has band_id, label, rating_key, and time_period."""
        rm: dict = {
            "danger": {
                "ratings": [
                    {
                        "period": "all_day",
                        "key": "moderate",
                        "elevation": {
                            "lower": 2200,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                ]
            }
        }
        bands = _derive_albina_bands(rm)
        assert bands
        band = bands[0]
        assert "band_id" in band
        assert "label" in band
        assert "rating_key" in band
        assert "time_period" in band
        assert band["rating_key"] == "moderate"
        assert band["time_period"] == "all_day"

    def test_two_by_two_bands_sorted_canonical_order(self) -> None:
        """2×2 ratings arrive in scrambled order and come out in canonical order.

        Canonical order: earlier-high, earlier-low, later-high, later-low.
        The CSS grid depends on this; incorrect order silently swaps quadrants.
        This fixture intentionally lists ratings in the wrong order to confirm
        the sort is applied rather than relying on source-data ordering.
        """
        rm: dict = {
            "danger": {
                "ratings": [
                    # Deliberately scrambled: later first, low band first.
                    {
                        "period": "later",
                        "key": "moderate",
                        "elevation": {
                            "lower": None,
                            "upper": 2800,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "later",
                        "key": "high",
                        "elevation": {
                            "lower": 2800,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "earlier",
                        "key": "low",
                        "elevation": {
                            "lower": None,
                            "upper": 2500,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                    {
                        "period": "earlier",
                        "key": "considerable",
                        "elevation": {
                            "lower": 2500,
                            "upper": None,
                            "treeline": False,
                            "treeline_side": None,
                        },
                    },
                ]
            }
        }
        bands = _derive_albina_bands(rm)
        assert bands is not None
        assert len(bands) == 4
        # Canonical order: earlier-high, earlier-low, later-high, later-low.
        assert bands[0]["band_id"] == "above-2500"
        assert bands[0]["time_period"] == "earlier"
        assert bands[1]["band_id"] == "below-2500"
        assert bands[1]["time_period"] == "earlier"
        assert bands[2]["band_id"] == "above-2800"
        assert bands[2]["time_period"] == "later"
        assert bands[3]["band_id"] == "below-2800"
        assert bands[3]["time_period"] == "later"


@pytest.mark.django_db
class TestRecomputeRegionDaySourceAndBands:
    """Tests for the source and bands fields added in v7."""

    def test_slf_bulletin_sets_source(self) -> None:
        """SLF bulletin populates source='slf' on the RegionDayRating row."""
        region = MicroRegionFactory.create(region_id="CH-4120")
        day = datetime.date(2026, 4, 10)
        vf = datetime.datetime(2026, 4, 10, 7, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 11, 0, 0, tzinfo=UTC)
        _make_bulletin_for_region(region, vf, vt, source="slf")
        recompute_region_day(region, day, commit=True)
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source == "slf"

    def test_slf_bulletin_has_null_bands(self) -> None:
        """SLF bulletin leaves bands=None on the RegionDayRating row."""
        region = MicroRegionFactory.create(region_id="CH-4121")
        day = datetime.date(2026, 4, 11)
        vf = datetime.datetime(2026, 4, 11, 7, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 12, 0, 0, tzinfo=UTC)
        _make_bulletin_for_region(region, vf, vt, source="slf")
        recompute_region_day(region, day, commit=True)
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.bands is None

    def test_albina_bulletin_sets_source(self) -> None:
        """ALBINA bulletin populates source='albina' on the RegionDayRating row."""
        region = MicroRegionFactory.create(region_id="AT-4122")
        day = datetime.date(2026, 4, 12)
        vf = datetime.datetime(2026, 4, 12, 7, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 13, 0, 0, tzinfo=UTC)
        _make_bulletin_for_region(
            region,
            vf,
            vt,
            source=Bulletin.Source.ALBINA,
            danger_ratings=[
                {
                    "period": "all_day",
                    "key": "considerable",
                    "elevation": {
                        "lower": 2200,
                        "upper": None,
                        "treeline": False,
                        "treeline_side": None,
                    },
                },
                {
                    "period": "all_day",
                    "key": "low",
                    "elevation": {
                        "lower": None,
                        "upper": 2200,
                        "treeline": False,
                        "treeline_side": None,
                    },
                },
            ],
        )
        recompute_region_day(region, day, commit=True)
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source == Bulletin.Source.ALBINA

    def test_albina_bulletin_sets_bands(self) -> None:
        """ALBINA bulletin with elevation split populates bands on the row."""
        region = MicroRegionFactory.create(region_id="AT-4123")
        day = datetime.date(2026, 4, 13)
        vf = datetime.datetime(2026, 4, 13, 7, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 14, 0, 0, tzinfo=UTC)
        _make_bulletin_for_region(
            region,
            vf,
            vt,
            source=Bulletin.Source.ALBINA,
            danger_ratings=[
                {
                    "period": "all_day",
                    "key": "considerable",
                    "elevation": {
                        "lower": 2200,
                        "upper": None,
                        "treeline": False,
                        "treeline_side": None,
                    },
                },
                {
                    "period": "all_day",
                    "key": "low",
                    "elevation": {
                        "lower": None,
                        "upper": 2200,
                        "treeline": False,
                        "treeline_side": None,
                    },
                },
            ],
        )
        recompute_region_day(region, day, commit=True)
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.bands is not None
        assert len(rdr.bands) == 2
        band_ids = {b["band_id"] for b in rdr.bands}
        assert "above-2200" in band_ids
        assert "below-2200" in band_ids

    def test_no_candidate_sets_blank_source(self) -> None:
        """When no qualifying bulletin exists, source is blank string."""
        region = MicroRegionFactory.create(region_id="CH-4124")
        day = datetime.date(2026, 4, 14)
        recompute_region_day(region, day, commit=True)
        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.source == ""
        assert rdr.bands is None


# ---------------------------------------------------------------------------
# SNOW-291 — _resolve_am_pm_keys (unit — no DB)
# ---------------------------------------------------------------------------


class TestResolveAmPmKeys:
    """Unit tests for _resolve_am_pm_keys() — no DB required."""

    def test_populated_when_both_periods_present(self) -> None:
        """Both morning and afternoon levels → am/pm keys are non-None."""
        am, am_sub, pm, pm_sub = _resolve_am_pm_keys(
            morning_levels=[2],
            afternoon_levels=[3],
            headline_subdivision="+",
        )
        assert am == "moderate"
        assert pm == "considerable"
        # Both subdivisions mirror headline.
        assert am_sub == "+"
        assert pm_sub == "+"

    def test_both_none_when_no_afternoon(self) -> None:
        """No afternoon levels → am and pm are both None."""
        am, am_sub, pm, pm_sub = _resolve_am_pm_keys(
            morning_levels=[3],
            afternoon_levels=[],
            headline_subdivision="",
        )
        assert am is None
        assert pm is None
        assert am_sub == ""
        assert pm_sub == ""

    def test_both_none_when_no_morning(self) -> None:
        """No morning levels → am and pm are both None."""
        am, am_sub, pm, pm_sub = _resolve_am_pm_keys(
            morning_levels=[],
            afternoon_levels=[3],
            headline_subdivision="",
        )
        assert am is None
        assert pm is None

    def test_flat_split_produces_equal_am_pm(self) -> None:
        """Same level morning and afternoon → am == pm (flat-but-split support)."""
        am, _, pm, _ = _resolve_am_pm_keys(
            morning_levels=[2],
            afternoon_levels=[2],
            headline_subdivision="",
        )
        assert am == "moderate"
        assert pm == "moderate"

    def test_uses_max_of_each_bucket(self) -> None:
        """Multiple levels in each bucket — uses max(), not first."""
        am, _, pm, _ = _resolve_am_pm_keys(
            morning_levels=[1, 2],
            afternoon_levels=[3, 4],
            headline_subdivision="",
        )
        assert am == "moderate"  # max of [1, 2] = 2
        assert pm == "high"  # max of [3, 4] = 4


# ---------------------------------------------------------------------------
# SNOW-291 — AM/PM persistence in recompute_region_day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAmPmPersistence:
    """Tests for AM/PM field population in recompute_region_day (SNOW-291)."""

    def test_flat_but_split_populates_both_am_pm_equal(self) -> None:
        """
        Flat-but-split day: dry all_day=moderate (2) + wet later=moderate (2).

        am_rating == pm_rating == moderate (equal levels, different problem mix).
        Both must be non-null — the key SNOW-291 case.
        """
        region = MicroRegionFactory.create(region_id="CH-4118")
        day = datetime.date(2026, 4, 10)
        vf = datetime.datetime(2026, 4, 9, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 10, 17, 0, tzinfo=UTC)
        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "all_day", "dry"),
                _split_trait(2, "later", "wet"),
            ],
            headline_key="moderate",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Both AM and PM must be non-null on a flat-but-split day.
        assert rdr.am_rating == RegionDayRating.Rating.MODERATE
        assert rdr.pm_rating == RegionDayRating.Rating.MODERATE
        # min/max stay headline-only (afternoon not strictly higher).
        assert rdr.min_rating == RegionDayRating.Rating.MODERATE
        assert rdr.max_rating == RegionDayRating.Rating.MODERATE

    def test_escalating_populates_am_lower_pm_higher(self) -> None:
        """
        Escalating day: all_day=moderate (2) + later=considerable (3).

        am_rating=moderate, pm_rating=considerable.
        """
        region = MicroRegionFactory.create(region_id="CH-4119")
        day = datetime.date(2026, 4, 11)
        vf = datetime.datetime(2026, 4, 10, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 11, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "all_day", "dry"),
                _split_trait(3, "later", "wet"),
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.am_rating == RegionDayRating.Rating.MODERATE
        assert rdr.pm_rating == RegionDayRating.Rating.CONSIDERABLE

    def test_uniform_day_leaves_am_pm_null(self) -> None:
        """
        Uniform day: only all_day traits, no later period.

        am_rating and pm_rating must both stay null.
        """
        region = MicroRegionFactory.create(region_id="CH-4120")
        day = datetime.date(2026, 4, 12)
        vf = datetime.datetime(2026, 4, 11, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 4, 12, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(3, "all_day", "dry"),
                _split_trait(3, "all_day", "wet"),
            ],
            headline_key="considerable",
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.am_rating is None
        assert rdr.pm_rating is None
        assert rdr.am_subdivision == ""
        assert rdr.pm_subdivision == ""

    def test_no_bulletin_leaves_am_pm_null(self) -> None:
        """No qualifying bulletin → am_rating and pm_rating stay null."""
        region = MicroRegionFactory.create(region_id="CH-4121")
        day = datetime.date(2026, 4, 13)

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.am_rating is None
        assert rdr.pm_rating is None


# ---------------------------------------------------------------------------
# SNOW-293 — elevation-band split (Météo-France style)
# ---------------------------------------------------------------------------


def _mf_rating(
    key: str, period: str = "all_day", elevation: dict | None = None
) -> dict:
    """
    Build a single projected ``danger.ratings`` entry for MF-style tests.

    Args:
        key: Danger key string (e.g. "low", "moderate").
        period: Validity time period, defaults to "all_day".
        elevation: Optional projected elevation dict.

    Returns:
        A dict shaped like one entry in render_model["danger"]["ratings"].

    """
    return {
        "period": period,
        "key": key,
        "subdivision": None,
        "elevation": elevation,
    }


class TestDetectElevationBandSplit:
    """Unit tests for _detect_elevation_band_split() — no DB required."""

    def test_two_all_day_different_keys_returns_min_max(self) -> None:
        """Two all_day ratings with different keys → (lower_key, higher_key)."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("low"),
                _mf_rating("moderate"),
            ]
        )
        assert result == ("low", "moderate")

    def test_two_all_day_same_key_returns_none(self) -> None:
        """Two all_day ratings with the same key → no split (None)."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("considerable"),
                _mf_rating("considerable"),
            ]
        )
        assert result is None

    def test_single_all_day_returns_none(self) -> None:
        """Only one all_day entry → cannot split → None."""
        result = _detect_elevation_band_split([_mf_rating("moderate")])
        assert result is None

    def test_empty_list_returns_none(self) -> None:
        """Empty ratings list → None (no data to inspect)."""
        result = _detect_elevation_band_split([])
        assert result is None

    def test_non_all_day_entries_ignored(self) -> None:
        """Only 'later' entries — zero all_day entries → None."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("low", period="later"),
                _mf_rating("considerable", period="later"),
            ]
        )
        assert result is None

    def test_mixed_periods_all_day_wins(self) -> None:
        """Two all_day + one later with different keys → split detected from all_day pair."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("low"),
                _mf_rating("moderate"),
                _mf_rating("considerable", period="later"),
            ]
        )
        assert result == ("low", "moderate")

    def test_three_all_day_returns_min_max(self) -> None:
        """Three all_day ratings → min is the lowest, max is the highest."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("low"),
                _mf_rating("considerable"),
                _mf_rating("moderate"),
            ]
        )
        assert result == ("low", "considerable")

    def test_rank_order_used_not_insertion_order(self) -> None:
        """Min/max are determined by EAWS rank, not the order entries appear."""
        result = _detect_elevation_band_split(
            [
                _mf_rating("high"),
                _mf_rating("low"),
            ]
        )
        assert result == ("low", "high")


class TestResolveMinMaxKeysElevationBand:
    """Unit tests for _resolve_min_max_keys() with the new elevation-band path."""

    def test_elevation_split_takes_precedence_over_afternoon(self) -> None:
        """
        When rm_ratings carries a band split AND afternoon traits are elevated,
        the elevation-band split wins (elevation split has higher precedence).

        Elevation split: low below / moderate above.
        Afternoon-elevated: morning=2, afternoon=3 (considerable).
        Expected: min=low, max=moderate (elevation split wins).
        """
        rm_ratings = [_mf_rating("low"), _mf_rating("moderate")]
        min_key, min_sub, max_key, max_sub = _resolve_min_max_keys(
            morning_levels=[2],  # moderate
            afternoon_levels=[3],  # considerable — would win without elevation
            headline_key="considerable",
            headline_subdivision="",
            rm_ratings=rm_ratings,
        )
        assert min_key == "low"
        assert max_key == "moderate"

    def test_no_rm_ratings_falls_through_to_afternoon_split(self) -> None:
        """When rm_ratings is None, the afternoon-elevated split still works."""
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[2],
            afternoon_levels=[3],
            headline_key="considerable",
            headline_subdivision="",
            rm_ratings=None,
        )
        assert min_key == "moderate"
        assert max_key == "considerable"

    def test_empty_rm_ratings_falls_through_to_afternoon_split(self) -> None:
        """When rm_ratings is an empty list, the afternoon-elevated split still works."""
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[2],
            afternoon_levels=[3],
            headline_key="considerable",
            headline_subdivision="",
            rm_ratings=[],
        )
        assert min_key == "moderate"
        assert max_key == "considerable"

    def test_same_key_band_falls_through_to_headline(self) -> None:
        """Band ratings with identical keys → no elevation split → headline-only."""
        rm_ratings = [_mf_rating("moderate"), _mf_rating("moderate")]
        min_key, _, max_key, _ = _resolve_min_max_keys(
            morning_levels=[2],
            afternoon_levels=[],
            headline_key="moderate",
            headline_subdivision="",
            rm_ratings=rm_ratings,
        )
        assert min_key == "moderate"
        assert max_key == "moderate"

    def test_band_split_subdivision_mirrors_headline(self) -> None:
        """Both subdivision fields mirror the headline when the band split fires."""
        rm_ratings = [_mf_rating("low"), _mf_rating("considerable")]
        _, min_sub, _, max_sub = _resolve_min_max_keys(
            morning_levels=[3],
            afternoon_levels=[],
            headline_key="considerable",
            headline_subdivision="+",
            rm_ratings=rm_ratings,
        )
        assert min_sub == "+"
        assert max_sub == "+"


@pytest.mark.django_db
class TestMFElevationBandSplitIntegration:
    """
    Integration tests for the Météo-France elevation-band split through
    recompute_region_day() — verifies the end-to-end calendar tile path.
    """

    def test_two_all_day_different_keys_produces_split(self) -> None:
        """
        MF-style: two all_day danger.ratings with different keys (low below,
        moderate above) → min=low, max=moderate.

        This is the canonical SNOW-293 regression fixture: before the fix
        both min and max were set to the headline key (moderate), causing
        a solid calendar tile instead of the diagonal split.
        """
        region = MicroRegionFactory.create(region_id="FR-38-1")
        day = datetime.date(2026, 3, 15)
        vf = datetime.datetime(2026, 3, 14, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 15, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(2, "wet")],
            headline_key="moderate",
            danger_ratings=[
                _mf_rating(
                    "low", elevation={"lower": None, "upper": 2500, "treeline": False}
                ),
                _mf_rating(
                    "moderate",
                    elevation={"lower": 2500, "upper": None, "treeline": False},
                ),
            ],
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == RegionDayRating.Rating.LOW
        assert rdr.max_rating == RegionDayRating.Rating.MODERATE

    def test_two_all_day_same_key_stays_headline(self) -> None:
        """Two all_day ratings with the same key → headline-only (no spurious split)."""
        region = MicroRegionFactory.create(region_id="FR-38-2")
        day = datetime.date(2026, 3, 16)
        vf = datetime.datetime(2026, 3, 15, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 16, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[_trait(3, "dry")],
            headline_key="considerable",
            danger_ratings=[
                _mf_rating("considerable"),
                _mf_rating("considerable"),
            ],
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == RegionDayRating.Rating.CONSIDERABLE
        assert rdr.max_rating == RegionDayRating.Rating.CONSIDERABLE

    def test_elevation_split_wins_over_afternoon_elevated_traits(self) -> None:
        """
        Elevation split takes precedence over the afternoon-elevated split.
        Band split: low below / moderate above (from danger.ratings).
        Afternoon traits: all_day=moderate(2), later=considerable(3).
        Expected: elevation split wins → min=low, max=moderate.
        """
        region = MicroRegionFactory.create(region_id="FR-38-3")
        day = datetime.date(2026, 3, 17)
        vf = datetime.datetime(2026, 3, 16, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 17, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[
                _split_trait(2, "all_day", "wet"),
                _split_trait(3, "later", "wet"),
            ],
            headline_key="moderate",
            danger_ratings=[
                _mf_rating("low"),
                _mf_rating("moderate"),
            ],
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # Elevation split wins — NOT the afternoon-elevated split.
        assert rdr.min_rating == RegionDayRating.Rating.LOW
        assert rdr.max_rating == RegionDayRating.Rating.MODERATE

    def test_no_danger_ratings_field_falls_back_to_trait_split(self) -> None:
        """
        When danger.ratings is absent, the afternoon-elevated trait split
        still operates correctly.
        """
        region = MicroRegionFactory.create(region_id="FR-38-4")
        day = datetime.date(2026, 3, 18)
        vf = datetime.datetime(2026, 3, 17, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 18, 17, 0, tzinfo=UTC)

        # Build a bulletin without 'ratings' in the danger block.
        render_model: dict = {
            "version": RENDER_MODEL_VERSION,
            "danger": {
                "key": "considerable",
                "subdivision": None,
                "number": 3,
                # No 'ratings' key at all.
            },
            "traits": [
                _split_trait(2, "all_day", "dry"),
                _split_trait(3, "later", "wet"),
            ],
        }
        bulletin = BulletinFactory.create(
            issued_at=vf,
            valid_from=vf,
            valid_to=vt,
            render_model=render_model,
            render_model_version=RENDER_MODEL_VERSION,
        )
        RegionBulletinFactory.create(
            bulletin=bulletin, region=region, region_name_at_time=region.name
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        # No band split → afternoon-elevated trait split fires.
        assert rdr.min_rating == RegionDayRating.Rating.MODERATE
        assert rdr.max_rating == RegionDayRating.Rating.CONSIDERABLE

    def test_empty_traits_with_band_split_uses_split(self) -> None:
        """
        Quiet day (empty traits) with an elevation-band split in danger.ratings
        → the band split is still picked up from danger.ratings.
        """
        region = MicroRegionFactory.create(region_id="FR-38-5")
        day = datetime.date(2026, 3, 19)
        vf = datetime.datetime(2026, 3, 18, 17, 0, tzinfo=UTC)
        vt = datetime.datetime(2026, 3, 19, 17, 0, tzinfo=UTC)

        _make_bulletin_for_region(
            region,
            vf,
            vt,
            traits=[],
            headline_key="moderate",
            danger_ratings=[
                _mf_rating("low"),
                _mf_rating("moderate"),
            ],
        )

        recompute_region_day(region, day, commit=True)

        rdr = RegionDayRating.objects.get(region=region, date=day)
        assert rdr.min_rating == RegionDayRating.Rating.LOW
        assert rdr.max_rating == RegionDayRating.Rating.MODERATE
