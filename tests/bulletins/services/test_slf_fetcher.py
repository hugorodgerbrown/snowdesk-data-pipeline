"""
tests/bulletins/services/test_slf_fetcher.py — Tests for the slf_fetcher service.

Covers run_slf_pipeline: full orchestration with mocked API pages.

The rest moved with the code: the pure helpers to
tests/ingest/normalise/test_slf.py (SNOW-1248), the HTTP to
tests/ingest/sources/test_slf.py and upsert_bulletin to
tests/bulletins/services/test_apply.py (SNOW-1249).
"""

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from apps.bulletins.models import (
    Bulletin,
    PipelineRun,
)
from apps.bulletins.services.apply import upsert_bulletin
from apps.bulletins.services.slf_fetcher import run_slf_pipeline
from apps.ingest.normalise.slf import slf_pdf_url
from apps.ingest.public import content_hash
from tests.factories import MicroRegionFactory, PipelineRunFactory

# The ``slf_fetcher`` logger, named once rather than spelled out at each
# ``caplog.at_level`` call site.
_LOGGER_NAME = "apps.bulletins.services.slf_fetcher"


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


@pytest.fixture
def _seed_test_regions(db: Any) -> None:
    """Seed the regions referenced by ``_make_raw_bulletin`` defaults.

    The standard test bulletin covers ``CH-4115`` and ``CH-7111``; other
    tests add ``CH-9999``. Regions are now fixture-backed (no
    auto-creation), so the test must pre-create them.
    """
    for rid in ("CH-4115", "CH-7111", "CH-9999"):
        MicroRegionFactory.create(region_id=rid, name=f"Test {rid}")


# ---------------------------------------------------------------------------
# run_slf_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunPipeline:
    """Tests for run_slf_pipeline."""

    pytestmark = pytest.mark.usefixtures("_seed_test_regions")

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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
        # SNOW-1228: the persisted status says so too, not only the counter.
        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PARTIAL

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_a_grouping_failure_finishes_the_run_partial(
        self, mock_fetch: MagicMock
    ) -> None:
        """SNOW-1228: a failed grouping recompute makes the run PARTIAL."""
        mock_fetch.return_value = [_make_raw_bulletin("b1", "2025-03-15T08:00:00Z")]

        with patch(
            "apps.bulletins.services.apply.compute_bulletin_grouping_boundary",
            side_effect=RuntimeError("geometry exploded"),
        ):
            run = run_slf_pipeline(
                start=date(2025, 3, 15),
                end=date(2025, 3, 15),
                triggered_by="test",
            )

        run.refresh_from_db()
        assert run.records_failed == 1
        assert run.records_created == 1
        assert run.status == PipelineRun.Status.PARTIAL

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_skips_existing_without_force(self, mock_fetch: MagicMock) -> None:
        """Without --force, a bulletin already stored as fetched is skipped."""
        # Pre-create the bulletin exactly as a fetch would have written it.
        pre_run = PipelineRunFactory.create()
        raw = _make_raw_bulletin("existing", "2025-03-15T08:00:00Z")
        upsert_bulletin(raw, pre_run, pdf_url=slf_pdf_url(raw))

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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_a_changed_bulletin_is_updated_without_force(
        self, mock_fetch: MagicMock
    ) -> None:
        """SNOW-1250: a stored id whose content changed is applied, no --force."""
        pre_run = PipelineRunFactory.create()
        raw = _make_raw_bulletin("existing", "2025-03-15T08:00:00Z")
        upsert_bulletin(raw, pre_run, pdf_url=slf_pdf_url(raw))
        changed = {**raw, "lang": "de"}
        mock_fetch.return_value = [changed]

        run = run_slf_pipeline(
            start=date(2025, 3, 15), end=date(2025, 3, 15), triggered_by="test"
        )

        assert (run.records_created, run.records_updated) == (0, 1)
        stored = Bulletin.objects.get(bulletin_id="existing")
        assert stored.lang == "de"
        assert stored.content_hash == content_hash(changed)

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_a_conflicting_bulletin_id_fails_the_run(
        self, mock_fetch: MagicMock
    ) -> None:
        """SNOW-998: a reused id stops the walk and is never written twice.

        The first copy is already applied when the second arrives; the run
        is FAILED, which makes ``fetch_bulletins`` exit non-zero, and the
        conflicting copy does not overwrite it.
        """
        first = _make_raw_bulletin(
            "shared",
            "2025-03-15T08:00:00Z",
            regions=[{"regionID": "CH-4115", "name": "Piz Buin"}],
        )
        second = _make_raw_bulletin(
            "shared",
            "2025-03-15T08:00:00Z",
            regions=[{"regionID": "CH-7111", "name": "Engadin"}],
        )
        mock_fetch.return_value = [first, second]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.FAILED
        assert "shared" in run.error_message
        bulletin = Bulletin.objects.get(bulletin_id="shared")
        assert list(
            bulletin.region_links.values_list("region__region_id", flat=True)
        ) == ["CH-4115"]

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_an_identical_repeat_is_counted_once(self, mock_fetch: MagicMock) -> None:
        """Paging overlap: the repeat is a duplicate, not a second write."""
        bulletin = _make_raw_bulletin("b1", "2025-03-15T08:00:00Z")
        mock_fetch.return_value = [bulletin, dict(bulletin)]

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="test",
        )

        assert run.status == PipelineRun.Status.SUCCESS
        assert (run.records_created, run.records_updated) == (1, 0)
        assert Bulletin.objects.count() == 1

    @patch("apps.ingest.sources.slf.PAGE_SIZE", 1)
    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
    def test_run_records_triggered_by(self, mock_fetch: MagicMock) -> None:
        """The triggered_by label is stored on the PipelineRun."""
        mock_fetch.return_value = []

        run = run_slf_pipeline(
            start=date(2025, 3, 15),
            end=date(2025, 3, 15),
            triggered_by="fetch_bulletins command",
        )

        assert run.triggered_by == "fetch_bulletins command"

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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

    @patch("apps.ingest.sources.slf.fetch_bulletin_page")
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
