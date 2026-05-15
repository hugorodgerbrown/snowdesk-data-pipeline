"""
tests/bulletins/management/commands/test_fetch_bulletins.py — Tests for fetch_bulletins.

Covers the unified fetch_bulletins command introduced in SNOW-190:
  * Required --source flag (argparse validation).
  * --source single and multi-provider dispatch.
  * Deduplication and ordering of --source values.
  * --today, --date, --start-date mutual exclusion.
  * --local-mirror flag (replaces old --source local-mirror).
  * --commit / --force / --stash forwarding.
  * Fail-at-end semantics when multiple sources are requested.
  * records_failed > 0 non-zero exit.
  * SLF stash archive round-trip.
  * Argparse rejects --end-date and --source local-mirror.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from bulletins.models import PipelineRun
from bulletins.services.slf_archive import read_archive, write_archive
from tests.factories import BulletinFactory, PipelineRunFactory

# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_SLF = "bulletins.services.data_fetcher.run_pipeline"
PATCH_EUREGIO = "bulletins.services.euregio_fetcher.run_euregio_pipeline"
# The registry is built lazily; patch the underlying functions at their
# canonical locations so both the command and the registry pick up the mock.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_successful_run(**overrides: int) -> PipelineRun:
    """
    Build a PipelineRun in SUCCESS state for mocking pipeline return values.

    Args:
        **overrides: Optional overrides for records_created / records_updated.

    Returns:
        A persisted PipelineRun marked as successful.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=overrides.get("records_created", 3),
        records_updated=overrides.get("records_updated", 0),
    )


