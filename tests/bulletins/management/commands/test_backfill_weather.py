"""
tests/bulletins/management/commands/test_backfill_weather.py — Tests for backfill_weather.

Covers:
  - Required --start and --end flags (CommandError if missing).
  - Validates end >= start (CommandError otherwise).
  - Read-only by default (commit=False forwarded to service).
  - --commit forwards commit=True to the service.
  - Banner content (date range, day count, region count, READ-ONLY, SOURCE, STASH flags).
  - Raises CommandError with non-zero exit when failed > 0.
  - --delay defaults to 1.0 s and is forwarded to the service; --delay 0
    disables pacing.
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
        MicroRegionFactory.create()
        MicroRegionFactory.create()
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

    @patch(PATCH_TARGET)
    def test_default_delay_is_one_second(self, mock_backfill: MagicMock) -> None:
        """Without an explicit --delay, the default 1.0 is forwarded to the service."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
        )

        assert mock_backfill.call_args[1]["delay"] == 1.0

    @patch(PATCH_TARGET)
    def test_explicit_delay_forwarded(self, mock_backfill: MagicMock) -> None:
        """--delay is forwarded verbatim to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            delay=2.5,
        )

        assert mock_backfill.call_args[1]["delay"] == 2.5

    @patch(PATCH_TARGET)
    def test_zero_delay_forwarded(self, mock_backfill: MagicMock) -> None:
        """--delay 0 disables pacing and forwards delay=0.0 to the service."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            delay=0,
        )

        assert mock_backfill.call_args[1]["delay"] == 0.0

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

    # ------------------------------------------------------------------
    # --source flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_source_live_passes_none_base_url(self, mock_backfill: MagicMock) -> None:
        """--source live passes base_url=None to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        call_command(
            "backfill_weather",
            start=date(2026, 4, 1),
            end=date(2026, 4, 30),
            source="live",
        )

        assert mock_backfill.call_args[1]["base_url"] is None

    @patch(PATCH_TARGET)
    def test_source_local_mirror_passes_configured_url(
        self, mock_backfill: MagicMock
    ) -> None:
        """--source local-mirror passes WEATHER_API_LOCAL_MIRROR_BASE_URL as base_url."""
        mock_backfill.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                source="local-mirror",
            )

        assert mock_backfill.call_args[1]["base_url"] == mirror_url

    @patch(PATCH_TARGET)
    def test_source_local_mirror_raises_when_setting_missing(
        self, mock_backfill: MagicMock
    ) -> None:
        """--source local-mirror raises CommandError when setting is not configured."""
        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=None):
            with pytest.raises(CommandError, match="WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                call_command(
                    "backfill_weather",
                    start=date(2026, 4, 1),
                    end=date(2026, 4, 30),
                    source="local-mirror",
                )

        mock_backfill.assert_not_called()

    @patch(PATCH_TARGET)
    def test_source_local_mirror_shown_in_banner(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes SOURCE=LOCAL-MIRROR when --source local-mirror is passed."""
        mock_backfill.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                source="local-mirror",
            )

        out = capsys.readouterr().out
        assert "LOCAL-MIRROR" in out

    # ------------------------------------------------------------------
    # --stash flag
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_stash_writes_records_to_archive(
        self, mock_backfill: MagicMock, tmp_path: Path
    ) -> None:
        """--stash causes on_fetched records to be merged into the archive."""
        region = MicroRegionFactory.create()
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_backfill_all_regions(
            start_date: date,
            end_date: date,
            *,
            commit: bool,
            delay: float,
            base_url: Any,
            on_fetched: Any,
        ) -> dict[str, int]:
            if on_fetched is not None:
                on_fetched(
                    {
                        "region_id": region.region_id,
                        "date": "2026-04-01",
                        "weather_code": 2,
                        "sunrise": "2026-04-01T06:00+02:00",
                        "sunset": "2026-04-01T20:00+02:00",
                        "captured_at": "2026-05-09T12:00:00Z",
                    }
                )
            return _make_counts()

        mock_backfill.side_effect = fake_backfill_all_regions

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                stash=True,
            )

        records = list(read_archive(archive_path))
        assert len(records) == 1
        assert records[0]["region_id"] == region.region_id
        assert records[0]["date"] == "2026-04-01"

    @patch(PATCH_TARGET)
    def test_stash_without_commit_leaves_db_unchanged(
        self, mock_backfill: MagicMock, tmp_path: Path
    ) -> None:
        """--stash without --commit writes the archive but not the database."""
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_backfill_all_regions(
            start_date: date,
            end_date: date,
            *,
            commit: bool,
            delay: float,
            base_url: Any,
            on_fetched: Any,
        ) -> dict[str, int]:
            if on_fetched is not None:
                on_fetched(
                    {
                        "region_id": "CH-TEST",
                        "date": "2026-04-01",
                        "weather_code": 0,
                        "sunrise": "2026-04-01T06:00+02:00",
                        "sunset": "2026-04-01T20:00+02:00",
                        "captured_at": "2026-05-09T12:00:00Z",
                    }
                )
            return _make_counts()

        mock_backfill.side_effect = fake_backfill_all_regions

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                stash=True,
                commit=False,
            )

        # Archive was populated.
        records = list(read_archive(archive_path))
        assert len(records) == 1
        # But commit=False was forwarded.
        assert mock_backfill.call_args[1]["commit"] is False

    @patch(PATCH_TARGET)
    def test_stash_shown_in_banner(
        self,
        mock_backfill: MagicMock,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Banner includes STASH when --stash is passed."""
        archive_path = tmp_path / "om_archive.ndjson"
        mock_backfill.return_value = _make_counts()

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            call_command(
                "backfill_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                stash=True,
            )

        out = capsys.readouterr().out
        assert "STASH" in out
