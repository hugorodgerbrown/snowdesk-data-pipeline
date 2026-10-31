"""
tests/bulletins/services/test_apply.py — Tests for apps.bulletins.services.apply.

Moved from tests/bulletins/services/test_slf_fetcher.py when upsert_bulletin
left slf_fetcher (SNOW-1249). Covers:
  - _get_region: returns the seeded region, raises UnknownRegionError otherwise
  - upsert_bulletin: creation, update, region linking, render model, day
    ratings, the pdf_url round trip
  - the BulletinGrouping ingest hook (SNOW-323)
  - target_date from validTime.startTime (SNOW-560)
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import IntegrityError
from django.test import Client

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.apply import (
    NoResolvableRegionsError,
    UnknownRegionError,
    _get_region,
    apply_bulletin,
    apply_feed_item,
    copy_service_day_ratings,
    upsert_bulletin,
)
from apps.bulletins.services.day_rating import target_day_for_valid_from
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from apps.ingest.models import SourceBulletin, SourceDayRating
from apps.ingest.public import (
    NormalisedBulletin,
    parse_dt,
    slf_pdf_url,
    store_bulletin,
)
from apps.regions.models import MicroRegion
from tests.factories import (
    MajorRegionFactory,
    MicroRegionFactory,
    PipelineRunFactory,
    SubRegionFactory,
)


def _make_raw_bulletin(
    bulletin_id: str = "test-001",
    publication_time: str = "2025-03-15T08:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """
    Build a raw bulletin dict matching the SLF CAAML API shape.

    Args:
        bulletin_id: The bulletin identifier.
        publication_time: ISO-8601 publication timestamp.
        **overrides: Additional keys to merge into the bulletin dict.

    Returns:
        A dict matching the shape returned by the SLF CAAML API.

    """
    base: dict[str, Any] = {
        "bulletinID": bulletin_id,
        "publicationTime": publication_time,
        "validTime": {
            "startTime": "2025-03-15T17:00:00Z",
            "endTime": "2025-03-16T17:00:00Z",
        },
        "nextUpdate": "2025-03-16T08:00:00Z",
        "lang": "en",
        "unscheduled": False,
        "regions": [
            {"regionID": "CH-4115", "name": "Piz Buin"},
            {"regionID": "CH-7111", "name": "Engadin"},
        ],
        "dangerRatings": [],
        "avalancheProblems": [],
        "customData": {"CH": {}},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _get_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetRegion:
    """Tests for _get_region (fixture-backed lookup)."""

    def test_returns_seeded_region(self) -> None:
        """Returns the Region when it exists."""
        seeded = MicroRegionFactory.create(region_id="CH-4115", name="Martigny-Verbier")
        found = _get_region("CH-4115")
        assert found.pk == seeded.pk

    def test_raises_unknown_region_error(self) -> None:
        """Unseeded region_id raises UnknownRegionError."""
        with pytest.raises(UnknownRegionError) as exc_info:
            _get_region("CH-9999")
        assert "CH-9999" in str(exc_info.value)

    def test_unknown_region_error_chains_does_not_exist(self) -> None:
        """The underlying Region.DoesNotExist is chained as __cause__."""
        try:
            _get_region("CH-0000")
        except UnknownRegionError as exc:
            assert isinstance(exc.__cause__, MicroRegion.DoesNotExist)
        else:
            pytest.fail("UnknownRegionError was not raised")

    def test_is_read_only(self) -> None:
        """_get_region does not create any Region rows."""
        assert MicroRegion.objects.count() == 0
        with pytest.raises(UnknownRegionError):
            _get_region("CH-4115")
        assert MicroRegion.objects.count() == 0


# ---------------------------------------------------------------------------
# upsert_bulletin
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_test_regions(db: Any) -> None:
    """Seed the regions referenced by ``_make_raw_bulletin`` defaults.

    The standard test bulletin covers ``CH-4115`` and ``CH-7111``; other
    tests add ``CH-9999``. Regions are now fixture-backed (no
    auto-creation), so the test must pre-create them.
    """
    for rid in ("CH-4115", "CH-7111", "CH-9999"):
        MicroRegionFactory.create(region_id=rid, name=f"Test {rid}")


@pytest.mark.django_db
class TestUpsertBulletin:
    """Tests for upsert_bulletin."""

    pytestmark = pytest.mark.usefixtures("_seed_test_regions")

    def test_creates_bulletin(self) -> None:
        """Creates a new Bulletin and returns True."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        created = upsert_bulletin(raw, run)

        assert created is True
        assert Bulletin.objects.count() == 1

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert bulletin.lang == "en"
        assert bulletin.unscheduled is False
        assert bulletin.pipeline_run == run

    def test_render_model_is_populated(self) -> None:
        """upsert_bulletin populates render_model and render_model_version."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert isinstance(bulletin.render_model, dict)
        assert bulletin.render_model_version == RENDER_MODEL_VERSION
        assert "version" in bulletin.render_model

    def test_render_model_version_set(self) -> None:
        """render_model_version equals RENDER_MODEL_VERSION after upsert."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.render_model_version == RENDER_MODEL_VERSION

    def test_wraps_raw_data_in_geojson_feature(self) -> None:
        """Raw data is wrapped in a GeoJSON Feature envelope."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.raw_data["type"] == "Feature"
        assert bulletin.raw_data["geometry"] is None
        assert bulletin.raw_data["properties"]["bulletinID"] == "test-001"

    def test_creates_region_links(self) -> None:
        """Creates RegionBulletin rows for each seeded region covered."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        assert RegionBulletin.objects.count() == 2

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        region_ids = list(
            bulletin.regions.order_by("region_id").values_list("region_id", flat=True)
        )
        assert region_ids == ["CH-4115", "CH-7111"]

    def test_all_unknown_regions_raises_before_any_write(self) -> None:
        """SNOW-547: a bulletin that resolves no region at all fails loudly.

        This used to be the *expected* outcome — ``created is True`` with
        zero linked regions. On an update that silently erased the
        bulletin's entire coverage from the map while the pipeline run
        reported success, indistinguishable from a legitimately
        empty-``regions`` bulletin. It now raises before the transaction,
        leaving prior state untouched, mirroring
        ``test_malformed_region_leaves_existing_bulletin_unchanged``.
        """
        run = PipelineRunFactory.create()
        upsert_bulletin(_make_raw_bulletin(), run)
        original_issued_at = Bulletin.objects.get(bulletin_id="test-001").issued_at

        raw_bad = _make_raw_bulletin(
            publication_time="2025-03-15T12:00:00Z",
            regions=[{"regionID": "CH-XXXX", "name": "Nonexistent"}],
        )
        with pytest.raises(NoResolvableRegionsError):
            upsert_bulletin(raw_bad, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == original_issued_at
        region_ids = list(
            bulletin.regions.order_by("region_id").values_list("region_id", flat=True)
        )
        assert region_ids == ["CH-4115", "CH-7111"]

    def test_all_unknown_regions_on_a_new_bulletin_creates_nothing(self) -> None:
        """SNOW-547: the first ingest of an all-unknown bulletin writes no row."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(
            regions=[{"regionID": "CH-XXXX", "name": "Nonexistent"}],
        )

        with pytest.raises(NoResolvableRegionsError):
            upsert_bulletin(raw, run)

        assert not Bulletin.objects.filter(bulletin_id="test-001").exists()

    def test_unknown_region_warning_partially_linked(self) -> None:
        """When a bulletin has mixed known/unknown regions, only known are linked."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(
            regions=[
                {"regionID": "CH-4115", "name": "Piz Buin"},
                {"regionID": "CH-XXXX", "name": "Nonexistent"},
            ],
        )
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        linked_ids = list(bulletin.regions.values_list("region_id", flat=True))
        assert linked_ids == ["CH-4115"]

    def test_stores_region_name_at_time(self) -> None:
        """RegionBulletin records store the name from the bulletin."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        link = RegionBulletin.objects.get(region__region_id="CH-4115")
        assert link.region_name_at_time == "Piz Buin"

    def test_update_existing_bulletin(self) -> None:
        """Updating an existing bulletin returns False and refreshes data."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)

        # Update with a new publication time
        raw_updated = _make_raw_bulletin(
            publication_time="2025-03-15T12:00:00Z",
        )
        created = upsert_bulletin(raw_updated, run)

        assert created is False
        assert Bulletin.objects.count() == 1
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == datetime(2025, 3, 15, 12, 0, 0, tzinfo=UTC)

    def test_update_replaces_region_links(self) -> None:
        """Updating a bulletin clears and re-creates region links."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        upsert_bulletin(raw, run)
        assert RegionBulletin.objects.count() == 2

        # Update with only one region
        raw_updated = _make_raw_bulletin(
            regions=[{"regionID": "CH-9999", "name": "New Region"}],
        )
        upsert_bulletin(raw_updated, run)

        assert RegionBulletin.objects.count() == 1
        rb = RegionBulletin.objects.first()
        assert rb is not None
        assert rb.region.region_id == "CH-9999"

    def test_malformed_region_leaves_existing_bulletin_unchanged(self) -> None:
        """SNOW-460: a malformed region aborts before any DB write.

        A region dict missing ``name`` raises ``KeyError`` during the
        up-front resolution pass, before the bulletin row is touched — so a
        prior bulletin and its links are left exactly as they were, never
        half-rewritten.
        """
        run = PipelineRunFactory.create()
        upsert_bulletin(_make_raw_bulletin(), run)
        original_issued_at = Bulletin.objects.get(bulletin_id="test-001").issued_at

        # Re-ingest with a changed field and a malformed second region.
        raw_bad = _make_raw_bulletin(
            publication_time="2025-03-15T12:00:00Z",
            regions=[
                {"regionID": "CH-4115", "name": "Piz Buin"},
                {"regionID": "CH-7111"},  # missing "name" — malformed
            ],
        )
        with pytest.raises(KeyError):
            upsert_bulletin(raw_bad, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == original_issued_at
        region_ids = list(
            bulletin.regions.order_by("region_id").values_list("region_id", flat=True)
        )
        assert region_ids == ["CH-4115", "CH-7111"]

    def test_region_link_failure_rolls_back_bulletin_update(self) -> None:
        """SNOW-460: a failure inside the write loop rolls back the whole update.

        Even a failure that surfaces *during* the delete-and-recreate (not a
        malformed payload caught up front) must leave the prior bulletin and
        links intact — the replacement is wrapped in a single transaction.
        """
        run = PipelineRunFactory.create()
        upsert_bulletin(_make_raw_bulletin(), run)
        original_issued_at = Bulletin.objects.get(bulletin_id="test-001").issued_at

        raw_updated = _make_raw_bulletin(publication_time="2025-03-15T12:00:00Z")
        real_create = RegionBulletin.objects.create
        call_count = {"n": 0}

        def _fail_on_second_link(*args: Any, **kwargs: Any) -> Any:
            """Let the first link write succeed, fail the second."""
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise IntegrityError("simulated link write failure")
            return real_create(*args, **kwargs)

        with (
            patch.object(
                RegionBulletin.objects, "create", side_effect=_fail_on_second_link
            ),
            pytest.raises(IntegrityError),
        ):
            upsert_bulletin(raw_updated, run)

        # The bulletin update and the link delete/recreate all rolled back.
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.issued_at == original_issued_at
        region_ids = list(
            bulletin.regions.order_by("region_id").values_list("region_id", flat=True)
        )
        assert region_ids == ["CH-4115", "CH-7111"]

    def test_rating_failures_increment_records_failed(self) -> None:
        """SNOW-461: day-rating recompute failures are added to records_failed.

        A failed recompute invalidates the stale rating (no stale data served)
        but the run must still be marked failed so cron/CI surface it —
        upsert_bulletin adds the count reported by apply_bulletin_day_ratings.
        """
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        with patch(
            "apps.bulletins.services.apply.apply_bulletin_day_ratings",
            return_value=2,
        ):
            upsert_bulletin(raw, run)

        run.refresh_from_db()
        assert run.records_failed == 2

    def test_rating_exception_increments_records_failed(self) -> None:
        """SNOW-461: a wholesale day-rating failure still fails the run.

        Even if apply_bulletin_day_ratings raises outright, ingest continues
        (the bulletin persists) but records_failed is bumped by one so the run
        is marked failed.
        """
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        with patch(
            "apps.bulletins.services.apply.apply_bulletin_day_ratings",
            side_effect=RuntimeError("boom"),
        ):
            created = upsert_bulletin(raw, run)

        assert created is True
        run.refresh_from_db()
        assert run.records_failed == 1

    def test_handles_missing_next_update(self) -> None:
        """Bulletin without nextUpdate stores None."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        del raw["nextUpdate"]
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.next_update is None

    def test_handles_missing_publication_time(self) -> None:
        """Pre-2024 bulletins without publicationTime fall back to validTime.startTime."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()
        del raw["publicationTime"]
        created = upsert_bulletin(raw, run)

        assert created is True
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        # validTime.startTime in the helper default is 2025-03-15T17:00:00Z.
        assert bulletin.issued_at == datetime(2025, 3, 15, 17, 0, 0, tzinfo=UTC)
        assert bulletin.render_model_version == RENDER_MODEL_VERSION

    def test_legacy_2023_fixture_ingests_cleanly(self) -> None:
        """The real failing 2023 payload ingests without error after the fix."""
        fixture_path = Path("tests/fixtures/sample_legacy_no_publication_time.json")
        # Sample fixtures are stored GeoJSON-wrapped; the SLF API delivers
        # the bare ``properties`` payload that ``upsert_bulletin`` consumes.
        raw = json.loads(fixture_path.read_text())["properties"]

        # Seed the regions referenced by this real bulletin so _get_region
        # finds them. MicroRegionFactory generates a unique region_id per call by
        # default, so we override explicitly per region.
        for region in raw["regions"]:
            MicroRegionFactory.create(
                region_id=region["regionID"],
                name=region["name"],
            )

        run = PipelineRunFactory.create()
        created = upsert_bulletin(raw, run)

        assert created is True
        bulletin = Bulletin.objects.get(bulletin_id="52873-A")
        assert bulletin.issued_at == datetime(2023, 12, 13, 16, 0, 0, tzinfo=UTC)
        assert bulletin.render_model_version == RENDER_MODEL_VERSION
        # Both editorial groupings (dry/new_snow + wet/gliding_snow) should
        # have been built into traits.
        traits = bulletin.render_model["traits"]
        assert [t["category"] for t in traits] == ["dry", "wet"]
        # All 25 regions should be linked.
        assert bulletin.regions.count() == 25

    def test_handles_empty_regions(self) -> None:
        """Bulletin with no regions creates no RegionBulletin rows."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(regions=[])
        upsert_bulletin(raw, run)

        assert RegionBulletin.objects.count() == 0

    def test_render_model_fallback_on_build_error(self) -> None:
        """When build_render_model raises RenderModelBuildError, stores error sentinel."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        with patch(
            "apps.bulletins.services.apply.build_render_model",
            side_effect=RenderModelBuildError("boom"),
        ):
            upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.render_model_version == 0
        assert bulletin.render_model["version"] == 0
        assert bulletin.render_model["error"] == "boom"
        assert bulletin.render_model["error_type"] == "RenderModelBuildError"

    def test_render_model_error_increments_records_failed(self) -> None:
        """RenderModelBuildError increments run.records_failed."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        with patch(
            "apps.bulletins.services.apply.build_render_model",
            side_effect=RenderModelBuildError("boom"),
        ):
            upsert_bulletin(raw, run)

        run.refresh_from_db()
        assert run.records_failed == 1

    def test_non_render_model_exception_propagates(self) -> None:
        """Exceptions that are not RenderModelBuildError propagate uncaught."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        with patch(
            "apps.bulletins.services.apply.build_render_model",
            side_effect=ValueError("unexpected error"),
        ):
            with pytest.raises(ValueError, match="unexpected error"):
                upsert_bulletin(raw, run)

    def test_upsert_bulletin_creates_day_rating_rows(self) -> None:
        """upsert_bulletin creates RegionDayRating rows for each covered region."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(
            bulletin_id="dr-test-001",
            publication_time="2025-03-15T08:00:00Z",
        )
        upsert_bulletin(raw, run)

        # The bulletin's validTime startTime is 2025-03-15T17:00:00Z (hour=17,
        # evening issue) so its target day is 2025-03-16.  Under the v3
        # target-day rule, exactly one RegionDayRating row is created per region.

        assert RegionDayRating.objects.filter(
            region__region_id="CH-4115",
        ).exists()
        assert RegionDayRating.objects.filter(
            region__region_id="CH-7111",
        ).exists()

    def test_narrowed_correction_reconciles_the_dropped_region(self) -> None:
        """SNOW-1168: a re-ingest that drops a region leaves it no stale rating.

        The first issue rates CH-4115 and CH-7111 low. The correction, under
        the same bulletin ID, rates CH-7111 high and no longer covers
        CH-4115. Before the fix only CH-7111 was recomputed, so CH-4115 kept
        a low rating sourced from a bulletin that no longer covers it — and
        the map served it. It must now fall to ``no_rating`` (no other
        bulletin covers it that day) while CH-7111 reads high.
        """
        run = PipelineRunFactory.create()
        upsert_bulletin(
            _make_raw_bulletin(
                bulletin_id="corr-001",
                dangerRatings=[{"mainValue": "low"}],
            ),
            run,
        )
        target = date(2025, 3, 16)
        assert (
            RegionDayRating.objects.get(region__region_id="CH-4115", date=target)
        ).max_rating == RegionDayRating.Rating.LOW

        upsert_bulletin(
            _make_raw_bulletin(
                bulletin_id="corr-001",
                dangerRatings=[{"mainValue": "high"}],
                regions=[{"regionID": "CH-7111", "name": "Engadin"}],
            ),
            run,
        )

        dropped = RegionDayRating.objects.get(region__region_id="CH-4115", date=target)
        assert dropped.max_rating == RegionDayRating.Rating.NO_RATING
        assert dropped.source_bulletin is None
        kept = RegionDayRating.objects.get(region__region_id="CH-7111", date=target)
        assert kept.max_rating == RegionDayRating.Rating.HIGH

    def test_moved_day_correction_reconciles_the_old_day(self) -> None:
        """SNOW-1168: a re-ingest that moves the target day clears the old day.

        The first issue is an evening one (17:00Z on the 15th, so it forecasts
        the 16th). The correction, same bulletin ID, is a morning issue on the
        17th, so its target day moves to the 17th. The 16th
        must fall to ``no_rating`` (nothing else covers it) rather than keep
        the rating the bulletin no longer gives it, and the 17th is written.
        """
        run = PipelineRunFactory.create()
        upsert_bulletin(
            _make_raw_bulletin(
                bulletin_id="move-001",
                dangerRatings=[{"mainValue": "low"}],
            ),
            run,
        )
        old_day, new_day = date(2025, 3, 16), date(2025, 3, 17)
        assert (
            RegionDayRating.objects.get(region__region_id="CH-4115", date=old_day)
        ).max_rating == RegionDayRating.Rating.LOW

        upsert_bulletin(
            _make_raw_bulletin(
                bulletin_id="move-001",
                dangerRatings=[{"mainValue": "high"}],
                validTime={
                    "startTime": "2025-03-17T07:00:00Z",
                    "endTime": "2025-03-17T17:00:00Z",
                },
            ),
            run,
        )

        for region_id in ("CH-4115", "CH-7111"):
            old_row = RegionDayRating.objects.get(
                region__region_id=region_id, date=old_day
            )
            assert old_row.max_rating == RegionDayRating.Rating.NO_RATING
            assert old_row.source_bulletin is None
            new_row = RegionDayRating.objects.get(
                region__region_id=region_id, date=new_day
            )
            assert new_row.max_rating == RegionDayRating.Rating.HIGH

    def test_first_ingest_passes_no_prior_pairs(self) -> None:
        """SNOW-1168: a bulletin seen for the first time has nothing to reconcile."""
        run = PipelineRunFactory.create()
        with patch(
            "apps.bulletins.services.apply.apply_bulletin_day_ratings",
            return_value=0,
        ) as apply:
            upsert_bulletin(_make_raw_bulletin(bulletin_id="first-001"), run)

        bulletin = Bulletin.objects.get(bulletin_id="first-001")
        apply.assert_called_once_with(bulletin, prior_pairs=set())

    def test_update_passes_the_pairs_captured_before_the_relink(self) -> None:
        """SNOW-1168: the prior pairs are the stored row's, not the new links."""
        run = PipelineRunFactory.create()
        upsert_bulletin(_make_raw_bulletin(bulletin_id="prior-001"), run)

        with patch(
            "apps.bulletins.services.apply.apply_bulletin_day_ratings",
            return_value=0,
        ) as apply:
            upsert_bulletin(
                _make_raw_bulletin(
                    bulletin_id="prior-001",
                    regions=[{"regionID": "CH-7111", "name": "Engadin"}],
                ),
                run,
            )

        prior = apply.call_args.kwargs["prior_pairs"]
        assert {(region.region_id, day) for region, day in prior} == {
            ("CH-4115", date(2025, 3, 16)),
            ("CH-7111", date(2025, 3, 16)),
        }

    # ------------------------------------------------------------------
    # source (SNOW-581)
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        ("custom_data", "expected"),
        [
            ({"CH": {}}, Bulletin.Source.SLF),
            ({"ALBINA": {}}, Bulletin.Source.ALBINA),
            ({"LWD_Tirol": {}}, Bulletin.Source.ALBINA),
            ({"MF": {}}, Bulletin.Source.METEOFRANCE),
        ],
    )
    def test_upsert_sets_source_from_custom_data(
        self, custom_data: dict[str, Any], expected: str
    ) -> None:
        """Each provider's customData marker lands in Bulletin.source."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(bulletin_id="src-001", customData=custom_data)

        upsert_bulletin(raw, run)

        assert Bulletin.objects.get(bulletin_id="src-001").source == expected

    def test_source_is_set_even_when_the_render_model_fails_to_build(self) -> None:
        """Provenance survives a render-model build failure.

        The source column exists precisely so it does not depend on the
        derived artefact — a bulletin whose render model errored must still
        record which provider it came from.
        """
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(bulletin_id="src-fail-001")

        with patch(
            "apps.bulletins.services.apply.build_render_model",
            side_effect=RenderModelBuildError("boom"),
        ):
            upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id="src-fail-001")
        assert bulletin.render_model_version == 0
        assert bulletin.source == Bulletin.Source.SLF

    def test_unknown_custom_data_leaves_source_blank_without_raising(self) -> None:
        """An unrecognised payload degrades to a blank source, not an error."""
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(
            bulletin_id="src-unknown-001",
            customData={"SOMETHING_ELSE": {}},
        )

        upsert_bulletin(raw, run)

        assert Bulletin.objects.get(bulletin_id="src-unknown-001").source == ""


@pytest.mark.django_db
class TestSlfPdfUrlUpsertRoundTrip:
    """Round-trip test: upsert_bulletin stores pdf_url in the database."""

    def test_pdf_url_persisted_on_create(self) -> None:
        """upsert_bulletin stores the supplied pdf_url on the created Bulletin."""
        MicroRegionFactory.create(region_id="CH-4115")
        MicroRegionFactory.create(region_id="CH-7111")
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="en")
        expected_url = slf_pdf_url(raw)
        upsert_bulletin(raw, run, pdf_url=expected_url)
        b = Bulletin.objects.get(bulletin_id="test-001")
        assert b.pdf_url == expected_url


# ---------------------------------------------------------------------------
# BulletinGrouping ingest hook (SNOW-323)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpsertBulletinGroupingHook:
    """Tests for the grouping hook inside upsert_bulletin.

    The hook calls compute_bulletin_grouping_boundary after apply_bulletin_day_ratings.
    It must:
    - Create a BulletinGrouping when the bulletin has two or more boundaried
      regions, and none when it has fewer (SNOW-1001).
    - Swallow exceptions from compute_bulletin_grouping_boundary without
      aborting ingest (so a geometry error never kills the pipeline).
    """

    def test_upsert_creates_grouping_when_region_has_boundary(self) -> None:
        """A bulletin whose linked region has a boundary gets a BulletinGrouping row."""
        major = MajorRegionFactory.create(prefix="CH-4", country="CH")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        MicroRegionFactory.create(
            region_id="CH-4115",
            subregion=sub,
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
                ],
            },
        )
        # The second region carries a boundary too: since SNOW-1001 a bulletin
        # needs two boundaried regions to earn a grouping at all, so seeding
        # this one without geometry would test the guard rather than the hook.
        MicroRegionFactory.create(
            region_id="CH-7111",
            subregion=sub,
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[7.0, 46.4], [7.1, 46.4], [7.1, 46.5], [7.0, 46.5], [7.0, 46.4]]
                ],
            },
        )
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        upsert_bulletin(raw, run)

        assert BulletinGrouping.objects.count() == 1
        grouping = BulletinGrouping.objects.get()
        assert "CH" in grouping.countries

    def test_upsert_writes_no_grouping_for_one_boundaried_region(self) -> None:
        """A bulletin with one boundaried region gets no row — the guard reaches ingest."""
        major = MajorRegionFactory.create(prefix="CH-4", country="CH")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        MicroRegionFactory.create(
            region_id="CH-4115",
            subregion=sub,
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
                ],
            },
        )
        MicroRegionFactory.create(region_id="CH-7111", subregion=sub, boundary=None)
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        upsert_bulletin(raw, run)

        assert Bulletin.objects.count() == 1
        assert BulletinGrouping.objects.count() == 0

    def test_narrowed_reingest_leaves_no_stale_grouping(self) -> None:
        """SNOW-1168: a correction down to one region drops the grouping it had."""
        major = MajorRegionFactory.create(prefix="CH-4", country="CH")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        for region_id, west in (("CH-4115", 6.9), ("CH-7111", 7.0)):
            east = round(west + 0.1, 1)
            MicroRegionFactory.create(
                region_id=region_id,
                subregion=sub,
                boundary={
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [west, 46.4],
                            [east, 46.4],
                            [east, 46.5],
                            [west, 46.5],
                            [west, 46.4],
                        ]
                    ],
                },
            )
        run = PipelineRunFactory.create()
        upsert_bulletin(_make_raw_bulletin(), run)
        assert BulletinGrouping.objects.count() == 1

        upsert_bulletin(
            _make_raw_bulletin(regions=[{"regionID": "CH-7111", "name": "Engadin"}]),
            run,
        )

        assert BulletinGrouping.objects.count() == 0

    def test_upsert_swallows_grouping_exception_and_still_creates_bulletin(
        self,
    ) -> None:
        """A geometry exception from compute_bulletin_grouping_boundary is swallowed.

        Ingest must succeed and the Bulletin row must be created even when the
        grouping service raises — a geometry error must never abort the pipeline.
        """
        MicroRegionFactory.create(region_id="CH-4115")
        MicroRegionFactory.create(region_id="CH-7111")
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        with patch(
            "apps.bulletins.services.apply.compute_bulletin_grouping_boundary",
            side_effect=RuntimeError("geometry exploded"),
        ):
            upsert_bulletin(raw, run)

        assert Bulletin.objects.count() == 1
        assert BulletinGrouping.objects.count() == 0

    def test_failed_recompute_drops_the_stale_grouping_and_fails_the_run(
        self, client: Client
    ) -> None:
        """SNOW-1228: a correction whose recompute fails leaves no old outline.

        A+B is ingested and grouped; a correction re-issues the bulletin as
        A+C and the grouping builder raises. The A+B outline used to stay
        live — drawing an aggregation the provider had withdrawn, over a
        region no longer in it. Now it is deleted, the failure counts against
        the run, and the bulletin and its new region links still commit.
        """
        major = MajorRegionFactory.create(prefix="CH-4", country="CH")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        for region_id, west in (("CH-4115", 6.9), ("CH-7111", 7.0), ("CH-4116", 7.1)):
            east = round(west + 0.1, 1)
            MicroRegionFactory.create(
                region_id=region_id,
                subregion=sub,
                boundary={
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [west, 46.4],
                            [east, 46.4],
                            [east, 46.5],
                            [west, 46.5],
                            [west, 46.4],
                        ]
                    ],
                },
            )
        upsert_bulletin(_make_raw_bulletin(), PipelineRunFactory.create())
        grouping = BulletinGrouping.objects.get()
        target_date = grouping.target_date

        run = PipelineRunFactory.create()
        failed_before = run.records_failed
        with patch(
            "apps.bulletins.services.apply.compute_bulletin_grouping_boundary",
            side_effect=RuntimeError("geometry exploded"),
        ):
            upsert_bulletin(
                _make_raw_bulletin(
                    publication_time="2025-03-15T09:00:00Z",
                    regions=[
                        {"regionID": "CH-4115", "name": "Piz Buin"},
                        {"regionID": "CH-4116", "name": "Silvretta"},
                    ],
                ),
                run,
            )

        assert BulletinGrouping.objects.count() == 0
        run.refresh_from_db()
        assert run.records_failed == failed_before + 1
        bulletin = Bulletin.objects.get()
        assert set(bulletin.regions.values_list("region_id", flat=True)) == {
            "CH-4115",
            "CH-4116",
        }
        cache.clear()
        response = client.get(
            "/api/bulletin-groupings.geojson", {"d": target_date.isoformat()}
        )
        assert response.status_code == 200
        assert response.json()["features"] == []

    def test_a_failing_stale_delete_does_not_abort_ingest(self) -> None:
        """The delete's own guard: ingest continues, the run still fails."""
        MicroRegionFactory.create(region_id="CH-4115")
        MicroRegionFactory.create(region_id="CH-7111")
        run = PipelineRunFactory.create()
        failed_before = run.records_failed

        with (
            patch(
                "apps.bulletins.services.apply.compute_bulletin_grouping_boundary",
                side_effect=RuntimeError("geometry exploded"),
            ),
            patch(
                "apps.bulletins.services.apply.BulletinGrouping.objects.filter",
                side_effect=RuntimeError("delete exploded"),
            ),
        ):
            upsert_bulletin(_make_raw_bulletin(), run)

        assert Bulletin.objects.count() == 1
        run.refresh_from_db()
        assert run.records_failed == failed_before + 1


