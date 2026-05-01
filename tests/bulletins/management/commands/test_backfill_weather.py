"""
tests/bulletins/management/commands/test_backfill_weather.py — Tests for backfill_weather.

Covers:
  - Required --start and --end flags (CommandError if missing).
  - Validates end >= start (CommandError otherwise).
  - Read-only by default (commit=False forwarded to service).
  - --commit forwards commit=True to the service.
  - Banner content (date range, day count, region count, READ-ONLY flag).
  - Raises CommandError with non-zero exit when failed > 0.
  - --delay is accepted as a non-negative float.
  - Success output when commit=True.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import RegionFactory

PATCH_TARGET = "bulletins.management.commands.backfill_weather.backfill_all_regions"


def _make_counts(
    created: int = 0,
    updated: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> dict[str, int]:
    """Build a backfill_all_regions-style result dict."""
    return {
        "created": created,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
    }


@pytest.mark.django_db
class TestBackfillWeatherCommand:
    """Tests for the backfill_weather management command."""

    # ------------------------------------------------------------------
    # Required arguments
    # ------------------------------------------------------------------

    def test_missing_start_raises(self) -> None:
        """--start is required; omitting it raises a CommandError."""
        with pytest.raises(CommandError, match="required"):
            call_command("backfill_weather", end=date(2026, 5, 1))

    def test_missing_end_raises(self) -> None:
        """--end is required; omitting it raises a CommandError."""
        with pytest.raises(CommandError, match="required"):
            call_command("backfill_weather", start=date(2026, 4, 1))

    # ------------------------------------------------------------------
    # Date validation
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_end_before_start_raises_command_error(
        self, mock_backfill: MagicMock
    ) -> None:
        """--end before --start raises CommandError."""
        with pytest.raises(CommandError, match="--end must be on or after --start"):
            call_command(
                "backfill_weather",
                start=date(2026, 5, 2),
                end=date(2026, 5, 1),
            )
        mock_backfill.assert_not_called()

    @patch(PATCH_TARGET)
    def test_same_start_and_end_is_allowed(self, mock_backfill: MagicMock) -> None:
        """--start == --end (single day) is valid."""
        mock_backfill.return_value = _make_counts()
        # Should not raise.
        call_command(
            "backfill_weather",
            start=date(2026, 5, 1),
            end=date(2026, 5, 1),
        )

    # ------------------------------------------------------------------
    # commit flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_read_only_by_default(self, mock_backfill: MagicMock) -> None:
        """Without --commit, commit=False is forwarded to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        call_args = mock_backfill.call_args
        assert call_args[1]["commit"] is False

    @patch(PATCH_TARGET)
    def test_commit_flag_forwards_commit_true(self, mock_backfill: MagicMock) -> None:
        """--commit causes commit=True to be forwarded to backfill_all_regions."""
        mock_backfill.return_value = _make_counts(created=30)

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            commit=True,
        )

        call_args = mock_backfill.call_args
        assert call_args[1]["commit"] is True

    # ------------------------------------------------------------------
    # Date range forwarding
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_start_and_end_forwarded_to_service(self, mock_backfill: MagicMock) -> None:
        """--start and --end are forwarded as date objects to the service."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        call_args = mock_backfill.call_args
        assert call_args[0][0] == date(2026, 4, 1)
        assert call_args[0][1] == date(2026, 4, 30)

    # ------------------------------------------------------------------
    # Banner content
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_banner_shows_read_only_flag(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows [READ-ONLY] when --commit is not passed."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        out = capsys.readouterr().out
        assert "READ-ONLY" in out
        assert "2026-04-01" in out
        assert "2026-04-30" in out

    @patch(PATCH_TARGET)
    def test_banner_includes_day_count(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows the number of days in the range."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        out = capsys.readouterr().out
        assert "30 day" in out

    @patch(PATCH_TARGET)
    def test_banner_includes_region_count(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes the number of Region rows."""
        RegionFactory.create()
        RegionFactory.create()
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        out = capsys.readouterr().out
        assert "2 region" in out

    @patch(PATCH_TARGET)
    def test_banner_omits_read_only_when_committed(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner does not show [READ-ONLY] when --commit is passed."""
        mock_backfill.return_value = _make_counts(created=5)

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 1),
            commit=True,
        )

        out = capsys.readouterr().out
        assert "READ-ONLY" not in out

    # ------------------------------------------------------------------
    # Failure exit code
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_raises_command_error_on_failures(
        self,
        mock_backfill: MagicMock,
    ) -> None:
        """CommandError is raised (non-zero exit) when failed > 0."""
        mock_backfill.return_value = _make_counts(failed=3, created=2)

        with pytest.raises(CommandError, match="3 region failure"):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                commit=True,
            )

    @patch(PATCH_TARGET)
    def test_no_command_error_on_success(
        self,
        mock_backfill: MagicMock,
    ) -> None:
        """No CommandError when failed == 0."""
        mock_backfill.return_value = _make_counts(created=10)

        # Should not raise.
        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            commit=True,
        )

    # ------------------------------------------------------------------
    # --delay flag
    # ------------------------------------------------------------------

    @patch("bulletins.management.commands.backfill_weather.time.sleep")
    @patch("bulletins.management.commands.backfill_weather.fetch_archive_for_region")
    def test_delay_flag_invokes_sleep_between_regions(
        self,
        mock_archive: MagicMock,
        mock_sleep: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--delay causes sleep() to be called between region archive calls."""
        # Two regions with centres.
        RegionFactory.create()
        RegionFactory.create()
        mock_archive.return_value = []

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 1),
            delay=0.5,
        )

        # Sleep should be called once (between the two regions, not after the last).
        mock_sleep.assert_called_once_with(0.5)

    @patch(PATCH_TARGET)
    def test_no_delay_does_not_sleep(self, mock_backfill: MagicMock) -> None:
        """Without --delay, the direct backfill_all_regions path is used (no sleep)."""
        mock_backfill.return_value = _make_counts()

        with patch(
            "bulletins.management.commands.backfill_weather.time.sleep"
        ) as mock_sleep:
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 1),
            )

        mock_sleep.assert_not_called()

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_success_output_shows_counts(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=True, stdout shows created/updated/skipped/failed counts."""
        mock_backfill.return_value = _make_counts(created=10, updated=3, skipped=1)

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            commit=True,
        )

        out = capsys.readouterr().out
        assert "10 created" in out
        assert "3 updated" in out
        assert "1 skipped" in out

    @patch(PATCH_TARGET)
    def test_read_only_output_prompts_commit(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=False, stdout tells the user to pass --commit."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        out = capsys.readouterr().out
        assert "--commit" in out
