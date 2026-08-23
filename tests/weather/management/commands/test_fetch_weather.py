"""
tests/weather/management/commands/test_fetch_weather.py — Tests for fetch_weather.

Covers:
  - Default window derivation (snapshots present → latest snapshot; no
    snapshots but bulletins present → earliest bulletin valid_from; empty
    DB → SEASON_START_DATE).
  - --local-mirror resolves the mirror base URL; raises CommandError when
    the setting is missing.
  - --start/--end explicit range.
  - --date single day.
  - --date combined with --start or --end → CommandError.
  - --end < --start → CommandError.
  - Per-date routing: a window spanning past dates + today calls BOTH
    backfill_all_regions (archive) and fetch_all_regions (forecast).
  - failed > 0 → CommandError + non-zero exit.
  - --commit vs read-only.
  - Banner content (READ-ONLY, LOCAL-MIRROR, DELAY, STASH flags).
  - --stash: writes records to the archive.
  - Active-ForecastCell pass (SNOW-416): invoked by default when the window
    reaches today; --skip-points disables it; --local-mirror skips it
    cleanly (points don't participate in the mirror); point failures
    propagate into the same failed > 0 → CommandError gate.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.weather.services.openmeteo_archive import read_archive
from tests.factories import BulletinFactory, MicroRegionFactory, WeatherSnapshotFactory

PATCH_FETCH_ALL = "apps.weather.management.commands.fetch_weather.fetch_all_regions"
PATCH_BACKFILL_ALL = (
    "apps.weather.management.commands.fetch_weather.backfill_all_regions"
)
PATCH_FETCH_ALL_POINTS = (
    "apps.weather.management.commands.fetch_weather.fetch_all_points"
)
PATCH_TODAY = "apps.weather.management.commands.fetch_weather.timezone.localdate"


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
    """Tests for the three-branch default start-date logic."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_default_start_from_latest_snapshot_when_present(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """When snapshots exist, default start = latest snapshot valid_for_date."""
        region = MicroRegionFactory.create()
        WeatherSnapshotFactory.create(region=region, valid_for_date=date(2026, 4, 10))
        WeatherSnapshotFactory.create(region=region, valid_for_date=date(2026, 4, 20))
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        # archive sub-range: 2026-04-20 → 2026-04-30 (yesterday)
        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 4, 20)

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_default_start_from_earliest_bulletin_when_no_snapshots(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """When no snapshots exist but bulletins do, default start = earliest bulletin valid_from."""
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
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        # archive sub-range starts at the earliest bulletin date
        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 1, 5)

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_default_start_from_season_start_date_when_db_empty(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """When the DB is empty, default start = settings.SEASON_START_DATE."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        season_start = settings.SEASON_START_DATE

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == season_start


