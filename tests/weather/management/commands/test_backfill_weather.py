"""
tests/weather/management/commands/test_backfill_weather.py — Tests for backfill_weather.

``backfill_weather`` is the only caller of the Open-Meteo archive endpoint.
It fills gaps in ``WeatherSnapshot`` and never rewrites a stored day. Most of
this file is the window/flag coverage that used to live in
``test_fetch_weather.py``, moved here with the flags themselves.

Covers:
  - Default window derivation (bulletins present → earliest bulletin
    valid_from; empty DB → SEASON_START_DATE) and the end-of-yesterday default.
  - The window may not reach today — today belongs to fetch_weather.
  - --start/--end explicit range; --date single day.
  - --date combined with --start or --end → CommandError; --end < --start →
    CommandError.
  - --local-mirror resolves the mirror base URL; raises CommandError when the
    setting is missing.
  - --delay default and forwarding.
  - --stash writes records to the archive without touching the DB.
  - failed > 0 → CommandError + non-zero exit.
  - --commit vs read-only, banner content, and the summary line.
"""

from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.weather.services.openmeteo_archive import read_archive
from tests.factories import BulletinFactory, MicroRegionFactory

PATCH_BACKFILL_ALL = (
    "apps.weather.management.commands.backfill_weather.backfill_all_regions"
)
PATCH_TODAY = "apps.weather.management.commands.backfill_weather.timezone.localdate"

TODAY = date(2026, 5, 1)
YESTERDAY = date(2026, 4, 30)


def _make_counts(
    created: int = 0,
    updated: int = 0,
    failed: int = 0,
    skipped: int = 0,
) -> dict[str, int]:
    """Build a service-function-style result dict."""
    return {
        "created": created,
        "updated": updated,
        "failed": failed,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Default window derivation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDefaultWindowDerivation:
    """Tests for the two-branch default start date and the default end."""

    @patch(PATCH_BACKFILL_ALL)
    def test_default_start_from_earliest_bulletin(
        self, mock_backfill: MagicMock
    ) -> None:
        """With bulletins present, start = earliest Bulletin.valid_from."""
        BulletinFactory.create(
            valid_from=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
            issued_at=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            valid_from=datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 21, 12, 0, tzinfo=UTC),
            issued_at=datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
        )
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather")

        assert mock_backfill.call_args[0][0] == date(2026, 1, 5)

    @patch(PATCH_BACKFILL_ALL)
    def test_default_start_from_season_start_date_when_db_empty(
        self, mock_backfill: MagicMock
    ) -> None:
        """With no bulletins, start = settings.SEASON_START_DATE."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather")

        assert mock_backfill.call_args[0][0] == settings.SEASON_START_DATE

    @patch(PATCH_BACKFILL_ALL)
    def test_default_end_is_yesterday(self, mock_backfill: MagicMock) -> None:
        """The window stops at yesterday — today is fetch_weather's."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather")

        assert mock_backfill.call_args[0][1] == YESTERDAY

    @patch(PATCH_BACKFILL_ALL)
    def test_never_keys_off_the_latest_stored_snapshot(
        self, mock_backfill: MagicMock
    ) -> None:
        """A stored snapshot does not move the window start.

        The retired routing command started at the latest stored date, which
        put that day inside the next run's archive range and rewrote it. Gap
        detection bounds the work now, so the window spans the whole archive
        and a fully-covered region simply costs no API call.
        """
        from tests.factories import WeatherSnapshotFactory

        region = MicroRegionFactory.create()
        WeatherSnapshotFactory.create(region=region, valid_for_date=date(2026, 4, 20))
        BulletinFactory.create(
            valid_from=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 1, 6, 12, 0, tzinfo=UTC),
            issued_at=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        )
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather")

        assert mock_backfill.call_args[0][0] == date(2026, 1, 5)


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArgumentValidation:
    """Mutually exclusive and out-of-order arguments raise CommandError."""

    def test_date_with_start_raises_command_error(self) -> None:
        """--date cannot be combined with --start."""
        with pytest.raises(CommandError, match="cannot be combined"):
            call_command(
                "backfill_weather", "--date", "2026-04-01", "--start", "2026-03-01"
            )

    def test_date_with_end_raises_command_error(self) -> None:
        """--date cannot be combined with --end."""
        with pytest.raises(CommandError, match="cannot be combined"):
            call_command(
                "backfill_weather", "--date", "2026-04-01", "--end", "2026-04-05"
            )

    @patch(PATCH_BACKFILL_ALL)
    def test_end_before_start_raises_command_error(
        self, mock_backfill: MagicMock
    ) -> None:
        """--end earlier than --start is rejected."""
        mock_backfill.return_value = _make_counts()

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="on or after"),
        ):
            call_command(
                "backfill_weather", "--start", "2026-04-10", "--end", "2026-04-01"
            )

    @patch(PATCH_BACKFILL_ALL)
    def test_end_today_or_later_raises_command_error(
        self, mock_backfill: MagicMock
    ) -> None:
        """The archive covers finished days only; today belongs to fetch_weather."""
        mock_backfill.return_value = _make_counts()

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="past days only"),
        ):
            call_command(
                "backfill_weather", "--start", "2026-04-01", "--end", TODAY.isoformat()
            )

    @patch(PATCH_BACKFILL_ALL)
    def test_same_start_and_end_is_allowed(self, mock_backfill: MagicMock) -> None:
        """A one-day window is valid."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command(
                "backfill_weather", "--start", "2026-04-10", "--end", "2026-04-10"
            )

        assert mock_backfill.call_args[0][0] == date(2026, 4, 10)
        assert mock_backfill.call_args[0][1] == date(2026, 4, 10)


# ---------------------------------------------------------------------------
# Explicit range and single day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestExplicitWindow:
    """Tests for --start/--end and --date."""

    @patch(PATCH_BACKFILL_ALL)
    def test_explicit_start_and_end_forwarded(self, mock_backfill: MagicMock) -> None:
        """Both bounds reach the service verbatim."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command(
                "backfill_weather", "--start", "2026-01-01", "--end", "2026-04-30"
            )

        assert mock_backfill.call_args[0][0] == date(2026, 1, 1)
        assert mock_backfill.call_args[0][1] == date(2026, 4, 30)

    @patch(PATCH_BACKFILL_ALL)
    def test_start_only_end_defaults_to_yesterday(
        self, mock_backfill: MagicMock
    ) -> None:
        """--start alone runs through to yesterday."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--start", "2026-04-01")

        assert mock_backfill.call_args[0][0] == date(2026, 4, 1)
        assert mock_backfill.call_args[0][1] == YESTERDAY

    @patch(PATCH_BACKFILL_ALL)
    def test_date_flag_sets_window_to_single_day(
        self, mock_backfill: MagicMock
    ) -> None:
        """--date pins start == end."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15")

        assert mock_backfill.call_args[0][0] == date(2026, 4, 15)
        assert mock_backfill.call_args[0][1] == date(2026, 4, 15)


