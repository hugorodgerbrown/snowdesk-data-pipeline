"""
tests/weather/management/commands/test_fetch_weather.py — Tests for fetch_weather.

``fetch_weather`` fetches today from the Open-Meteo forecast endpoint and
nothing else. It takes no date arguments, and it never reaches the archive
endpoint — the tests that used to cover window derivation, per-date routing,
``--start``/``--end``/``--date``, ``--local-mirror``, ``--delay`` and
``--stash`` moved with those flags to ``test_backfill_weather.py``.

Covers:
  - Today is the only date fetched, and it is read from ``timezone.localdate``.
  - The command exposes no date arguments at all.
  - --commit vs read-only.
  - failed > 0 → CommandError + non-zero exit, aggregated across both passes.
  - Banner content (READ-ONLY, SKIP-POINTS, ADD-HISTORY flags).
  - Output counts.
  - Active-ForecastCell pass (SNOW-416): invoked by default; --skip-points
    disables it; --add-history reaches it; point failures propagate into the
    same failed > 0 → CommandError gate.
"""

from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import CommandError, call_command

from tests.factories import MicroRegionFactory

PATCH_FETCH_ALL = "apps.weather.management.commands.fetch_weather.fetch_all_regions"
PATCH_FETCH_ALL_POINTS = (
    "apps.weather.management.commands.fetch_weather.fetch_all_points"
)
PATCH_TODAY = "apps.weather.management.commands.fetch_weather.timezone.localdate"

