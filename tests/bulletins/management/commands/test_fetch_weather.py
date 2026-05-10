"""
tests/bulletins/management/commands/test_fetch_weather.py — Tests for fetch_weather.

Covers:
  - Default date (today) when --date is omitted.
  - --date YYYY-MM-DD overrides the default.
  - Read-only by default (commit=False forwarded to service).
  - --commit forwards commit=True to the service.
  - Banner content (READ-ONLY flag, date, region count).
  - Raises CommandError with non-zero exit when failed > 0.
  - Success output when commit=True.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import MicroRegionFactory

PATCH_TARGET = "bulletins.management.commands.fetch_weather.fetch_all_regions"


def _make_counts(
    created: int = 0,
    updated: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> dict[str, int]:
    """Build a fetch_all_regions-style result dict."""
    return {
        "created": created,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
    }


@pytest.mark.django_db
class TestFetchWeatherCommand:
    """Tests for the fetch_weather management command."""

    # ------------------------------------------------------------------
    # Default date
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_default_date_is_today(self, mock_fetch: MagicMock) -> None:
        """Without --date, the command uses today's local date."""
        mock_fetch.return_value = _make_counts()

        with patch(
            "bulletins.management.commands.fetch_weather.timezone.localdate",
            return_value=date(2026, 5, 1),
        ):
            call_command("fetch_weather")

        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        assert call_args[0][0] == date(2026, 5, 1)

    @patch(PATCH_TARGET)
    def test_explicit_date_overrides_default(self, mock_fetch: MagicMock) -> None:
        """--date YYYY-MM-DD is forwarded to fetch_all_regions."""
        mock_fetch.return_value = _make_counts()

        # call_command with a keyword arg that has a type= parser requires passing
        # the already-parsed value (a date object) not a raw string.
        call_command("fetch_weather", date=date(2026, 4, 1))

        call_args = mock_fetch.call_args
        assert call_args[0][0] == date(2026, 4, 1)

    # ------------------------------------------------------------------
    # commit flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_read_only_by_default(self, mock_fetch: MagicMock) -> None:
        """Without --commit, commit=False is forwarded to fetch_all_regions."""
        mock_fetch.return_value = _make_counts()

        call_command("fetch_weather")

        call_args = mock_fetch.call_args
        assert call_args[1]["commit"] is False

    @patch(PATCH_TARGET)
    def test_commit_flag_forwards_commit_true(self, mock_fetch: MagicMock) -> None:
        """--commit causes commit=True to be forwarded to fetch_all_regions."""
        mock_fetch.return_value = _make_counts(created=3)

        call_command("fetch_weather", commit=True)

        call_args = mock_fetch.call_args
        assert call_args[1]["commit"] is True

    # ------------------------------------------------------------------
    # Banner content
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_banner_contains_read_only_when_not_committed(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows [READ-ONLY] when --commit is not passed."""
        mock_fetch.return_value = _make_counts()

        with patch(
            "bulletins.management.commands.fetch_weather.timezone.localdate",
            return_value=date(2026, 5, 1),
        ):
            call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "READ-ONLY" in out
        assert "2026-05-01" in out

    @patch(PATCH_TARGET)
    def test_banner_omits_read_only_when_committed(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner does not show [READ-ONLY] when --commit is passed."""
        mock_fetch.return_value = _make_counts(created=1)

        call_command("fetch_weather", commit=True)

        out = capsys.readouterr().out
        assert "READ-ONLY" not in out

    @patch(PATCH_TARGET)
    def test_banner_includes_region_count(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes the total number of regions."""
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()

        call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "2 region" in out

    # ------------------------------------------------------------------
    # Failure exit code
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_raises_command_error_on_failures(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        """CommandError is raised when failed > 0 (non-zero exit)."""
        mock_fetch.return_value = _make_counts(failed=2, created=1)

        with pytest.raises(CommandError, match="2 region failure"):
            call_command("fetch_weather", commit=True)

    @patch(PATCH_TARGET)
    def test_no_command_error_on_success(
        self,
        mock_fetch: MagicMock,
    ) -> None:
        """No CommandError when failed == 0."""
        mock_fetch.return_value = _make_counts(created=5)

        # Should not raise.
        call_command("fetch_weather", commit=True)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_success_output_shows_counts(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=True, stdout shows created/updated/skipped/failed counts."""
        mock_fetch.return_value = _make_counts(created=4, updated=1, skipped=2)

        call_command("fetch_weather", commit=True)

        out = capsys.readouterr().out
        assert "4 created" in out
        assert "1 updated" in out
        assert "2 skipped" in out

    @patch(PATCH_TARGET)
    def test_read_only_output_prompts_commit(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=False, stdout tells the user to pass --commit."""
        mock_fetch.return_value = _make_counts()

        call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "--commit" in out
