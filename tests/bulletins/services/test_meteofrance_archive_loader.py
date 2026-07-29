# tests/bulletins/services/test_meteofrance_archive_loader.py — Tests for the
# meteofrance_archive_loader service.
#
# Exercises load_meteofrance_archive() in both dry-run and commit modes against
# minimal NDJSON lines, covering the key counter fields and the triggered_by
# propagation.
"""Tests for bulletins.services.meteofrance_archive_loader."""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.core.management import call_command

from bulletins.models import Bulletin, PipelineRun
from bulletins.services.meteofrance_archive_loader import (
    LoadResult,
    _slug_to_region_id,
    load_meteofrance_archive,
)
from regions.models import MicroRegion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line(slug: str, code: str, date_str: str) -> str:
    """Return a minimal valid NDJSON line for a given massif slug and date.

    Args:
        slug: Massif slug used in ``customData.MF.massif``
            (e.g. ``"ARAVIS"``).
        code: Zero-padded massif code used in ``regionID``
            (e.g. ``"02"``).
        date_str: Date string in ``YYYY-MM-DD`` format.

    Returns:
        JSON-serialised NDJSON line.

    """
    envelope: dict[str, Any] = {
        "type": "Feature",
        "geometry": None,
        "properties": {
            "lang": "fr",
            "regions": [{"regionID": f"FR-{slug}", "name": slug.capitalize()}],
            "validTime": {
                "startTime": f"{date_str}T00:00:00+00:00",
                "endTime": f"{date_str}T23:59:59+00:00",
            },
            "customData": {"MF": {"massif": slug, "date": date_str}},
            "dangerRatings": [],
            "avalancheProblems": [],
        },
    }
    return json.dumps(envelope)


_ARAVIS_LINE = _make_line("ARAVIS", "02", "2026-01-15")
_CHABLAIS_LINE = _make_line("CHABLAIS", "01", "2026-01-15")
_UNKNOWN_LINE = _make_line("UNKNOWNMASSIF", "00", "2026-01-15")
_BAD_JSON_LINE = "this is not json"
_BAD_SHAPE_LINE = json.dumps({"no_properties_key": True})


@pytest.fixture()
def _load_fr_regions(db: object) -> None:
    """Load the eaws_FR.json fixture so FR-01 and FR-02 MicroRegion rows exist."""
    call_command("loaddata", "eaws_FR", verbosity=0)


# ---------------------------------------------------------------------------
# LoadResult.as_summary()
# ---------------------------------------------------------------------------


class TestLoadResultAsSummary:
    """Tests for LoadResult.as_summary()."""

    def test_commit_mode_summary_contains_key_counts(self) -> None:
        """as_summary() in commit mode includes created, updated, and failed."""
        result = LoadResult(
            total=10,
            bad_shape=0,
            unknown_slug=0,
            would_upsert=0,
            created=8,
            updated=2,
            failed=0,
            pipeline_run_id=42,
        )
        summary = result.as_summary()
        assert "8 created" in summary
        assert "2 updated" in summary
        assert "0 failed" in summary
        assert "#42" in summary

    def test_dry_run_summary_contains_would_upsert(self) -> None:
        """as_summary() in dry-run mode (pipeline_run_id=None) notes would-upsert."""
        result = LoadResult(
            total=5,
            bad_shape=0,
            unknown_slug=0,
            would_upsert=5,
            created=0,
            updated=0,
            failed=0,
            pipeline_run_id=None,
        )
        summary = result.as_summary()
        assert "dry-run" in summary
        assert "5 would-upsert" in summary


# ---------------------------------------------------------------------------
# Dry-run mode (commit=False)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDryRunMode:
    """load_meteofrance_archive with commit=False must not write to the database."""

    def test_dry_run_does_not_create_bulletins(self) -> None:
        """No Bulletin rows are created in dry-run mode."""
        load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=False)
        assert Bulletin.objects.count() == 0

    def test_dry_run_does_not_open_pipeline_run(self) -> None:
        """No PipelineRun rows are created in dry-run mode."""
        load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=False)
        assert PipelineRun.objects.count() == 0

    def test_dry_run_pipeline_run_id_is_none(self) -> None:
        """LoadResult.pipeline_run_id is None in dry-run mode."""
        result = load_meteofrance_archive([_ARAVIS_LINE], commit=False)
        assert result.pipeline_run_id is None

    def test_dry_run_counts_would_upsert(self) -> None:
        """Valid lines are counted as would_upsert in dry-run mode."""
        result = load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=False)
        assert result.would_upsert == 2
        assert result.total == 2