# ---------------------------------------------------------------------------
# Per-date routing
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPerDateRouting:
    """Tests for the archive/forecast split at today's boundary."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_window_spanning_past_and_today_calls_both_services(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """A window that includes past dates AND today calls both service functions."""
        mock_backfill.return_value = _make_counts(created=5)
        mock_fetch.return_value = _make_counts(created=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 28),
                end=date(2026, 5, 1),
            )

        mock_backfill.assert_called_once()
        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 4, 28)
        assert backfill_call[0][1] == date(2026, 4, 30)  # yesterday

        mock_fetch.assert_called_once()
        fetch_call = mock_fetch.call_args
        assert fetch_call[0][0] == date(2026, 5, 1)  # today

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_past_only_window_calls_only_backfill(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """A window entirely in the past calls only backfill_all_regions."""
        mock_backfill.return_value = _make_counts(created=3)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
            )

        mock_backfill.assert_called_once()
        mock_fetch.assert_not_called()

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_today_only_calls_only_forecast(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """A window of just today calls only fetch_all_regions."""
        mock_fetch.return_value = _make_counts(created=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", date=date(2026, 5, 1))

        mock_fetch.assert_called_once()
        assert mock_fetch.call_args[0][0] == date(2026, 5, 1)
        mock_backfill.assert_not_called()

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_single_past_date_calls_only_backfill(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--date with a past date calls only backfill_all_regions."""
        mock_backfill.return_value = _make_counts(updated=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", date=date(2026, 4, 15))

        mock_backfill.assert_called_once()
        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 4, 15)
        assert backfill_call[0][1] == date(2026, 4, 15)
        mock_fetch.assert_not_called()

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_counts_are_aggregated_across_both_services(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Counts from archive and forecast fetches are summed for the final report."""
        mock_backfill.return_value = _make_counts(created=3, updated=1)
        mock_fetch.return_value = _make_counts(created=1, skipped=2)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 29),
                end=date(2026, 5, 1),
                commit=True,
            )

        out = capsys.readouterr().out
        assert "4 created" in out
        assert "1 updated" in out
        assert "2 skipped" in out


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArgumentValidation:
    """Tests for flag mutual-exclusion and range validation."""

    def test_date_with_start_raises_command_error(self) -> None:
        """--date combined with --start raises CommandError."""
        with pytest.raises(CommandError, match="cannot be combined"):
            call_command(
                "fetch_weather",
                date=date(2026, 4, 1),
                start=date(2026, 4, 1),
            )

    def test_date_with_end_raises_command_error(self) -> None:
        """--date combined with --end raises CommandError."""
        with pytest.raises(CommandError, match="cannot be combined"):
            call_command(
                "fetch_weather",
                date=date(2026, 4, 1),
                end=date(2026, 4, 30),
            )

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_end_before_start_raises_command_error(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--end before --start raises CommandError."""
        with pytest.raises(CommandError, match="--end must be on or after --start"):
            call_command(
                "fetch_weather",
                start=date(2026, 5, 2),
                end=date(2026, 5, 1),
            )
        mock_backfill.assert_not_called()
        mock_fetch.assert_not_called()

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_same_start_and_end_is_allowed(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--start == --end (single-day range) is valid."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 15),
                end=date(2026, 4, 15),
            )

        mock_backfill.assert_called_once()


# ---------------------------------------------------------------------------
# --local-mirror flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLocalMirrorFlag:
    """Tests for --local-mirror source resolution."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_passes_configured_url(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--local-mirror passes WEATHER_API_LOCAL_MIRROR_BASE_URL as base_url."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", local_mirror=True)

        assert mock_fetch.call_args[1]["base_url"] == mirror_url

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_raises_when_setting_missing(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--local-mirror raises CommandError when the setting is not configured."""
        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=None):
            with pytest.raises(CommandError, match="WEATHER_API_LOCAL_MIRROR_BASE_URL"):
                call_command("fetch_weather", local_mirror=True)

        mock_fetch.assert_not_called()
        mock_backfill.assert_not_called()

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_shown_in_banner(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes LOCAL-MIRROR when --local-mirror is passed."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", local_mirror=True)

        out = capsys.readouterr().out
        assert "LOCAL-MIRROR" in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_no_local_mirror_passes_none_base_url(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Without --local-mirror, base_url=None is forwarded (live API)."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        assert mock_fetch.call_args[1]["base_url"] is None


# ---------------------------------------------------------------------------
# --start / --end range
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStartEndRange:
    """Tests for explicit --start/--end range forwarding."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_explicit_start_and_end_forwarded(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--start and --end are forwarded as date objects to the archive service."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
            )

        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 4, 1)
        assert backfill_call[0][1] == date(2026, 4, 30)

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_start_only_end_defaults_to_today(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--start without --end defaults end to today."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", start=date(2026, 4, 1))

        # archive end = yesterday (2026-04-30)
        backfill_call = mock_backfill.call_args
        assert backfill_call[0][1] == date(2026, 4, 30)
        # forecast for today
        mock_fetch.assert_called_once_with(
            date(2026, 5, 1),
            commit=False,
            base_url=None,
            on_fetched=None,
        )


# ---------------------------------------------------------------------------
# --date single day
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDateSingleDay:
    """Tests for the --date flag (single-day shorthand)."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_date_flag_sets_window_to_single_day(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--date YYYY-MM-DD sets both start and end to that date."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", date=date(2026, 4, 15))

        backfill_call = mock_backfill.call_args
        assert backfill_call[0][0] == date(2026, 4, 15)
        assert backfill_call[0][1] == date(2026, 4, 15)


# ---------------------------------------------------------------------------
# commit flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommitFlag:
    """Tests for the --commit flag."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_read_only_by_default(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Without --commit, commit=False is forwarded to service functions."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        assert mock_fetch.call_args[1]["commit"] is False

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_commit_flag_forwards_true(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--commit causes commit=True to be forwarded."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(created=2)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)

        assert mock_fetch.call_args[1]["commit"] is True


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFailureHandling:
    """Tests for failed > 0 → CommandError."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_raises_command_error_on_failures(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """CommandError is raised when total failed > 0."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(failed=2, created=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            with pytest.raises(CommandError, match="2 region/point failure"):
                call_command("fetch_weather", commit=True)

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_failures_aggregated_across_both_services(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Failures from archive + forecast are summed; CommandError fires on total."""
        mock_backfill.return_value = _make_counts(failed=1)
        mock_fetch.return_value = _make_counts(failed=2)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            with pytest.raises(CommandError, match="3 region/point failure"):
                call_command(
                    "fetch_weather",
                    start=date(2026, 4, 29),
                    end=date(2026, 5, 1),
                    commit=True,
                )

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_no_error_when_no_failures(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """No CommandError when total failed == 0."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(created=3)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)  # should not raise


# ---------------------------------------------------------------------------
# Banner content
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBannerContent:
    """Tests for the start-of-run banner."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_banner_shows_read_only_when_not_committed(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner shows [READ-ONLY] when --commit is not passed."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "READ-ONLY" in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_banner_omits_read_only_when_committed(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner does not show [READ-ONLY] when --commit is passed."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(created=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)

        out = capsys.readouterr().out
        assert "READ-ONLY" not in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_banner_includes_region_count(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes the total number of regions."""
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "2 region" in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_banner_shows_delay_flag(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes DELAY when --delay is passed."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                delay=2.0,
            )

        out = capsys.readouterr().out
        assert "DELAY=2s" in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_banner_shows_stash_flag(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        """Banner includes STASH when --stash is passed."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        archive_path = tmp_path / "om_archive.ndjson"

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", stash=True)

        out = capsys.readouterr().out
        assert "STASH" in out


# ---------------------------------------------------------------------------
# --delay flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDelayFlag:
    """Tests for --delay forwarding to backfill_all_regions."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_default_delay_is_one_second(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Without an explicit --delay, 1.0 is forwarded to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
            )

        assert mock_backfill.call_args[1]["delay"] == 1.0

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_explicit_delay_forwarded(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--delay is forwarded verbatim to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                delay=2.5,
            )

        assert mock_backfill.call_args[1]["delay"] == 2.5

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_zero_delay_forwarded(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """--delay 0 disables pacing and forwards 0.0 to backfill_all_regions."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                delay=0,
            )

        assert mock_backfill.call_args[1]["delay"] == 0.0


# ---------------------------------------------------------------------------
# --stash flag
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStashFlag:
    """Tests for --stash archive capture."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_stash_writes_records_to_archive(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--stash causes on_fetched records to be merged into the archive."""
        region = MicroRegionFactory.create()
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_fetch_all(
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

        mock_fetch.side_effect = fake_fetch_all
        mock_backfill.return_value = _make_counts()

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", stash=True)

        records = list(read_archive(archive_path))
        assert len(records) == 1
        assert records[0]["region_id"] == region.region_id
        assert records[0]["date"] == "2026-05-01"

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_stash_without_commit_leaves_db_unchanged(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--stash without --commit writes the archive but not the database."""
        archive_path = tmp_path / "om_archive.ndjson"

        def fake_fetch_all(
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

        mock_fetch.side_effect = fake_fetch_all
        mock_backfill.return_value = _make_counts()

        with override_settings(OPENMETEO_ARCHIVE_PATH=archive_path):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", stash=True, commit=False)

        records = list(read_archive(archive_path))
        assert len(records) == 1
        assert mock_fetch.call_args[1]["commit"] is False


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOutput:
    """Tests for the post-run output summary."""

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_success_output_shows_counts(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=True, stdout shows created/updated/skipped/failed counts."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(created=4, updated=1, skipped=2)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)

        out = capsys.readouterr().out
        assert "4 created" in out
        assert "1 updated" in out
        assert "2 skipped" in out

    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_read_only_output_prompts_commit(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When commit=False, stdout tells the user to pass --commit."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather")

        out = capsys.readouterr().out
        assert "--commit" in out


# ---------------------------------------------------------------------------
# Active-ForecastCell pass (SNOW-416)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestActiveForecastCellPass:
    """Tests for the active-ForecastCell forecast pass and --skip-points."""

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_point_pass_invoked_by_default_when_window_reaches_today(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """A window reaching today invokes fetch_all_points for today's date."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mock_fetch_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)

        mock_fetch_points.assert_called_once()
        assert mock_fetch_points.call_args[0][0] == date(2026, 5, 1)
        assert mock_fetch_points.call_args[1]["commit"] is True
        # History retention is opt-in (SNOW-629) — a bare run does not ask
        # for it.
        assert mock_fetch_points.call_args[1]["add_history"] is False

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_add_history_flag_reaches_the_point_pass(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """--add-history is threaded through to fetch_all_points (SNOW-629)."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mock_fetch_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True, add_history=True)

        assert mock_fetch_points.call_args[1]["add_history"] is True

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_point_pass_skipped_when_window_does_not_reach_today(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """A window entirely in the past does not invoke fetch_all_points."""
        mock_backfill.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command(
                "fetch_weather",
                start=date(2026, 4, 1),
                end=date(2026, 4, 30),
                commit=True,
            )

        mock_fetch_points.assert_not_called()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_skip_points_disables_point_pass(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """--skip-points prevents fetch_all_points from being called."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True, skip_points=True)

        mock_fetch_points.assert_not_called()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_local_mirror_skips_point_pass(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """--local-mirror skips the point pass — points don't participate in the mirror."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mirror_url = "http://localhost:8000/dev/openmeteo-mirror/v1"

        with override_settings(WEATHER_API_LOCAL_MIRROR_BASE_URL=mirror_url):
            with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
                call_command("fetch_weather", commit=True, local_mirror=True)

        mock_fetch_points.assert_not_called()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_point_failures_propagate_to_command_error(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
    ) -> None:
        """Point-pass failures are merged into the failed>0 → CommandError gate."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()
        mock_fetch_points.return_value = _make_counts(failed=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            with pytest.raises(CommandError, match="1 region/point failure"):
                call_command("fetch_weather", commit=True)

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_point_counts_merged_into_report(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Point pass created/updated counts are folded into the final report."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts(created=1)
        mock_fetch_points.return_value = _make_counts(created=2, updated=1)

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True)

        out = capsys.readouterr().out
        assert "3 created" in out
        assert "1 updated" in out

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    @patch(PATCH_BACKFILL_ALL)
    def test_skip_points_shown_in_banner(
        self,
        mock_backfill: MagicMock,
        mock_fetch: MagicMock,
        mock_fetch_points: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Banner includes SKIP-POINTS when --skip-points is passed."""
        mock_backfill.return_value = _make_counts()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=date(2026, 5, 1)):
            call_command("fetch_weather", commit=True, skip_points=True)

        out = capsys.readouterr().out
        assert "SKIP-POINTS" in out
