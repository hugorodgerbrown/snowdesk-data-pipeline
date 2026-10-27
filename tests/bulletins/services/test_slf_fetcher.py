"""
tests/bulletins/services/test_slf_fetcher.py — Tests for the slf_fetcher service.

Covers:
  - _get_region: returns seeded Region, raises UnknownRegionError otherwise
  - upsert_bulletin: creation, update, region linking
  - fetch_bulletin_page: HTTP call with mocked responses
  - run_slf_pipeline: full orchestration with mocked API pages
  - _log_caaml_shape and the page fetch's shape logging (SNOW-900)

The pure helpers this module used to hold — response unwrapping, timestamp
parsing, shape detection and the PDF URL — moved to apps.ingest.normalise.slf
with their tests (tests/ingest/normalise/test_slf.py, SNOW-1248).
"""

import logging
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import override_settings

from apps.bulletins.models import (
    Bulletin,
    PipelineRun,
)
from apps.bulletins.services.slf_fetcher import (
    _log_caaml_shape,
    _resolve_base_url,
    fetch_bulletin_page,
    run_slf_pipeline,
    upsert_bulletin,
)
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
        # SNOW-1228: the persisted status says so too, not only the counter.
        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PARTIAL

    @patch("apps.bulletins.services.slf_fetcher.fetch_bulletin_page")
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


def _per_region_bulletin(
    index: int,
    **overrides: Any,
) -> dict[str, Any]:
    """
    Build a raw bulletin in SLF's 2026/27 one-region-per-bulletin shape.

    Args:
        index: Distinguishes bulletins within a page; used for both the
            bulletin ID and the region ID.
        **overrides: Additional keys merged into the bulletin dict.

    Returns:
        A raw bulletin dict carrying exactly one region.

    """
    return _make_raw_bulletin(
        bulletin_id=f"v5-{index:03d}",
        regions=[{"regionID": f"CH-{4000 + index}", "name": f"Region {index}"}],
        **overrides,
    )


class TestLogCaamlShape:
    """The changeover has to announce itself in the log."""

    def test_the_new_export_logs_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A per-region page is the event an operator must not miss."""
        page = [_per_region_bulletin(i) for i in range(133)]

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            _log_caaml_shape(page, "https://aws.slf.ch/api/bulletin-list/caaml")

        record = caplog.records[-1]
        assert record.levelno == logging.WARNING
        assert "shape=per-region" in record.getMessage()
        assert "bulletins=133" in record.getMessage()

    def test_todays_export_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        """An aggregated page is business as usual, and stays quiet."""
        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            _log_caaml_shape(
                [_make_raw_bulletin()], "https://aws.slf.ch/api/bulletin-list/caaml"
            )

        record = caplog.records[-1]
        assert record.levelno == logging.INFO
        assert "shape=aggregated" in record.getMessage()

    def test_the_base_url_is_in_the_message(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Shape and URL together, because the shape is not a property of it.

        Seeing both is what tells an operator whether pinning
        ``SLF_API_LEGACY_URL`` actually changed what arrived — SLF
        confirmed the legacy path preserves the format but not
        necessarily the aggregation.
        """
        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            _log_caaml_shape([_make_raw_bulletin()], "https://legacy.example/caaml")

        assert "https://legacy.example/caaml" in caplog.records[-1].getMessage()


class TestFetchBulletinPageClassifiesTheShape:
    """The fetch path classifies without changing what it returns."""

    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_a_new_shape_page_is_returned_unchanged(
        self, mock_get: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Detection is observation only — it never filters or rejects."""
        page = [_per_region_bulletin(i) for i in range(133)]
        mock_response = MagicMock()
        mock_response.json.return_value = page
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            result = fetch_bulletin_page("en", 50, 0)

        assert len(result) == 133
        assert result[0]["bulletinID"] == "v5-000"
        assert any(
            "shape=per-region" in record.getMessage() for record in caplog.records
        )


# ---------------------------------------------------------------------------
# _resolve_base_url — SNOW-900
# ---------------------------------------------------------------------------


class TestResolveBaseUrl:
    """Which endpoint one SLF fetch reads.

    These sit at the fetcher rather than at ``fetch_bulletins`` on
    purpose. The pin first shipped in the command, which left
    ``BulletinAdmin.backfill_view`` — the other caller of
    ``run_slf_pipeline``, and one that passes no ``base_url`` — reading
    the live URL straight past a configured rollback.
    """

    def test_no_pin_uses_the_live_api(self) -> None:
        """The default is an empty pin, so nothing changes until it is set."""
        assert _resolve_base_url(None) == "https://aws.slf.ch/api/bulletin-list/caaml"

    @override_settings(SLF_API_LEGACY_URL="https://aws.slf.ch/api/bulletin/caaml/v3")
    def test_a_set_pin_replaces_the_live_api(self) -> None:
        """A non-empty setting is the whole switch — no flag to remember."""
        assert _resolve_base_url(None) == "https://aws.slf.ch/api/bulletin/caaml/v3"

    @override_settings(SLF_API_LEGACY_URL="https://aws.slf.ch/api/bulletin/caaml/v3")
    def test_an_explicit_url_still_wins(self) -> None:
        """``--local-mirror`` passes one, and must outrank an ambient pin.

        Otherwise a pin left set in a dev ``.env`` would silently send
        mirror runs at the live SLF API.
        """
        assert _resolve_base_url("http://localhost:8000/dev/slf-mirror") == (
            "http://localhost:8000/dev/slf-mirror"
        )

    @override_settings(SLF_API_LEGACY_URL="https://aws.slf.ch/api/bulletin/caaml/v3")
    def test_the_pin_is_logged_every_time(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An emergency lever pointed at a doomed endpoint stays loud."""
        with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
            _resolve_base_url(None)

        record = caplog.records[-1]
        assert record.levelno == logging.WARNING
        assert "SLF_API_LEGACY_URL" in record.getMessage()

    @override_settings(SLF_API_LEGACY_URL="https://aws.slf.ch/api/bulletin/caaml/v3")
    @patch("apps.bulletins.services.slf_fetcher.requests.get")
    def test_the_pin_reaches_a_caller_that_passes_no_url(
        self, mock_get: MagicMock
    ) -> None:
        """The admin backfill's shape: ``run_slf_pipeline`` with no base_url.

        Asserted through ``fetch_bulletin_page`` rather than the resolver
        alone, because the defect this guards was a caller reaching the
        request URL without passing through the pin at all.
        """
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_bulletin_page("en", 50, 0)

        assert mock_get.call_args[0][0] == (
            "https://aws.slf.ch/api/bulletin/caaml/v3/en/json"
        )