TODAY = date(2026, 5, 1)


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
# Today is the only date
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTodayOnly:
    """The command fetches today, from the forecast endpoint, and nothing else."""

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_fetches_today_for_regions_and_points(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """Both passes are handed today's date."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather")

        assert mock_fetch.call_args[0][0] == TODAY
        assert mock_points.call_args[0][0] == TODAY

    def test_command_exposes_no_date_arguments(self) -> None:
        """--date/--start/--end are gone; passing one is an error.

        Date-based endpoint selection is what let the archive rewrite a day
        the forecast pass had already stored. The flags went with it.
        """
        for flag in ("--date", "--start", "--end"):
            with pytest.raises(CommandError):
                call_command("fetch_weather", flag, "2026-05-01")

    def test_command_exposes_no_archive_flags(self) -> None:
        """--local-mirror/--stash/--delay belong to backfill_weather now."""
        for flag in ("--local-mirror", "--stash"):
            with pytest.raises(CommandError):
                call_command("fetch_weather", flag)
        with pytest.raises(CommandError):
            call_command("fetch_weather", "--delay", "1")


# ---------------------------------------------------------------------------
# --commit
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommitFlag:
    """Tests for --commit forwarding."""

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_read_only_by_default(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """Without --commit, commit=False is forwarded to both passes."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather")

        assert mock_fetch.call_args[1]["commit"] is False
        assert mock_points.call_args[1]["commit"] is False

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_commit_flag_forwards_true(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """--commit forwards commit=True to both passes."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--commit")

        assert mock_fetch.call_args[1]["commit"] is True
        assert mock_points.call_args[1]["commit"] is True


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFailureHandling:
    """A non-zero failed count must raise so cron/CI can detect it."""

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_raises_command_error_on_failures(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """failed > 0 raises CommandError."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts(failed=2)
        mock_points.return_value = _make_counts()

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="2 region/point failure"),
        ):
            call_command("fetch_weather", "--commit")

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_failures_aggregated_across_both_passes(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """Region and point failures sum into one total."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts(failed=1)
        mock_points.return_value = _make_counts(failed=3)

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="4 region/point failure"),
        ):
            call_command("fetch_weather", "--commit")

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_no_error_when_no_failures(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """failed == 0 completes cleanly."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts(created=3)
        mock_points.return_value = _make_counts(created=7)

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--commit")


# ---------------------------------------------------------------------------
# Banner and output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBannerAndOutput:
    """Tests for the start-of-run banner and the post-run summary."""

    @staticmethod
    def _run(*args: str, **counts: Any) -> str:
        """Run the command with both passes stubbed and return stdout."""
        from io import StringIO

        out = StringIO()
        with (
            patch(PATCH_FETCH_ALL, return_value=_make_counts(**counts)),
            patch(PATCH_FETCH_ALL_POINTS, return_value=_make_counts()),
            patch(PATCH_TODAY, return_value=TODAY),
        ):
            call_command("fetch_weather", *args, stdout=out)
        return out.getvalue()

    def test_banner_shows_read_only_when_not_committed(self) -> None:
        """The READ-ONLY flag appears without --commit."""
        MicroRegionFactory.create()
        assert "READ-ONLY" in self._run()

    def test_banner_omits_read_only_when_committed(self) -> None:
        """The READ-ONLY flag is absent with --commit."""
        MicroRegionFactory.create()
        assert "READ-ONLY" not in self._run("--commit")

    def test_banner_includes_region_and_point_counts(self) -> None:
        """The banner names how many regions and points are in scope."""
        MicroRegionFactory.create()
        MicroRegionFactory.create()
        output = self._run()
        assert "2 region(s)" in output
        assert "active point(s)" in output

    def test_banner_names_today_not_a_window(self) -> None:
        """The banner carries today's date alone — there is no window to show."""
        MicroRegionFactory.create()
        banner = self._run().splitlines()[0]
        assert str(TODAY) in banner
        assert " to " not in banner

    def test_success_output_shows_counts(self) -> None:
        """The summary reports created/updated/skipped/failed."""
        MicroRegionFactory.create()
        output = self._run("--commit", created=5, updated=2)
        assert "5 created" in output
        assert "2 updated" in output

    def test_read_only_output_prompts_commit(self) -> None:
        """A read-only run tells the caller how to persist."""
        MicroRegionFactory.create()
        assert "--commit" in self._run()


# ---------------------------------------------------------------------------
# Active-ForecastCell pass (SNOW-416)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestActiveForecastCellPass:
    """The point pass runs by default and can be skipped."""

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_point_pass_invoked_by_default(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """With no flags the point pass runs for today."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather")

        mock_points.assert_called_once()
        assert mock_points.call_args[0][0] == TODAY

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_skip_points_disables_point_pass(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """--skip-points runs the region pass alone."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--skip-points")

        mock_points.assert_not_called()
        mock_fetch.assert_called_once()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_add_history_flag_reaches_the_point_pass(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """--add-history is forwarded to fetch_all_points."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--commit", "--add-history")

        assert mock_points.call_args[1]["add_history"] is True

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_skip_points_shown_in_banner(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """The SKIP-POINTS flag appears in the banner."""
        from io import StringIO

        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        out = StringIO()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--skip-points", stdout=out)

        assert "SKIP-POINTS" in out.getvalue()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_point_counts_merged_into_report(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """Region and point counts are summed in the summary line."""
        from io import StringIO

        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts(created=2)
        mock_points.return_value = _make_counts(created=5)
        out = StringIO()

        with patch(PATCH_TODAY, return_value=TODAY):
            call_command("fetch_weather", "--commit", stdout=out)

        assert "7 created" in out.getvalue()

    @patch(PATCH_FETCH_ALL_POINTS)
    @patch(PATCH_FETCH_ALL)
    def test_point_failures_propagate_to_command_error(
        self,
        mock_fetch: MagicMock,
        mock_points: MagicMock,
    ) -> None:
        """A point-only failure still raises."""
        MicroRegionFactory.create()
        mock_fetch.return_value = _make_counts()
        mock_points.return_value = _make_counts(failed=1)

        with (
            patch(PATCH_TODAY, return_value=TODAY),
            pytest.raises(CommandError, match="1 region/point failure"),
        ):
            call_command("fetch_weather", "--commit")