# ---------------------------------------------------------------------------
# --local-mirror
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLocalMirrorFlag:
    """Tests for --local-mirror base-URL resolution."""

    @override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL="http://localhost:8000/dev")
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_passes_configured_url(self, mock_backfill: MagicMock) -> None:
        """The mirror URL is forwarded as base_url."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--local-mirror", "--date", "2026-04-15")

        assert mock_backfill.call_args[1]["base_url"] == "http://localhost:8000/dev"

    @override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=None)
    def test_local_mirror_raises_when_setting_missing(self) -> None:
        """Without the setting the flag is an error, not a silent live fetch."""
        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError),
        ):
            call_command("backfill_weather", "--local-mirror", "--date", "2026-04-15")

    @override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL="http://localhost:8000/dev")
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_shown_in_banner(self, mock_backfill: MagicMock) -> None:
        """The LOCAL-MIRROR flag appears in the banner."""
        mock_backfill.return_value = _make_counts()
        out = StringIO()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command(
                "backfill_weather",
                "--local-mirror",
                "--date",
                "2026-04-15",
                stdout=out,
            )

        assert "LOCAL-MIRROR" in out.getvalue()

    @patch(PATCH_BACKFILL_ALL)
    def test_no_local_mirror_passes_none_base_url(
        self, mock_backfill: MagicMock
    ) -> None:
        """Without the flag, base_url is None (the configured archive host)."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15")

        assert mock_backfill.call_args[1]["base_url"] is None