# ---------------------------------------------------------------------------
# Commit mode (commit=True)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommitMode:
    """load_meteofrance_archive with commit=True writes PipelineRun and Bulletin rows."""

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def test_commit_creates_pipeline_run(self) -> None:
        """A PipelineRun is created and marked SUCCESS."""
        load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=True)
        assert PipelineRun.objects.count() == 1
        run = PipelineRun.objects.get()
        assert run.status == PipelineRun.Status.SUCCESS

    def test_commit_creates_bulletins(self) -> None:
        """One Bulletin row per valid line is created."""
        load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=True)
        assert Bulletin.objects.count() == 2

    def test_load_result_created_count(self) -> None:
        """LoadResult.created equals the number of new rows inserted."""
        result = load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=True)
        assert result.created == 2

    def test_load_result_pipeline_run_id_set(self) -> None:
        """LoadResult.pipeline_run_id matches the created PipelineRun pk."""
        result = load_meteofrance_archive([_ARAVIS_LINE], commit=True)
        assert result.pipeline_run_id is not None
        run = PipelineRun.objects.get(pk=result.pipeline_run_id)
        assert run.status == PipelineRun.Status.SUCCESS

    def test_second_commit_updates_not_creates(self) -> None:
        """A second commit updates existing rows; LoadResult.updated == 1."""
        load_meteofrance_archive([_ARAVIS_LINE], commit=True)
        result = load_meteofrance_archive([_ARAVIS_LINE], commit=True)
        assert Bulletin.objects.count() == 1
        assert result.updated == 1
        assert result.created == 0


# ---------------------------------------------------------------------------
# Unknown slug handling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnknownSlug:
    """Unknown slugs increment unknown_slug and continue."""

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def test_unknown_slug_bumps_counter(self) -> None:
        """One unknown slug increments LoadResult.unknown_slug by 1."""
        result = load_meteofrance_archive([_ARAVIS_LINE, _UNKNOWN_LINE], commit=True)
        assert result.unknown_slug == 1

    def test_unknown_slug_does_not_abort(self) -> None:
        """The valid row is still processed despite the unknown slug."""
        result = load_meteofrance_archive([_ARAVIS_LINE, _UNKNOWN_LINE], commit=True)
        assert result.created == 1

    def test_unknown_slug_increments_records_failed(self) -> None:
        """run.records_failed is incremented for unknown-slug rows."""
        load_meteofrance_archive([_ARAVIS_LINE, _UNKNOWN_LINE], commit=True)
        run = PipelineRun.objects.get()
        assert run.records_failed >= 1


# ---------------------------------------------------------------------------
# Unresolvable regions (SNOW-547)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUnresolvableRegions:
    """A row whose regions don't resolve fails that row, not the file.

    SNOW-547 made ``upsert_bulletin`` raise on total resolution failure.
    This loader's documented contract is that per-row failures increment
    counters and continue, so the new exception must be caught here —
    otherwise one bad row aborts an entire NDJSON archive load.
    """

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def _drop_aravis_region(self) -> None:
        """Delete the MicroRegion the ARAVIS line resolves to."""
        MicroRegion.objects.filter(region_id=_slug_to_region_id("ARAVIS")).delete()

    def test_unresolvable_row_does_not_abort_the_file(self) -> None:
        """The following valid row is still ingested."""
        self._drop_aravis_region()

        result = load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=True)

        assert result.created == 1
        assert Bulletin.objects.count() == 1

    def test_unresolvable_row_increments_records_failed(self) -> None:
        """The failure reaches run.records_failed and LoadResult.failed."""
        self._drop_aravis_region()

        result = load_meteofrance_archive([_ARAVIS_LINE, _CHABLAIS_LINE], commit=True)

        assert result.failed == 1
        assert PipelineRun.objects.get().records_failed == 1


# ---------------------------------------------------------------------------
# Bad JSON / shape errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBadShape:
    """Malformed lines increment bad_shape and continue."""

    def test_bad_json_bumps_bad_shape(self) -> None:
        """A line that cannot be parsed as JSON increments bad_shape."""
        result = load_meteofrance_archive([_BAD_JSON_LINE], commit=False)
        assert result.bad_shape == 1

    def test_bad_shape_line_bumps_bad_shape(self) -> None:
        """A JSON line with no 'properties' key increments bad_shape."""
        result = load_meteofrance_archive([_BAD_SHAPE_LINE], commit=False)
        assert result.bad_shape == 1

    def test_bad_shape_does_not_abort(self) -> None:
        """A subsequent valid line is still processed after a bad line."""
        result = load_meteofrance_archive([_BAD_JSON_LINE, _ARAVIS_LINE], commit=False)
        assert result.bad_shape == 1
        assert result.would_upsert == 1


# ---------------------------------------------------------------------------
# triggered_by propagation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTriggeredBy:
    """triggered_by kwarg is stored on the PipelineRun row."""

    pytestmark = pytest.mark.usefixtures("_load_fr_regions")

    def test_triggered_by_default(self) -> None:
        """Default triggered_by is 'meteofrance-archive-admin'."""
        load_meteofrance_archive([_ARAVIS_LINE], commit=True)
        run = PipelineRun.objects.get()
        assert run.triggered_by == "meteofrance-archive-admin"

    def test_triggered_by_custom(self) -> None:
        """Custom triggered_by kwarg propagates to the PipelineRun row."""
        load_meteofrance_archive(
            [_ARAVIS_LINE], commit=True, triggered_by="admin upload"
        )
        run = PipelineRun.objects.get()
        assert run.triggered_by == "admin upload"

    def test_triggered_by_backfill_label(self) -> None:
        """The backfill script's triggered_by label is stored correctly."""
        load_meteofrance_archive(
            [_ARAVIS_LINE],
            commit=True,
            triggered_by="meteofrance-archive-backfill",
        )
        run = PipelineRun.objects.get()
        assert run.triggered_by == "meteofrance-archive-backfill"