# ---------------------------------------------------------------------------
# upsert_bulletin: target_date (SNOW-560)
# ---------------------------------------------------------------------------

SENTINELS_DIR = Path(__file__).resolve().parent.parent.parent / "sentinels"

# One evening-issue sentinel per provider — each has an hour >= 12 UTC
# validTime.startTime, so each targets the *following* calendar day.
_TARGET_DATE_SENTINELS = [
    SENTINELS_DIR / "slf" / "A-single-level" / "source.json",
    SENTINELS_DIR / "albina" / "A-single-level" / "source.json",
    SENTINELS_DIR / "meteofrance" / "A-single-level" / "source.json",
]


def _sentinel_id(path: Path) -> str:
    """Return a short human-readable test ID from a sentinel file path."""
    return str(path.relative_to(SENTINELS_DIR).parent)


@pytest.mark.django_db
class TestUpsertBulletinTargetDate:
    """Tests for upsert_bulletin's target_date assignment (SNOW-560)."""

    @pytest.mark.parametrize("source_path", _TARGET_DATE_SENTINELS, ids=_sentinel_id)
    def test_evening_sentinel_targets_the_following_day(
        self, source_path: Path
    ) -> None:
        """Every provider's evening-issue sentinel targets valid_from's next day.

        Each committed sentinel here is an evening issue (validTime.startTime
        hour >= 12 UTC). Only the first region in the payload is seeded —
        upsert_bulletin only requires at least one resolvable region, and the
        rest are skipped as "unknown but well-formed" without affecting
        target_date derivation.
        """
        raw: dict[str, Any] = json.loads(source_path.read_text(encoding="utf-8"))
        first_region = raw["regions"][0]
        MicroRegionFactory.create(
            region_id=first_region["regionID"], name=first_region["name"]
        )

        run = PipelineRunFactory.create()
        upsert_bulletin(raw, run)

        bulletin = Bulletin.objects.get(bulletin_id=raw["bulletinID"])
        valid_from = parse_dt(raw["validTime"]["startTime"])
        assert valid_from.hour >= 12, "sentinel is expected to be an evening issue"
        expected = target_day_for_valid_from(valid_from)
        assert bulletin.target_date == expected

    def test_morning_and_previous_evening_issue_share_a_target_date(
        self, _seed_test_regions: None
    ) -> None:
        """A morning issue and the prior evening's issue target the same day."""
        run = PipelineRunFactory.create()

        evening_raw = _make_raw_bulletin(
            bulletin_id="evening-issue",
            publication_time="2025-03-14T16:00:00Z",
            validTime={
                "startTime": "2025-03-14T16:00:00Z",
                "endTime": "2025-03-15T08:00:00Z",
            },
        )
        morning_raw = _make_raw_bulletin(
            bulletin_id="morning-issue",
            publication_time="2025-03-15T08:00:00Z",
            validTime={
                "startTime": "2025-03-15T07:00:00Z",
                "endTime": "2025-03-15T17:00:00Z",
            },
        )

        upsert_bulletin(evening_raw, run)
        upsert_bulletin(morning_raw, run)

        evening_bulletin = Bulletin.objects.get(bulletin_id="evening-issue")
        morning_bulletin = Bulletin.objects.get(bulletin_id="morning-issue")

        assert evening_bulletin.target_date == date(2025, 3, 15)
        assert morning_bulletin.target_date == date(2025, 3, 15)
        assert evening_bulletin.target_date == morning_bulletin.target_date


