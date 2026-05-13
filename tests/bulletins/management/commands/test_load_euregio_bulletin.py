"""
tests/bulletins/management/commands/test_load_euregio_bulletin.py — Tests for
load_euregio_bulletin.

Covers the dry-run (default) and --commit paths, unknown-region skipping,
HTTP error handling, and the exit-code path when bulletins fail.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError

from bulletins.management.commands.load_euregio_bulletin import fetch_euregio_bulletins

PATCH_FETCH = (
    "bulletins.management.commands.load_euregio_bulletin.fetch_euregio_bulletins"
)
PATCH_UPSERT = "bulletins.management.commands.load_euregio_bulletin.upsert_bulletin"


def _make_raw_bulletin(
    bulletin_id: str = "euregio-2026-01-01",
    region_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Build a minimal EUREGIO raw bulletin dict (pre-GeoJSON envelope).

    Args:
        bulletin_id: The bulletinID to use.
        region_ids: List of regionID strings to include.

    Returns:
        A raw bulletin dict suitable for upsert_bulletin.

    """
    return {
        "bulletinID": bulletin_id,
        "publicationTime": "2026-01-01T08:00:00+00:00",
        "validTime": {
            "startTime": "2026-01-01T07:00:00+00:00",
            "endTime": "2026-01-01T23:00:00+00:00",
        },
        "regions": [
            {"regionID": rid, "name": f"Region {rid}"}
            for rid in (region_ids or ["AT-07-01"])
        ],
        "dangerRatings": [{"mainValue": "moderate", "validTimePeriod": "all_day"}],
        "avalancheProblems": [],
        "customData": {"ALBINA": {}},
        "lang": "en",
    }


# ---------------------------------------------------------------------------
# fetch_euregio_bulletins unit tests
# ---------------------------------------------------------------------------


class TestFetchEuregioBulletins:
    """Unit tests for the fetch_euregio_bulletins helper."""

    def test_flat_list_response_returned_directly(self) -> None:
        """A flat JSON list is returned as-is."""
        raw = [_make_raw_bulletin()]
        mock_response = MagicMock()
        mock_response.json.return_value = raw

        with patch("requests.get", return_value=mock_response):
            result = fetch_euregio_bulletins("https://example.com/api")

        assert result == raw

    def test_dict_with_bulletins_key_unwrapped(self) -> None:
        """A dict with a 'bulletins' key is unwrapped."""
        raw = [_make_raw_bulletin()]
        mock_response = MagicMock()
        mock_response.json.return_value = {"bulletins": raw, "meta": {}}

        with patch("requests.get", return_value=mock_response):
            result = fetch_euregio_bulletins("https://example.com/api")

        assert result == raw

    def test_unexpected_shape_returns_empty_list(self) -> None:
        """An unrecognised JSON shape returns []."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"unexpected": "shape"}

        with patch("requests.get", return_value=mock_response):
            result = fetch_euregio_bulletins("https://example.com/api")

        assert result == []

    def test_http_error_propagated(self) -> None:
        """An HTTP error from requests is re-raised."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404")

        with patch("requests.get", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                fetch_euregio_bulletins("https://example.com/api")


# ---------------------------------------------------------------------------
# Command: dry-run (default)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoadEuregioBulletinDryRun:
    """Dry-run (no --commit) mode tests."""

    @patch(PATCH_FETCH)
    def test_no_commit_prints_dry_run_report(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without --commit, the command prints a dry-run summary and returns."""
        mock_fetch.return_value = [_make_raw_bulletin()]

        call_command("load_euregio_bulletin")

        captured = capsys.readouterr()
        assert "dry-run" in captured.out
        assert "Pass --commit" in captured.out

    @patch(PATCH_FETCH)
    @patch(PATCH_UPSERT)
    def test_no_commit_does_not_call_upsert(
        self,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Without --commit, upsert_bulletin is never called."""
        mock_fetch.return_value = [_make_raw_bulletin()]

        call_command("load_euregio_bulletin")

        mock_upsert.assert_not_called()

    @patch(PATCH_FETCH)
    def test_empty_api_response_prints_warning(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An empty API response prints a warning."""
        mock_fetch.return_value = []

        call_command("load_euregio_bulletin")

        captured = capsys.readouterr()
        assert "No bulletins" in captured.out

    @patch(PATCH_FETCH)
    def test_http_error_raises_command_error(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        """An HTTP error from the API raises CommandError."""
        mock_fetch.side_effect = requests.HTTPError("500 Server Error")

        with pytest.raises(CommandError, match="Failed to fetch EUREGIO bulletins"):
            call_command("load_euregio_bulletin")


# ---------------------------------------------------------------------------
# Command: --commit path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoadEuregioBulletinCommit:
    """--commit mode tests."""

    @patch(PATCH_FETCH)
    @patch(PATCH_UPSERT)
    def test_commit_calls_upsert_for_each_bulletin(
        self,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """With --commit, upsert_bulletin is called once per bulletin."""
        mock_fetch.return_value = [
            _make_raw_bulletin("b1"),
            _make_raw_bulletin("b2"),
        ]
        mock_upsert.return_value = True  # created

        call_command("load_euregio_bulletin", commit=True)

        assert mock_upsert.call_count == 2

    @patch(PATCH_FETCH)
    @patch(PATCH_UPSERT)
    def test_commit_prints_success_summary(
        self,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--commit prints created / updated / skipped / failed counts."""
        mock_fetch.return_value = [_make_raw_bulletin("b1")]
        mock_upsert.return_value = True

        call_command("load_euregio_bulletin", commit=True)

        captured = capsys.readouterr()
        assert "Done:" in captured.out
        assert "created" in captured.out

    @patch(PATCH_FETCH)
    @patch(PATCH_UPSERT)
    def test_unknown_region_skipped_not_exception(
        self,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unknown region causes a warning + CommandError, not an uncaught exception."""
        from bulletins.services.data_fetcher import UnknownRegionError

        mock_fetch.return_value = [_make_raw_bulletin("bad-region-bulletin")]
        mock_upsert.side_effect = UnknownRegionError("AT-99-01 not seeded")

        with pytest.raises(CommandError, match="failed to import"):
            call_command("load_euregio_bulletin", commit=True)

        captured = capsys.readouterr()
        assert "unknown region" in captured.out.lower()

    @patch(PATCH_FETCH)
    def test_force_skips_existence_check(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        """--force passes through to upsert without the existence check."""
        mock_fetch.return_value = [_make_raw_bulletin("force-id")]

        with patch(PATCH_UPSERT, return_value=True) as mock_upsert:
            call_command("load_euregio_bulletin", commit=True, force=True)

        mock_upsert.assert_called_once()

    @patch(PATCH_FETCH)
    @patch(PATCH_UPSERT)
    def test_partial_failure_marks_pipeline_run_failed(
        self,
        mock_upsert: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """When some bulletins fail, PipelineRun ends with FAILED status."""
        from bulletins.models import PipelineRun
        from bulletins.services.data_fetcher import UnknownRegionError

        # One bulletin succeeds, one has an unknown region (fails).
        mock_fetch.return_value = [
            _make_raw_bulletin("good-bulletin"),
            _make_raw_bulletin("bad-bulletin"),
        ]
        mock_upsert.side_effect = [True, UnknownRegionError("AT-99-01 not seeded")]

        with pytest.raises(CommandError, match="failed to import"):
            call_command("load_euregio_bulletin", commit=True)

        run = PipelineRun.objects.order_by("-started_at").first()
        assert run is not None
        assert run.status == PipelineRun.Status.FAILED
