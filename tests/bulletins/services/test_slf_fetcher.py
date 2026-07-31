"""
tests/bulletins/services/test_slf_fetcher.py — Tests for the slf_fetcher service.

Covers:
  - _normalise_response: all three API response shapes + empty cases
  - _parse_dt: ISO-8601 parsing
  - _get_region: returns seeded Region, raises UnknownRegionError otherwise
  - upsert_bulletin: creation, update, region linking
  - fetch_bulletin_page: HTTP call with mocked responses
  - run_slf_pipeline: full orchestration with mocked API pages
  - _slf_pdf_url: URL derivation for SLF archive PDFs
"""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.db import IntegrityError
from django.test import override_settings

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    PipelineRun,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.day_rating import target_day_for_valid_from
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
)
from apps.bulletins.services.slf_fetcher import (
    NoResolvableRegionsError,
    UnknownRegionError,
    _get_region,
    _normalise_response,
    _parse_dt,
    _resolve_issued_at,
    _slf_pdf_url,
    fetch_bulletin_page,
    run_slf_pipeline,
    upsert_bulletin,
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
# _normalise_response
# ---------------------------------------------------------------------------


class TestNormaliseResponse:
    """Tests for _normalise_response."""

    def test_flat_list(self) -> None:
        """A flat list of bulletin dicts is returned as-is."""
        bulletins = [{"bulletinID": "a"}, {"bulletinID": "b"}]
        assert _normalise_response(bulletins) == bulletins

    def test_single_collection_object(self) -> None:
        """A dict with a 'bulletins' key unwraps to the inner list."""
        data = {"bulletins": [{"bulletinID": "a"}]}
        assert _normalise_response(data) == [{"bulletinID": "a"}]

    def test_list_of_collection_objects(self) -> None:
        """A list of collection objects is flattened."""
        data = [
            {"bulletins": [{"bulletinID": "a"}]},
            {"bulletins": [{"bulletinID": "b"}, {"bulletinID": "c"}]},
        ]
        result = _normalise_response(data)
        assert len(result) == 3
        assert [b["bulletinID"] for b in result] == ["a", "b", "c"]

    def test_empty_list(self) -> None:
        """An empty list returns an empty list."""
        assert _normalise_response([]) == []

    def test_empty_dict(self) -> None:
        """A dict without 'bulletins' returns an empty list."""
        assert _normalise_response({}) == []

    def test_none_returns_empty(self) -> None:
        """None returns an empty list."""
        assert _normalise_response(None) == []

    def test_string_returns_empty(self) -> None:
        """An unexpected string returns an empty list."""
        assert _normalise_response("unexpected") == []


# ---------------------------------------------------------------------------
# _parse_dt
# ---------------------------------------------------------------------------


class TestParseDt:
    """Tests for _parse_dt."""

    def test_utc_timestamp(self) -> None:
        """Parses a Z-suffixed ISO-8601 string to a UTC-aware datetime."""
        result = _parse_dt("2025-03-15T08:00:00Z")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_offset_timestamp_is_converted_to_utc(self) -> None:
        """An ISO-8601 string with a +01:00 offset is converted to UTC."""
        result = _parse_dt("2025-03-15T09:00:00+01:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_negative_offset_timestamp_is_converted_to_utc(self) -> None:
        """An ISO-8601 string with a -05:00 offset is converted to UTC."""
        result = _parse_dt("2025-03-15T03:00:00-05:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC

    def test_naive_timestamp_is_assumed_utc(self) -> None:
        """A naive ISO-8601 string is assumed to be UTC."""
        result = _parse_dt("2025-03-15T08:00:00")
        assert result == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)
        assert result.tzinfo is UTC


# ---------------------------------------------------------------------------
# _resolve_issued_at
# ---------------------------------------------------------------------------


class TestResolveIssuedAt:
    """Tests for _resolve_issued_at."""

    def test_uses_publication_time_when_present(self) -> None:
        """Modern bulletins return ``publicationTime`` parsed to UTC."""
        raw = {
            "publicationTime": "2025-03-15T08:00:00Z",
            "validTime": {
                "startTime": "2025-03-15T17:00:00Z",
                "endTime": "2025-03-16T17:00:00Z",
            },
        }
        assert _resolve_issued_at(raw) == datetime(2025, 3, 15, 8, 0, 0, tzinfo=UTC)

    def test_falls_back_to_valid_time_start_when_missing(self) -> None:
        """Pre-2024 bulletins lack publicationTime; fall back to validTime.startTime."""
        raw = {
            "validTime": {
                "startTime": "2023-12-13T16:00:00Z",
                "endTime": "2023-12-14T16:00:00Z",
            },
        }
        assert _resolve_issued_at(raw) == datetime(2023, 12, 13, 16, 0, 0, tzinfo=UTC)

    def test_falls_back_when_publication_time_is_empty_string(self) -> None:
        """An empty publicationTime string is treated the same as missing."""
        raw = {
            "publicationTime": "",
            "validTime": {
                "startTime": "2023-12-13T16:00:00Z",
                "endTime": "2023-12-14T16:00:00Z",
            },
        }
        assert _resolve_issued_at(raw) == datetime(2023, 12, 13, 16, 0, 0, tzinfo=UTC)


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
            "apps.bulletins.services.slf_fetcher.apply_bulletin_day_ratings",
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
            "apps.bulletins.services.slf_fetcher.apply_bulletin_day_ratings",
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
            "apps.bulletins.services.slf_fetcher.build_render_model",
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
            "apps.bulletins.services.slf_fetcher.build_render_model",
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
            "apps.bulletins.services.slf_fetcher.build_render_model",
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
            "apps.bulletins.services.slf_fetcher.build_render_model",
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


# ---------------------------------------------------------------------------
# fetch_bulletin_page
# ---------------------------------------------------------------------------


class TestFetchBulletinPage:
    """Tests for fetch_bulletin_page."""

    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_returns_normalised_bulletins(self, mock_get: MagicMock) -> None:
        """Fetches a page from the API and normalises the response."""
        mock_response = MagicMock()
        mock_response.json.return_value = [_make_raw_bulletin()]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = fetch_bulletin_page("en", 50, 0)

        assert len(result) == 1
        assert result[0]["bulletinID"] == "test-001"
        mock_get.assert_called_once_with(
            "https://aws.slf.ch/api/bulletin-list/caaml/en/json",
            params={"limit": 50, "offset": 0},
            timeout=30,
        )

    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_raises_on_http_error(self, mock_get: MagicMock) -> None:
        """Raises HTTPError when the API returns a non-2xx status."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500")
        mock_get.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            fetch_bulletin_page("en", 50, 0)

    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_passes_lang_in_url(self, mock_get: MagicMock) -> None:
        """The language code is included in the URL path."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_bulletin_page("de", 10, 5)

        url = mock_get.call_args[0][0]
        assert "/de/json" in url

    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_base_url_override_replaces_default(self, mock_get: MagicMock) -> None:
        """``base_url=`` swaps out the API base for that single call."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_bulletin_page(
            "en",
            50,
            0,
            base_url="http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml",
        )

        url = mock_get.call_args[0][0]
        assert url == (
            "http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml/en/json"
        )

    @override_settings(
        SLF_API_BASE_URL="https://override.example/api/bulletin-list/caaml"
    )
    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_default_base_url_falls_back_to_settings(self, mock_get: MagicMock) -> None:
        """Without ``base_url=``, the call reads ``settings.SLF_API_BASE_URL``."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_bulletin_page("en", 50, 0)

        url = mock_get.call_args[0][0]
        assert url == "https://override.example/api/bulletin-list/caaml/en/json"


# ---------------------------------------------------------------------------
# run_slf_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunPipeline:
    """Tests for run_slf_pipeline."""

    pytestmark = pytest.mark.usefixtures("_seed_test_regions")

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_creates_bulletins_in_date_range(self, mock_fetch: MagicMock) -> None:
        """Bulletins within the date range are stored."""
        mock_fetch.return_value = [
            _make_raw_bulletin("b1", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("b2", "2025-03-14T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 2
        assert Bulletin.objects.count() == 2

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_all_unknown_regions_increments_records_failed(
        self, mock_fetch: MagicMock
    ) -> None:
        """SNOW-547: an unresolvable bulletin fails itself, not the batch.

        ``records_failed > 0`` is what makes ``fetch_bulletins`` exit
        non-zero per the management-command contract, so cron/CI notice.
        The rest of the page still ingests.
        """
        unresolvable = _make_raw_bulletin("no-regions", "2025-03-15T08:00:00Z")
        unresolvable["regions"] = [{"regionID": "CH-XXXX", "name": "Nonexistent"}]
        mock_fetch.return_value = [
            unresolvable,
            _make_raw_bulletin("fine", "2025-03-15T09:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.records_failed == 1
        assert run.records_created == 1
        assert not Bulletin.objects.filter(bulletin_id="no-regions").exists()
        assert Bulletin.objects.filter(bulletin_id="fine").exists()

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_skips_bulletins_newer_than_end_date(self, mock_fetch: MagicMock) -> None:
        """Bulletins newer than the end date are skipped."""
        mock_fetch.return_value = [
            _make_raw_bulletin("future", "2025-04-01T08:00:00Z"),
            _make_raw_bulletin("in-range", "2025-03-15T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.records_created == 1
        assert Bulletin.objects.filter(bulletin_id="in-range").exists()
        assert not Bulletin.objects.filter(bulletin_id="future").exists()

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_stops_at_start_date_boundary(self, mock_fetch: MagicMock) -> None:
        """Pagination stops when a bulletin older than start date is hit."""
        mock_fetch.return_value = [
            _make_raw_bulletin("in-range", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("too-old", "2025-03-13T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.records_created == 1
        assert not Bulletin.objects.filter(bulletin_id="too-old").exists()

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_dry_run_does_not_write(self, mock_fetch: MagicMock) -> None:
        """Dry run fetches data but does not persist bulletins."""
        mock_fetch.return_value = [
            _make_raw_bulletin("b1", "2025-03-15T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            dry_run=True,
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 0
        assert Bulletin.objects.count() == 0

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_skips_existing_without_force(self, mock_fetch: MagicMock) -> None:
        """Without --force, existing bulletins are skipped."""
        # Pre-create the bulletin
        pre_run = PipelineRunFactory.create()
        upsert_bulletin(
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
            pre_run,
        )

        mock_fetch.return_value = [
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            force=False,
        )

        assert run.records_created == 0
        assert run.records_updated == 0

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_updates_existing_with_force(self, mock_fetch: MagicMock) -> None:
        """With --force, existing bulletins are upserted."""
        pre_run = PipelineRunFactory.create()
        upsert_bulletin(
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
            pre_run,
        )

        mock_fetch.return_value = [
            _make_raw_bulletin("existing", "2025-03-15T08:00:00Z"),
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            force=True,
        )

        assert run.records_updated == 1
        assert Bulletin.objects.count() == 1

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_marks_run_failed_on_exception(self, mock_fetch: MagicMock) -> None:
        """Run is marked FAILED if fetch raises an exception."""
        mock_fetch.side_effect = requests.ConnectionError("timeout")

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.FAILED
        assert "timeout" in run.error_message

    @patch("apps.bulletins.services.slf_fetcher.PAGE_SIZE", 1)
    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_paginates_until_empty_page(self, mock_fetch: MagicMock) -> None:
        """Pages until the API returns an empty list."""
        # With PAGE_SIZE=1, a page with 1 result does NOT trigger the
        # "fewer than requested" early exit, so a second fetch occurs.
        mock_fetch.side_effect = [
            [_make_raw_bulletin("b1", "2025-03-15T08:00:00Z")],
            [],
        ]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == 1
        assert mock_fetch.call_count == 2

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_run_records_triggered_by(self, mock_fetch: MagicMock) -> None:
        """The triggered_by label is stored on the PipelineRun."""
        mock_fetch.return_value = []

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="fetch_bulletins command",
        )

        assert run.triggered_by == "fetch_bulletins command"

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_base_url_threads_through_to_fetch(self, mock_fetch: MagicMock) -> None:
        """``base_url=`` is forwarded verbatim to ``fetch_bulletin_page``."""
        mock_fetch.return_value = []

        run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
            base_url="http://mirror.test/api/bulletin-list/caaml",
        )

        assert mock_fetch.call_args.kwargs["base_url"] == (
            "http://mirror.test/api/bulletin-list/caaml"
        )

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_on_fetched_called_for_every_record(self, mock_fetch: MagicMock) -> None:
        """``on_fetched`` fires once per raw record in the page."""
        mock_fetch.return_value = [
            _make_raw_bulletin("a", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("b", "2025-03-14T08:00:00Z"),
        ]
        seen: list[str] = []

        run_slf_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
            on_fetched=lambda raw: seen.append(raw["bulletinID"]),
        )

        assert seen == ["a", "b"]

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
    def test_on_fetched_fires_for_out_of_range_records(
        self, mock_fetch: MagicMock
    ) -> None:
        """``on_fetched`` captures records outside the date window too.

        The stash should mirror everything the API returned, regardless
        of whether the orchestration loop chose to ingest the record.
        """
        mock_fetch.return_value = [
            _make_raw_bulletin("future", "2025-04-01T08:00:00Z"),  # newer-than-end
            _make_raw_bulletin("in-range", "2025-03-15T08:00:00Z"),
            _make_raw_bulletin("too-old", "2025-03-13T08:00:00Z"),  # ends loop
        ]
        seen: list[str] = []

        run_slf_pipeline(
            start=date(2025, 3, 14),
            end=date(2025, 3, 15),
            triggered_by="test",
            on_fetched=lambda raw: seen.append(raw["bulletinID"]),
        )

        # The "too-old" record IS observed by on_fetched before the
        # out-of-range branch terminates pagination — the stash captures
        # the page boundary even though the DB does not.
        assert seen == ["future", "in-range", "too-old"]


# ---------------------------------------------------------------------------
# _slf_pdf_url
# ---------------------------------------------------------------------------


class TestSlfPdfUrl:
    """Tests for _slf_pdf_url URL derivation (pure, no DB)."""

    def test_afternoon_issue_produces_1700_url(self) -> None:
        """A bulletin published at 16:00 UTC (17:00 CET) uses the 17-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="en")
        url = _slf_pdf_url(raw)
        assert url == (
            "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/"
            "2026/03/Bulletin_2026-03-15_17-00_en.pdf"
        )

    def test_morning_update_produces_0800_url(self) -> None:
        """A bulletin published at 07:00 UTC (08:00 CET) uses the 08-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T07:00:00Z", lang="en")
        url = _slf_pdf_url(raw)
        assert url == (
            "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/"
            "2026/03/Bulletin_2026-03-15_08-00_en.pdf"
        )

    def test_hour_exactly_12_uses_1700(self) -> None:
        """UTC hour == 12 is >= 12, so it maps to the 17-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T12:00:00Z", lang="en")
        url = _slf_pdf_url(raw)
        assert "_17-00_" in url

    def test_hour_11_uses_0800(self) -> None:
        """UTC hour == 11 is < 12, so it maps to the 08-00 slot."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T11:59:59Z", lang="en")
        url = _slf_pdf_url(raw)
        assert "_08-00_" in url

    def test_german_language_variant(self) -> None:
        """Language is taken from raw['lang'] and embedded in the filename."""
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="de")
        url = _slf_pdf_url(raw)
        assert url.endswith("_17-00_de.pdf")

    def test_folder_uses_issue_date(self) -> None:
        """The year/month folder is derived from the issue date, not validity."""
        raw = _make_raw_bulletin(publication_time="2025-12-31T16:00:00Z", lang="en")
        url = _slf_pdf_url(raw)
        # Folder must be 2025/12, not 2026/01
        assert "/2025/12/" in url


@pytest.mark.django_db
class TestSlfPdfUrlUpsertRoundTrip:
    """Round-trip test: upsert_bulletin stores pdf_url in the database."""

    def test_pdf_url_persisted_on_create(self) -> None:
        """upsert_bulletin stores the supplied pdf_url on the created Bulletin."""
        MicroRegionFactory.create(region_id="CH-4115")
        MicroRegionFactory.create(region_id="CH-7111")
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin(publication_time="2026-03-15T16:00:00Z", lang="en")
        expected_url = _slf_pdf_url(raw)
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
    - Create a BulletinGrouping when the bulletin has boundaried regions.
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
        # The second region (CH-7111) is seeded without a boundary so we
        # confirm that the missing-boundary case is handled gracefully.
        MicroRegionFactory.create(region_id="CH-7111", subregion=sub, boundary=None)
        run = PipelineRunFactory.create()
        raw = _make_raw_bulletin()

        upsert_bulletin(raw, run)

        assert BulletinGrouping.objects.count() == 1
        grouping = BulletinGrouping.objects.get()
        assert "CH" in grouping.countries

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
            "apps.bulletins.services.slf_fetcher.compute_bulletin_grouping_boundary",
            side_effect=RuntimeError("geometry exploded"),
        ):
            upsert_bulletin(raw, run)

        assert Bulletin.objects.count() == 1
        assert BulletinGrouping.objects.count() == 0


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
        valid_from = _parse_dt(raw["validTime"]["startTime"])
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