@pytest.mark.usefixtures("_seed_test_regions")
class TestStoreThenApply:
    """apply_bulletin and apply_feed_item (SNOW-1250)."""

    def _record(self, raw: dict[str, Any], pdf: str = "https://x/a.pdf") -> Any:
        """A walk's record for ``raw``."""
        return NormalisedBulletin(raw["bulletinID"], "SLF", raw, lambda: pdf)

    def test_stores_in_ingest_then_writes_the_bulletin(self) -> None:
        """The Bulletin is written from the stored row, CAAML and link alike."""
        raw = _make_raw_bulletin("b-1")
        outcome = apply_bulletin(self._record(raw), PipelineRunFactory.create())
        assert outcome == "created"
        source = SourceBulletin.objects.get(bulletin_id="b-1")
        bulletin = Bulletin.objects.get(bulletin_id="b-1")
        assert bulletin.raw_data["properties"] == source.caaml == raw
        assert bulletin.pdf_url == source.pdf_url == "https://x/a.pdf"

    def test_a_stored_bulletin_missing_here_is_applied_again(self) -> None:
        """Ingest has it but Bulletin does not (a failed apply): it is retried."""
        raw = _make_raw_bulletin("b-1")
        store_bulletin(self._record(raw))
        outcome = apply_bulletin(self._record(raw), PipelineRunFactory.create())
        assert outcome == "created"
        assert Bulletin.objects.filter(bulletin_id="b-1").exists()

    def test_the_same_bulletin_twice_is_unchanged(self) -> None:
        """The second apply writes nothing; force rewrites it."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        run = PipelineRunFactory.create()
        assert apply_bulletin(self._record(raw), run) == "unchanged"
        assert apply_bulletin(self._record(raw), run, force=True) == "updated"

    def test_a_dry_feed_item_decides_without_writing(self) -> None:
        """commit=False reports the outcome and leaves the table alone."""
        item = {"bulletin_id": "b-1", "caaml": _make_raw_bulletin("b-1")}
        assert apply_feed_item(item, None, existing=None, commit=False) == "created"
        assert not Bulletin.objects.exists()

    def test_the_services_render_model_is_copied(self) -> None:
        """apply_bulletin stores the model the service built (SNOW-1270)."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        source = SourceBulletin.objects.get(bulletin_id="b-1")
        bulletin = Bulletin.objects.get(bulletin_id="b-1")
        assert source.render_model_version == RENDER_MODEL_VERSION
        assert bulletin.render_model == source.render_model

    def test_a_current_served_model_is_copied_not_built(self) -> None:
        """A model at this code's version is stored as served."""
        served = {"version": RENDER_MODEL_VERSION, "marker": "served"}
        item = {
            "bulletin_id": "b-1",
            "caaml": _make_raw_bulletin("b-1"),
            "render_model": served,
        }
        with patch("apps.bulletins.services.apply.build_render_model") as build:
            apply_feed_item(item, PipelineRunFactory.create(), existing=None)
        build.assert_not_called()
        bulletin = Bulletin.objects.get(bulletin_id="b-1")
        assert bulletin.render_model == served
        assert bulletin.render_model_version == RENDER_MODEL_VERSION

    def test_a_served_model_at_another_version_is_built_here(self) -> None:
        """An older (or newer) service's model is ignored; this code builds its own."""
        item = {
            "bulletin_id": "b-1",
            "caaml": _make_raw_bulletin("b-1"),
            "render_model": {"version": 1, "marker": "served"},
        }
        apply_feed_item(item, PipelineRunFactory.create(), existing=None)
        bulletin = Bulletin.objects.get(bulletin_id="b-1")
        assert "marker" not in bulletin.render_model
        assert bulletin.render_model["version"] == RENDER_MODEL_VERSION

    def test_a_rebuilt_served_model_updates_an_unchanged_bulletin(self) -> None:
        """Same CAAML, a different current model: the service rebuilt it."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        existing = Bulletin.objects.get(bulletin_id="b-1")
        item = {
            "bulletin_id": "b-1",
            "pdf_url": existing.pdf_url,
            "caaml": raw,
            "render_model": {"version": RENDER_MODEL_VERSION, "marker": "new"},
        }
        assert (
            apply_feed_item(item, PipelineRunFactory.create(), existing=existing)
            == "updated"
        )
        existing.refresh_from_db()
        assert existing.render_model["marker"] == "new"

    def test_a_stale_served_model_leaves_an_unchanged_bulletin(self) -> None:
        """Same CAAML, an old-version model: nothing to copy, nothing to do."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        existing = Bulletin.objects.get(bulletin_id="b-1")
        item = {
            "bulletin_id": "b-1",
            "pdf_url": existing.pdf_url,
            "caaml": raw,
            "render_model": {"version": 1},
        }
        assert (
            apply_feed_item(item, PipelineRunFactory.create(), existing=existing)
            == "unchanged"
        )

    def test_the_services_day_ratings_are_copied(self) -> None:
        """The service rates the bulletin's regions; this instance shows them."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        service = {r.region_code: r.max_rating for r in SourceDayRating.objects.all()}
        assert set(service) == {"CH-4115", "CH-7111"}
        local = {
            r.region.region_id: r.max_rating
            for r in RegionDayRating.objects.select_related("region")
        }
        assert local == service

    def test_the_services_rating_stands_where_they_differ(self) -> None:
        """A copy overwrites a local rating that disagrees."""
        raw = _make_raw_bulletin("b-1")
        apply_bulletin(self._record(raw), PipelineRunFactory.create())
        SourceDayRating.objects.filter(region_code="CH-4115").update(max_rating="high")
        copy_service_day_ratings(
            {("CH-4115", target_day_for_valid_from(parse_dt("2025-03-15T17:00:00Z")))}
        )
        assert (
            RegionDayRating.objects.get(region__region_id="CH-4115").max_rating
            == "high"
        )

    def test_a_failed_copy_never_aborts_ingest(self) -> None:
        """The local rating stands and the bulletin is applied."""
        raw = _make_raw_bulletin("b-1")
        with patch(
            "apps.bulletins.services.apply.apply_day_rating_item",
            side_effect=RuntimeError("boom"),
        ):
            outcome = apply_bulletin(self._record(raw), PipelineRunFactory.create())
        assert outcome == "created"
        assert RegionDayRating.objects.count() == 2

    def test_committing_needs_a_run(self) -> None:
        """A write must be attributed to a PipelineRun."""
        item = {"bulletin_id": "b-1", "caaml": _make_raw_bulletin("b-1")}
        with pytest.raises(ValueError, match="PipelineRun"):
            apply_feed_item(item, None, existing=None)


# ---------------------------------------------------------------------------
# The service's index and derived block (SNOW-1341)
# ---------------------------------------------------------------------------


def _feed_item(raw: dict[str, Any], **index: Any) -> dict[str, Any]:
    """Return ``raw`` as a bulletins feed item, with any index keys given."""
    return {"bulletin_id": raw["bulletinID"], "pdf_url": "", "caaml": raw, **index}


@pytest.mark.django_db
class TestFedIndex:
    """upsert_bulletin uses the feed item's index and falls back to the CAAML."""

    pytestmark = pytest.mark.usefixtures("_seed_test_regions")

    def test_fed_values_are_used_as_sent(self) -> None:
        """target_date, valid_from and region_codes are not re-derived."""
        raw = _make_raw_bulletin(
            dangerRatings=[{"mainValue": "moderate", "validTimePeriod": "all_day"}]
        )
        item = _feed_item(
            raw,
            valid_from="2025-03-15T18:00:00+00:00",
            target_date="2025-03-20",
            region_codes=["CH-7111"],
        )
        apply_feed_item(item, PipelineRunFactory.create(), existing=None)
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.valid_from == datetime(2025, 3, 15, 18, tzinfo=UTC)
        assert bulletin.target_date == date(2025, 3, 20)
        assert list(bulletin.regions.values_list("region_id", flat=True)) == ["CH-7111"]
        # The link's name is still the CAAML's — the feed does not send it.
        link = RegionBulletin.objects.get(bulletin=bulletin)
        assert link.region_name_at_time == "Engadin"

    def test_an_item_without_them_reads_the_caaml(self) -> None:
        """An older service's item: every value comes from the CAAML."""
        raw = _make_raw_bulletin()
        apply_feed_item(_feed_item(raw), PipelineRunFactory.create(), existing=None)
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        start = parse_dt(raw["validTime"]["startTime"])
        assert bulletin.valid_from == start
        assert bulletin.target_date == target_day_for_valid_from(start)
        assert set(bulletin.regions.values_list("region_id", flat=True)) == {
            "CH-4115",
            "CH-7111",
        }

    def test_null_index_values_read_the_caaml(self) -> None:
        """A row the service never indexed sends nulls; they are ignored."""
        raw = _make_raw_bulletin()
        item = _feed_item(raw, valid_from=None, target_date=None)
        apply_feed_item(item, PipelineRunFactory.create(), existing=None)
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.target_date == target_day_for_valid_from(
            parse_dt(raw["validTime"]["startTime"])
        )

    def test_a_current_served_model_is_copied_derived_and_all(self) -> None:
        """The derived block arrives with the model; nothing is rebuilt."""
        raw = _make_raw_bulletin(
            dangerRatings=[{"mainValue": "high", "validTimePeriod": "all_day"}]
        )
        served = build_render_model(raw)
        served["derived"]["problem_family"] = "served"
        with patch("apps.bulletins.services.apply.build_render_model") as build:
            apply_feed_item(
                _feed_item(raw, render_model=served),
                PipelineRunFactory.create(),
                existing=None,
            )
        build.assert_not_called()
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.render_model["derived"]["problem_family"] == "served"

    def test_danger_levels_column_is_written(self) -> None:
        """'|'-delimited, low first, from the stored model's derived block."""
        raw = _make_raw_bulletin(
            dangerRatings=[
                {"mainValue": "considerable", "validTimePeriod": "earlier"},
                {"mainValue": "moderate", "validTimePeriod": "later"},
            ]
        )
        upsert_bulletin(raw, PipelineRunFactory.create())
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.danger_levels == "|moderate|considerable|"

    def test_a_failed_build_names_no_level(self) -> None:
        """The version-0 sentinel carries no ratings, so the column is empty."""
        raw = _make_raw_bulletin(customData={})
        upsert_bulletin(raw, PipelineRunFactory.create())
        bulletin = Bulletin.objects.get(bulletin_id="test-001")
        assert bulletin.render_model_version == 0
        assert bulletin.danger_levels == ""
