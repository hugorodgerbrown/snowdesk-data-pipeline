"""
tests/bulletins/management/commands/test_fetch_weather.py — Tests for fetch_weather.

Covers:
  - Default date (today) when --date is omitted.
  - --date YYYY-MM-DD overrides the default.
  - Read-only by default (commit=False forwarded to service).
  - --commit forwards commit=True to the service.
  - Banner content (READ-ONLY flag, date, region count, SOURCE flag, STASH flag).
  - Raises CommandError with non-zero exit when failed > 0.
  - Success output when commit=True.
  - --source live: base_url=None forwarded.
  - --source local-mirror: base_url from settings forwarded; CommandError when
    setting is missing.
  - --stash: writes records to the archive; DB unchanged when commit=False.
"""

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from bulletins.services.openmeteo_archive import read_archive
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

    # ------------------------------------------------------------------
    # --source flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_source_live_passes_none_base_url(self, mock_fetch: MagicMock) -> None:
        """--source live passes base_url=None to fetch_all_regions."""
        mock_fetch.return_value = _make_counts()

        call_command("fetch_weather", source="live")

        assert mock_fetch.call_args[1]["base_url"] is None

    @patch(PATCH_TARGET)
    def test_source_local_mirror_passes_configured_url(
        self, mock_fetch: MagicMock
    ) -> None:
        """--source local-mirror passes WEATHER_API_LOCAL_MIRROR_BASE_URL as base_url."""
        mock_fetch.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            call_command("fetch_weather", source="local-mirror")

        assert mock_fetch.call_args[1]["base_url"] == mirror_url

    @patch(PATCH_TARGET)
    def test_source_local_mirror_raises_when_setting_missing(
        self, mock_fetch: MagicMock
    ) -> None:
        """--source local-mirror raises CommandError when the setting is not configured."""
        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=None):
            with pytest.raises(CommandError, match="WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                call_command("fetch_weather", source="local-mirror")

        mock_fetch.assert_not_called()

    @patch(PATCH_TARGET)
    def test_source_local_mirror_shown_in_banner(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes SOURCE=LOCAL-MIRROR when --source local-mirror is passed."""
        mock_fetch.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            call_command("fetch_weather", source="local-mirror")

        out = capsys.readouterr().out
        assert "LOCAL-MIRROR" in out

    # ------------------------------------------------------------------
    # --stash flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_stash_writes_records_to_archive(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        """--stash causes on_fetched records to be merged into the archive."""
        region = MicroRegionFactory.create()
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_fetch_all_regions(
            target_date: date,
            *,
            commit: bool,
            base_url: Any,
            on_fetched: Any,
        ) -> dict[str, int]:
            if on_fetched is not None:
                on_fetched(
                    {
                        "region_id": region.region_id,
                        "date": "2026-05-01",
                        "weather_code": 3,
                        "sunrise": "2026-05-01T05:32+02:00",
                        "sunset": "2026-05-01T20:14+02:00",
                        "captured_at": "2026-05-09T12:00:00Z",
                    }
                )
            return _make_counts()

        mock_fetch.side_effect = fake_fetch_all_regions

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command("fetch_weather", stash=True)

        records = list(read_archive(archive_path))
        assert len(records) == 1
        assert records[0]["region_id"] == region.region_id
        assert records[0]["date"] == "2026-05-01"

    @patch(PATCH_TARGET)
    def test_stash_without_commit_leaves_db_unchanged(
        self, mock_fetch: MagicMock, tmp_path: Path
    ) -> None:
        """--stash without --commit writes the archive but not the database."""
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_fetch_all_regions(
            target_date: date,
            *,
            commit: bool,
            base_url: Any,
            on_fetched: Any,
        ) -> dict[str, int]:
            if on_fetched is not None:
                on_fetched(
                    {
                        "region_id": "CH-TEST",
                        "date": "2026-05-01",
                        "weather_code": 1,
                        "sunrise": "2026-05-01T05:32+02:00",
                        "sunset": "2026-05-01T20:14+02:00",
                        "captured_at": "2026-05-09T12:00:00Z",
                    }
                )
            return _make_counts()

        mock_fetch.side_effect = fake_fetch_all_regions

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command("fetch_weather", stash=True, commit=False)

        # Archive was populated.
        records = list(read_archive(archive_path))
        assert len(records) == 1
        # But commit=False was forwarded.
        assert mock_fetch.call_args[1]["commit"] is False

    @patch(PATCH_TARGET)
    def test_stash_shown_in_banner(
        self,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Banner includes STASH when --stash is passed."""
        archive_path = tmp_path / "om_archive.ndjson"
        mock_fetch.return_value = _make_counts()

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command("fetch_weather", stash=True)

        out = capsys.readouterr().out
        assert "STASH" in out