# ---------------------------------------------------------------------------
# --delay
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDelayFlag:
    """Tests for --delay forwarding."""

    @patch(PATCH_BACKFILL_ALL)
    def test_default_delay_is_one_second(self, mock_backfill: MagicMock) -> None:
        """Without --delay, 1.0 is forwarded."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15")

        assert mock_backfill.call_args[1]["delay"] == 1.0

    @patch(PATCH_BACKFILL_ALL)
    def test_explicit_delay_forwarded(self, mock_backfill: MagicMock) -> None:
        """--delay is forwarded verbatim."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15", "--delay", "2.5")

        assert mock_backfill.call_args[1]["delay"] == 2.5

    @patch(PATCH_BACKFILL_ALL)
    def test_zero_delay_forwarded(self, mock_backfill: MagicMock) -> None:
        """--delay 0 disables pacing."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15", "--delay", "0")

        assert mock_backfill.call_args[1]["delay"] == 0.0


# ---------------------------------------------------------------------------
# --commit, failures, banner, output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommitFailuresAndOutput:
    """Tests for --commit, the failure gate, the banner and the summary."""

    @patch(PATCH_BACKFILL_ALL)
    def test_read_only_by_default(self, mock_backfill: MagicMock) -> None:
        """Without --commit, commit=False is forwarded."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15")

        assert mock_backfill.call_args[1]["commit"] is False

    @patch(PATCH_BACKFILL_ALL)
    def test_commit_flag_forwards_true(self, mock_backfill: MagicMock) -> None:
        """--commit forwards commit=True."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15", "--commit")

        assert mock_backfill.call_args[1]["commit"] is True

    @patch(PATCH_BACKFILL_ALL)
    def test_raises_command_error_on_failures(self, mock_backfill: MagicMock) -> None:
        """failed > 0 raises so cron/CI can detect it."""
        mock_backfill.return_value = _make_counts(failed=2)

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="2 region failure"),
        ):
            call_command("backfill_weather", "--date", "2026-04-15", "--commit")

    @patch(PATCH_BACKFILL_ALL)
    def test_banner_shows_read_only_and_delay(self, mock_backfill: MagicMock) -> None:
        """READ-ONLY and DELAY appear in the banner."""
        MicroRegionFactory.create()
        mock_backfill.return_value = _make_counts()
        out = StringIO()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("backfill_weather", "--date", "2026-04-15", stdout=out)

        output = out.getvalue()
        assert "READ-ONLY" in output
        assert "DELAY=1s" in output
        assert "1 region(s)" in output

    @patch(PATCH_BACKFILL_ALL)
    def test_summary_reports_gaps_filled(self, mock_backfill: MagicMock) -> None:
        """The summary counts gaps filled, not rows updated."""
        mock_backfill.return_value = _make_counts(created=4, skipped=140)
        out = StringIO()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command(
                "backfill_weather", "--date", "2026-04-15", "--commit", stdout=out
            )

        output = out.getvalue()
        assert "4 gap(s) filled" in output
        assert "140 skipped" in output


# ---------------------------------------------------------------------------
# --stash
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStashFlag:
    """--stash captures fetched records to the on-disk archive."""

    @staticmethod
    def _archive_response(target: date) -> dict[str, Any]:
        """Build a one-day Open-Meteo archive response."""
        return {
            "daily": {
                "time": [target.isoformat()],
                "weather_code": [3],
                "sunrise": [f"{target.isoformat()}T06:00"],
                "sunset": [f"{target.isoformat()}T20:00"],
                "temperature_2m_max": [5.0],
                "temperature_2m_min": [-1.0],
                "snowfall_sum": [0.0],
            }
        }

    def test_stash_writes_records_to_archive(self, tmp_path: Path) -> None:
        """Fetched records land in the archive file."""
        MicroRegionFactory.create(region_id="CH-1000")
        target = date(2026, 4, 15)
        archive = tmp_path / "openmeteo_archive.ndjson"

        mock_response = MagicMock()
        mock_response.json.return_value = self._archive_response(target)
        mock_response.raise_for_status.return_value = None

        with (
            override_settings(OPENMETEO_ARCHIVE_PATH=archive),
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                return_value=mock_response,
            ),
            patch(PATCH_TODAY, return_value=TODAY),
        ):
            call_command(
                "backfill_weather",
                "--date",
                target.isoformat(),
                "--stash",
                "--delay",
                "0",
            )

        records = list(read_archive(archive))
        assert len(records) == 1
        assert records[0]["region_id"] == "CH-1000"
        assert records[0]["date"] == target.isoformat()

    def test_stash_without_commit_leaves_db_unchanged(self, tmp_path: Path) -> None:
        """--stash alone writes the file but no DB rows."""
        from apps.weather.models import WeatherSnapshot

        MicroRegionFactory.create(region_id="CH-1001")
        target = date(2026, 4, 15)
        archive = tmp_path / "openmeteo_archive.ndjson"

        mock_response = MagicMock()
        mock_response.json.return_value = self._archive_response(target)
        mock_response.raise_for_status.return_value = None

        with (
            override_settings(OPENMETEO_ARCHIVE_PATH=archive),
            patch(
                "apps.weather.services.weather_fetcher.requests.get",
                return_value=mock_response,
            ),
            patch(PATCH_TODAY, return_value=TODAY),
        ):
            call_command(
                "backfill_weather",
                "--date",
                target.isoformat(),
                "--stash",
                "--delay",
                "0",
            )

        assert WeatherSnapshot.objects.count() == 0
        assert len(list(read_archive(archive))) == 1