def _make_failed_run(error_message: str = "API timeout") -> PipelineRun:
    """
    Build a PipelineRun in FAILED state.

    Args:
        error_message: The error message to store on the run.

    Returns:
        A persisted PipelineRun marked as failed.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.FAILED,
        error_message=error_message,
    )


def _make_failed_records_run(records_failed: int = 2) -> PipelineRun:
    """
    Build a SUCCESS-state run with a non-zero records_failed counter.

    Args:
        records_failed: The number of render-model failures to record.

    Returns:
        A persisted PipelineRun with status=SUCCESS but records_failed > 0.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=5,
        records_updated=0,
        records_failed=records_failed,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchBulletinsArgparse:
    """Argparse-level validation tests (flag shapes, required args, rejects)."""

    def test_source_required(self) -> None:
        """Missing --source raises a CommandError (argparse error)."""
        with pytest.raises((CommandError, SystemExit)):
            call_command("fetch_bulletins")

    def test_source_rejects_local_mirror_value(self) -> None:
        """--source local-mirror is rejected by argparse (not a valid choice)."""
        with pytest.raises((CommandError, SystemExit)):
            call_command("fetch_bulletins", "--source", "local-mirror")

    def test_end_date_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--end-date is not a recognised argument (removed in SNOW-190)."""
        with pytest.raises((CommandError, SystemExit)):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--end-date",
                "2026-01-01",
            )

    def test_negative_delay_rejected(self) -> None:
        """Negative --delay values are rejected at argparse time."""
        with pytest.raises(CommandError, match="delay must be non-negative"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-03-15",
                "--delay",
                "-1",
            )


@pytest.mark.django_db
class TestFetchBulletinsDateResolution:
    """Tests for --today, --date, --start-date mutual exclusion and resolution."""

    @patch(PATCH_SLF)
    def test_today_resolves_to_utc_today(self, mock_run: MagicMock) -> None:
        """--today sets both start and end to today (UTC)."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins", "--source", "slf", "--today")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 4, 16)
        assert kwargs["end"] == date(2026, 4, 16)

    def test_today_mutually_exclusive_with_start_date(self) -> None:
        """--today and --start-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--today",
                "--start-date",
                "2026-01-01",
            )

    def test_today_mutually_exclusive_with_date(self) -> None:
        """--today and --date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--today",
                "--date",
                "2026-01-15",
            )

    def test_date_mutually_exclusive_with_start_date(self) -> None:
        """--date and --start-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-01-15",
                "--start-date",
                "2026-01-01",
            )

    @patch(PATCH_SLF)
    def test_date_sets_both_start_and_end(self, mock_run: MagicMock) -> None:
        """--date forwards the same value for start and end."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-01-15")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 15)
        assert kwargs["end"] == date(2026, 1, 15)

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_SLF)
    def test_no_date_flag_empty_db_falls_back_to_season_start(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty DB: default start is SEASON_START_DATE."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins", "--source", "slf")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2025, 11, 1)
        assert kwargs["end"] == date(2026, 4, 16)
        assert "SEASON_START_DATE backstop" in capsys.readouterr().out

    @patch(PATCH_SLF)
    def test_no_date_flag_uses_latest_bulletin_valid_from(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Populated DB: default start is the newest bulletin's valid_from day."""
        mock_run.return_value = _make_successful_run()
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )

        with patch(
            "bulletins.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins", "--source", "slf")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 4, 15)
        assert kwargs["end"] == date(2026, 4, 16)
        assert "from latest bulletin valid_from day" in capsys.readouterr().out

    @patch(PATCH_SLF)
    def test_explicit_start_date(self, mock_run: MagicMock) -> None:
        """--start-date overrides the default; end is today."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command(
                "fetch_bulletins", "--source", "slf", "--start-date", "2026-01-01"
            )

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 1)
        assert kwargs["end"] == date(2026, 4, 16)


@pytest.mark.django_db
class TestFetchBulletinsSourceDispatch:
    """Tests verifying which pipeline functions are called for each --source value."""

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_source_slf_calls_only_slf_pipeline(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source slf calls run_pipeline but not run_euregio_pipeline."""
        mock_slf.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        mock_slf.assert_called_once()
        mock_euregio.assert_not_called()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_source_euregio_calls_only_euregio_pipeline(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source euregio calls run_euregio_pipeline but not run_pipeline."""
        mock_euregio.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "euregio", "--date", "2026-03-15")

        mock_euregio.assert_called_once()
        mock_slf.assert_not_called()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_source_slf_euregio_space_separated_calls_both(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source slf euregio calls both pipelines."""
        mock_slf.return_value = _make_successful_run()
        mock_euregio.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "euregio",
            "--date",
            "2026-03-15",
        )

        mock_slf.assert_called_once()
        mock_euregio.assert_called_once()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_source_euregio_slf_preserves_order(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source euregio slf runs EUREGIO first, then SLF (AC#5 ordering)."""
        mock_slf.return_value = _make_successful_run()
        mock_euregio.return_value = _make_successful_run()

        call_order: list[str] = []

        def _record_slf(**kw: object) -> PipelineRun:
            call_order.append("slf")
            return _make_successful_run()

        def _record_euregio(**kw: object) -> PipelineRun:
            call_order.append("euregio")
            return _make_successful_run()

        mock_slf.side_effect = _record_slf
        mock_euregio.side_effect = _record_euregio

        call_command(
            "fetch_bulletins",
            "--source",
            "euregio",
            "slf",
            "--date",
            "2026-03-15",
        )

        assert call_order == ["euregio", "slf"]

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_source_repeated_flag_calls_both(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source slf --source euregio (repeated) is equivalent to space-separated."""
        mock_slf.return_value = _make_successful_run()
        mock_euregio.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "--source",
            "euregio",
            "--date",
            "2026-03-15",
        )

        mock_slf.assert_called_once()
        mock_euregio.assert_called_once()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_duplicate_source_deduped_to_one_call(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """--source slf slf deduplicates to a single SLF pipeline call."""
        mock_slf.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "slf",
            "--date",
            "2026-03-15",
        )

        mock_slf.assert_called_once()
        mock_euregio.assert_not_called()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_triggered_by_includes_source_name_slf(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """triggered_by label identifies both the command and the provider."""
        mock_slf.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        _, kwargs = mock_slf.call_args
        assert "fetch_bulletins" in kwargs["triggered_by"]
        assert "slf" in kwargs["triggered_by"]

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_triggered_by_includes_source_name_euregio(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """triggered_by label identifies the euregio provider."""
        mock_euregio.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "euregio", "--date", "2026-03-15")

        _, kwargs = mock_euregio.call_args
        assert "fetch_bulletins" in kwargs["triggered_by"]
        assert "euregio" in kwargs["triggered_by"]


@pytest.mark.django_db
class TestFetchBulletinsCommitForce:
    """Tests for --commit and --force flag forwarding."""

    @patch(PATCH_SLF)
    def test_default_is_dry_run(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bare invocation is read-only."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True
        out = capsys.readouterr().out
        assert "Read-only run complete" in out

    @patch(PATCH_SLF)
    def test_commit_disables_dry_run(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--commit forwards dry_run=False."""
        mock_run.return_value = _make_successful_run(
            records_created=5, records_updated=2
        )

        call_command(
            "fetch_bulletins", "--source", "slf", "--date", "2026-03-15", "--commit"
        )

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is False
        out = capsys.readouterr().out
        assert "5 created" in out
        assert "Read-only" not in out

    @patch(PATCH_SLF)
    def test_force_flag_forwarded(self, mock_run: MagicMock) -> None:
        """--force forwards force=True to the pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "--date",
            "2026-03-15",
            "--commit",
            "--force",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["force"] is True

    @patch(PATCH_SLF)
    def test_delay_forwarded(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--delay forwards a positive float and surfaces in the banner."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "--date",
            "2026-03-15",
            "--delay",
            "2.5",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["delay"] == 2.5
        assert "DELAY=2.5s" in capsys.readouterr().out

    @patch(PATCH_SLF)
    def test_delay_defaults_to_zero(self, mock_run: MagicMock) -> None:
        """Bare invocation forwards delay=0.0."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["delay"] == 0.0


@pytest.mark.django_db
class TestFetchBulletinsLocalMirror:
    """Tests for --local-mirror flag behaviour."""

    @patch(PATCH_SLF)
    def test_no_local_mirror_passes_none_base_url(self, mock_run: MagicMock) -> None:
        """Without --local-mirror, base_url=None so the live setting wins."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] is None

    @override_settings(
        SLF_API_LOCAL_MIRROR_URL=(
            "http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml"
        )
    )
    @patch(PATCH_SLF)
    def test_local_mirror_slf_forwards_setting_url(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--local-mirror --source slf forwards SLF_API_LOCAL_MIRROR_URL."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "slf",
            "--date",
            "2026-03-15",
            "--local-mirror",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] == (
            "http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml"
        )
        assert "LOCAL-MIRROR" in capsys.readouterr().out

    @override_settings(SLF_API_LOCAL_MIRROR_URL="")
    @patch(PATCH_SLF)
    def test_local_mirror_slf_without_setting_raises(self, mock_run: MagicMock) -> None:
        """Empty SLF_API_LOCAL_MIRROR_URL with --local-mirror raises CommandError."""
        with pytest.raises(CommandError, match="SLF_API_LOCAL_MIRROR_URL"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-03-15",
                "--local-mirror",
            )
        mock_run.assert_not_called()

    @override_settings(
        EUREGIO_API_LOCAL_MIRROR_URL="http://localhost:8000/dev/euregio-mirror"
    )
    @patch(PATCH_EUREGIO)
    def test_local_mirror_euregio_forwards_setting_url(
        self, mock_run: MagicMock
    ) -> None:
        """--local-mirror --source euregio forwards EUREGIO_API_LOCAL_MIRROR_URL."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--source",
            "euregio",
            "--date",
            "2026-03-15",
            "--local-mirror",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] == "http://localhost:8000/dev/euregio-mirror"

    @override_settings(EUREGIO_API_LOCAL_MIRROR_URL=None)
    @patch(PATCH_EUREGIO)
    def test_local_mirror_euregio_without_setting_raises(
        self, mock_run: MagicMock
    ) -> None:
        """None EUREGIO_API_LOCAL_MIRROR_URL with --local-mirror raises CommandError."""
        with pytest.raises(CommandError, match="EUREGIO_API_LOCAL_MIRROR_URL"):
            call_command(
                "fetch_bulletins",
                "--source",
                "euregio",
                "--date",
                "2026-03-15",
                "--local-mirror",
            )
        mock_run.assert_not_called()


@pytest.mark.django_db
class TestFetchBulletinsErrorHandling:
    """Tests for fail-at-end semantics and non-zero exit conditions."""

    @patch(PATCH_SLF)
    def test_raises_on_failed_run_status(self, mock_run: MagicMock) -> None:
        """CommandError when the pipeline returns status=failed."""
        mock_run.return_value = _make_failed_run("connection refused")

        with pytest.raises(CommandError, match="connection refused"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-03-15",
                "--commit",
            )

    @patch(PATCH_SLF)
    def test_raises_on_pipeline_exception(self, mock_run: MagicMock) -> None:
        """CommandError wraps an unexpected exception from the pipeline."""
        mock_run.side_effect = RuntimeError("unexpected error")

        with pytest.raises(CommandError, match="unexpected error"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-03-15",
                "--commit",
            )

    @patch(PATCH_SLF)
    def test_raises_when_records_failed_nonzero(self, mock_run: MagicMock) -> None:
        """records_failed > 0 surfaces as a non-zero exit (CommandError)."""
        mock_run.return_value = _make_failed_records_run(records_failed=2)

        with pytest.raises(CommandError, match="render-model"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "--date",
                "2026-03-15",
                "--commit",
            )

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_fail_at_end_one_source_raises_other_still_runs(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """SLF pipeline raises → EUREGIO still runs → CommandError names both."""
        mock_slf.side_effect = RuntimeError("SLF network error")
        mock_euregio.return_value = _make_successful_run()

        with pytest.raises(CommandError, match="slf"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "euregio",
                "--date",
                "2026-03-15",
            )

        # EUREGIO must still have been called despite SLF failing.
        mock_euregio.assert_called_once()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_fail_at_end_error_message_names_failed_source(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """The final CommandError message names the failed source."""
        mock_slf.side_effect = RuntimeError("SLF boom")
        mock_euregio.return_value = _make_successful_run()

        with pytest.raises(CommandError) as exc_info:
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "euregio",
                "--date",
                "2026-03-15",
            )

        assert "slf" in str(exc_info.value).lower()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_fail_at_end_records_failed_on_one_source(
        self, mock_slf: MagicMock, mock_euregio: MagicMock
    ) -> None:
        """records_failed > 0 on one source causes non-zero exit; other source ran."""
        mock_slf.return_value = _make_failed_records_run(records_failed=1)
        mock_euregio.return_value = _make_successful_run()

        with pytest.raises(CommandError, match="slf"):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "euregio",
                "--date",
                "2026-03-15",
                "--commit",
            )

        # EUREGIO ran regardless.
        mock_euregio.assert_called_once()

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_stash_failure_is_per_source_error_other_source_still_runs(
        self,
        mock_slf: MagicMock,
        mock_euregio: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Stash writer raising does not abort remaining sources (fail-at-end)."""
        mock_slf.return_value = _make_successful_run()
        mock_euregio.return_value = _make_successful_run()

        euregio_archive = tmp_path / "euregio.ndjson"

        with (
            patch(
                "bulletins.management.commands.fetch_bulletins.Command._flush_stash",
                side_effect=[PermissionError("disk full"), None],
            ),
            override_settings(
                SLF_ARCHIVE_PATH=tmp_path / "slf.ndjson",
                EUREGIO_ARCHIVE_PATH=euregio_archive,
            ),
            pytest.raises(CommandError) as exc_info,
        ):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "euregio",
                "--date",
                "2026-03-15",
                "--stash",
            )

        # SLF stash failure is named in the final error.
        assert "slf" in str(exc_info.value).lower()
        # EUREGIO pipeline was still invoked despite SLF stash failure.
        mock_euregio.assert_called_once()


@pytest.mark.django_db
class TestFetchBulletinsStash:
    """Tests for --stash archive writing."""

    @patch(PATCH_SLF)
    def test_stash_off_by_default(self, mock_run: MagicMock) -> None:
        """Without --stash, on_fetched is None."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--source", "slf", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["on_fetched"] is None

    @patch(PATCH_SLF)
    def test_stash_collects_and_writes_slf_archive(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--stash --source slf flushes collected records to SLF_ARCHIVE_PATH."""
        archive_path = tmp_path / "archive.ndjson"
        collected_records = [
            {
                "bulletinID": "b1",
                "publicationTime": "2025-03-15T08:00:00Z",
                "validTime": {
                    "startTime": "2025-03-15T17:00:00Z",
                    "endTime": "2025-03-16T17:00:00Z",
                },
            },
            {
                "bulletinID": "b2",
                "publicationTime": "2025-03-16T08:00:00Z",
                "validTime": {
                    "startTime": "2025-03-16T17:00:00Z",
                    "endTime": "2025-03-17T17:00:00Z",
                },
            },
        ]

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            """Simulate the pipeline pumping records into on_fetched."""
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                for record in collected_records:
                    on_fetched(record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            call_command(
                "fetch_bulletins", "--source", "slf", "--date", "2026-03-15", "--stash"
            )

        archived = list(read_archive(archive_path))
        assert {r["bulletinID"] for r in archived} == {"b1", "b2"}

        out = capsys.readouterr().out
        assert "Stashed 2 fetched bulletin(s)" in out
        assert "STASH" in out

    @patch(PATCH_SLF)
    def test_stash_merges_with_existing_slf_archive(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """--stash overlays new records onto the existing archive (later wins)."""
        archive_path = tmp_path / "archive.ndjson"
        write_archive(
            archive_path,
            [
                {
                    "bulletinID": "old",
                    "publicationTime": "2025-03-10T08:00:00Z",
                    "validTime": {
                        "startTime": "2025-03-10T17:00:00Z",
                        "endTime": "2025-03-11T17:00:00Z",
                    },
                    "lang": "en",
                }
            ],
        )

        new_record = {
            "bulletinID": "new",
            "publicationTime": "2025-03-15T08:00:00Z",
            "validTime": {
                "startTime": "2025-03-15T17:00:00Z",
                "endTime": "2025-03-16T17:00:00Z",
            },
            "lang": "en",
        }

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            """Simulate the pipeline yielding one new record."""
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                on_fetched(new_record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            call_command(
                "fetch_bulletins", "--source", "slf", "--date", "2026-03-15", "--stash"
            )

        archived = list(read_archive(archive_path))
        assert [r["bulletinID"] for r in archived] == ["old", "new"]

    @patch(PATCH_EUREGIO)
    @patch(PATCH_SLF)
    def test_stash_multi_source_writes_both_archives(
        self,
        mock_slf: MagicMock,
        mock_euregio: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--stash --source slf euregio invokes both archive writers."""
        slf_archive = tmp_path / "slf.ndjson"
        euregio_archive = tmp_path / "euregio.ndjson"

        slf_record = {
            "bulletinID": "slf-1",
            "publicationTime": "2026-03-15T08:00:00Z",
            "validTime": {
                "startTime": "2026-03-15T17:00:00Z",
                "endTime": "2026-03-16T17:00:00Z",
            },
        }
        euregio_record = {
            "bulletinID": "eu-1",
            "validTime": {
                "startTime": "2026-03-15T16:00:00Z",
                "endTime": "2026-03-16T16:00:00Z",
            },
        }

        def fake_slf(**kwargs: object) -> PipelineRun:
            """SLF pipeline yields one record."""
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                on_fetched(slf_record)  # type: ignore[operator]
            return _make_successful_run()

        def fake_euregio(**kwargs: object) -> PipelineRun:
            """EUREGIO pipeline yields one record."""
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                on_fetched(euregio_record)  # type: ignore[operator]
            return _make_successful_run()

        mock_slf.side_effect = fake_slf
        mock_euregio.side_effect = fake_euregio

        with override_settings(
            SLF_ARCHIVE_PATH=slf_archive,
            EUREGIO_ARCHIVE_PATH=euregio_archive,
        ):
            call_command(
                "fetch_bulletins",
                "--source",
                "slf",
                "euregio",
                "--date",
                "2026-03-15",
                "--stash",
            )

        import json

        slf_archived = list(read_archive(slf_archive))
        assert {r["bulletinID"] for r in slf_archived} == {"slf-1"}

        euregio_archived = [
            json.loads(line)
            for line in euregio_archive.read_text().splitlines()
            if line.strip()
        ]
        assert {r["bulletinID"] for r in euregio_archived} == {"eu-1"}
