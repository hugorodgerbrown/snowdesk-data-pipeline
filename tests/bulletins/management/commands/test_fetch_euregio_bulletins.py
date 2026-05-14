"""
tests/bulletins/management/commands/test_fetch_euregio_bulletins.py — Tests for
fetch_euregio_bulletins.

Covers argument defaults (no-args invocation), --date / --start-date /
--end-date semantics, --commit / --force forwarding, --source / --stash
flag handling, error paths, and the records_failed exit-code path.
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from bulletins.models import PipelineRun
from tests.factories import PipelineRunFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PATCH_PIPELINE = (
    "bulletins.management.commands.fetch_euregio_bulletins.run_euregio_pipeline"
)
PATCH_LATEST = (
    "bulletins.management.commands.fetch_euregio_bulletins.latest_euregio_date"
)


def _make_successful_run(
    records_created: int = 3,
    records_updated: int = 0,
) -> PipelineRun:
    """
    Build a PipelineRun in SUCCESS state for mocking run_euregio_pipeline return.

    Args:
        records_created: Number of bulletins created.
        records_updated: Number of bulletins updated.

    Returns:
        A persisted PipelineRun marked as successful.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=records_created,
        records_updated=records_updated,
    )


def _make_failed_run(error_message: str = "CDN timeout") -> PipelineRun:
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
    Build a SUCCESS-state run with non-zero records_failed.

    Args:
        records_failed: Number of render-model failures.

    Returns:
        A PipelineRun with status=SUCCESS but records_failed > 0.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=5,
        records_failed=records_failed,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFetchEuregioBulletinsCommand:
    """Tests for the fetch_euregio_bulletins management command."""

    # ------------------------------------------------------------------
    # Defaults — bare invocation
    # ------------------------------------------------------------------

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_PIPELINE)
    @patch(PATCH_LATEST, return_value=None)
    def test_no_args_empty_db_falls_back_to_season_start(
        self,
        mock_latest: MagicMock,
        mock_run: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Empty DB: bare invocation runs from SEASON_START_DATE to today."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_euregio_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_euregio_bulletins")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2025, 11, 1)
        assert kwargs["end"] == date(2026, 4, 16)
        assert kwargs["dry_run"] is True
        out = capsys.readouterr().out
        assert "SEASON_START_DATE (empty DB)" in out

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_PIPELINE)
    @patch(PATCH_LATEST, return_value=date(2026, 4, 15))
    def test_no_args_uses_latest_euregio_date(
        self,
        mock_latest: MagicMock,
        mock_run: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Populated DB: start defaults to latest EUREGIO bulletin's valid_from day."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_euregio_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_euregio_bulletins")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 4, 15)
        assert kwargs["end"] == date(2026, 4, 16)
        out = capsys.readouterr().out
        assert "latest EUREGIO bulletin in DB" in out

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_PIPELINE)
    @patch(PATCH_LATEST, return_value=date(2026, 4, 15))
    def test_explicit_start_date_overrides_default(
        self,
        mock_latest: MagicMock,
        mock_run: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--start-date wins over the latest-bulletin default even with data in DB."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_euregio_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_euregio_bulletins", "--start-date", "2026-01-01")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 1)
        out = capsys.readouterr().out
        assert "latest EUREGIO bulletin" not in out
        assert "SEASON_START_DATE" not in out

    @patch(PATCH_PIPELINE)
    @patch(PATCH_LATEST, return_value=None)
    def test_no_args_is_dry_run(
        self,
        mock_latest: MagicMock,
        mock_run: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Bare invocation is read-only and confirms so in output."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins")

        out = capsys.readouterr().out
        assert "Dry-run complete" in out
        assert "--commit" in out

    # ------------------------------------------------------------------
    # --date single-day shortcut
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_date_sets_both_start_and_end(self, mock_run: MagicMock) -> None:
        """--date forwards the same value for start and end."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--date", "2026-01-15")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 15)
        assert kwargs["end"] == date(2026, 1, 15)

    def test_date_conflicts_with_start_date(self) -> None:
        """--date and --start-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_euregio_bulletins",
                "--date",
                "2026-01-15",
                "--start-date",
                "2026-01-01",
            )

    def test_date_conflicts_with_end_date(self) -> None:
        """--date and --end-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_euregio_bulletins",
                "--date",
                "2026-01-15",
                "--end-date",
                "2026-01-31",
            )

    # ------------------------------------------------------------------
    # Range arguments
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_explicit_range_forwarded(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Explicit --start-date / --end-date are forwarded verbatim."""
        mock_run.return_value = _make_successful_run(
            records_created=10, records_updated=2
        )

        call_command(
            "fetch_euregio_bulletins",
            "--start-date",
            "2026-03-01",
            "--end-date",
            "2026-03-31",
            "--commit",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 3, 1)
        assert kwargs["end"] == date(2026, 3, 31)
        out = capsys.readouterr().out
        assert "31 day(s)" in out
        assert "10 created" in out
        assert "2 updated" in out

    def test_rejects_end_before_start(self) -> None:
        """CommandError when --end-date precedes --start-date."""
        with pytest.raises(CommandError, match="must not precede"):
            call_command(
                "fetch_euregio_bulletins",
                "--start-date",
                "2026-03-31",
                "--end-date",
                "2026-03-01",
            )

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_PIPELINE)
    @patch(PATCH_LATEST, return_value=None)
    def test_end_only_uses_season_start(
        self, mock_latest: MagicMock, mock_run: MagicMock
    ) -> None:
        """Omitting --start-date falls back to settings.SEASON_START_DATE."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--end-date", "2026-01-31")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2025, 11, 1)
        assert kwargs["end"] == date(2026, 1, 31)

    @patch(PATCH_PIPELINE)
    def test_start_only_uses_today_for_end(self, mock_run: MagicMock) -> None:
        """Omitting --end-date defaults to today (UTC)."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "bulletins.management.commands.fetch_euregio_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_euregio_bulletins", "--start-date", "2026-04-01")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 4, 1)
        assert kwargs["end"] == date(2026, 4, 16)

    # ------------------------------------------------------------------
    # --commit / --force
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_commit_disables_dry_run(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--commit forwards dry_run=False and prints the success summary."""
        mock_run.return_value = _make_successful_run(
            records_created=5, records_updated=1
        )

        call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--commit")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is False

        out = capsys.readouterr().out
        assert "5 created" in out
        assert "1 updated" in out
        assert "Dry-run" not in out

    @patch(PATCH_PIPELINE)
    def test_force_flag_forwarded(self, mock_run: MagicMock) -> None:
        """--force forwards force=True to the pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_euregio_bulletins", "--date", "2026-03-15", "--commit", "--force"
        )

        _, kwargs = mock_run.call_args
        assert kwargs["force"] is True

    # ------------------------------------------------------------------
    # --delay
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_delay_defaults_to_zero(self, mock_run: MagicMock) -> None:
        """Bare invocation forwards delay=0.0 — no throttling by default."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["delay"] == 0.0

    @patch(PATCH_PIPELINE)
    def test_delay_forwarded_to_pipeline(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--delay forwards a positive float and surfaces in the banner."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_euregio_bulletins", "--date", "2026-03-15", "--delay", "1.5"
        )

        _, kwargs = mock_run.call_args
        assert kwargs["delay"] == 1.5
        assert "1.5s" in capsys.readouterr().out

    def test_negative_delay_rejected(self) -> None:
        """Negative --delay values are rejected at argparse time."""
        with pytest.raises(CommandError, match="delay must be non-negative"):
            call_command(
                "fetch_euregio_bulletins", "--date", "2026-03-15", "--delay", "-1"
            )

    # ------------------------------------------------------------------
    # triggered_by label & error paths
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_sets_triggered_by(self, mock_run: MagicMock) -> None:
        """The triggered_by label identifies the command in PipelineRun history."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["triggered_by"] == "fetch_euregio_bulletins command"

    @patch(PATCH_PIPELINE)
    def test_raises_on_failed_run(self, mock_run: MagicMock) -> None:
        """CommandError is raised when run_euregio_pipeline returns a failed run."""
        mock_run.return_value = _make_failed_run("connection refused")

        with pytest.raises(CommandError, match="connection refused"):
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--commit")

    @patch(PATCH_PIPELINE)
    def test_raises_on_pipeline_exception(self, mock_run: MagicMock) -> None:
        """CommandError wraps an unexpected exception from run_euregio_pipeline."""
        mock_run.side_effect = RuntimeError("unexpected CDN error")

        with pytest.raises(CommandError, match="unexpected CDN error"):
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--commit")

    @patch(PATCH_PIPELINE)
    def test_raises_when_records_failed_nonzero(self, mock_run: MagicMock) -> None:
        """records_failed > 0 surfaces as a non-zero exit (CommandError)."""
        mock_run.return_value = _make_failed_records_run(records_failed=3)

        with pytest.raises(CommandError, match="failure"):
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--commit")

    # ------------------------------------------------------------------
    # --source / --stash
    # ------------------------------------------------------------------

    @patch(PATCH_PIPELINE)
    def test_default_source_passes_no_base_url(self, mock_run: MagicMock) -> None:
        """Bare invocation passes ``base_url=None`` so the live setting wins."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] is None
        assert kwargs["on_fetched"] is None

    @override_settings(
        EUREGIO_API_LOCAL_MIRROR_URL="http://localhost:8000/dev/euregio-mirror"
    )
    @patch(PATCH_PIPELINE)
    def test_source_local_mirror_forwards_setting_url(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--source local-mirror`` forwards EUREGIO_API_LOCAL_MIRROR_URL."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_euregio_bulletins",
            "--date",
            "2026-03-15",
            "--source",
            "local-mirror",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] == "http://localhost:8000/dev/euregio-mirror"

    @override_settings(EUREGIO_API_LOCAL_MIRROR_URL="")
    @patch(PATCH_PIPELINE)
    def test_source_local_mirror_without_setting_raises(
        self, mock_run: MagicMock
    ) -> None:
        """Empty/unset EUREGIO_API_LOCAL_MIRROR_URL → CommandError naming the setting."""
        with pytest.raises(CommandError, match="EUREGIO_API_LOCAL_MIRROR_URL"):
            call_command(
                "fetch_euregio_bulletins",
                "--date",
                "2026-03-15",
                "--source",
                "local-mirror",
            )

        mock_run.assert_not_called()

    def test_source_rejects_unknown_value(self) -> None:
        """argparse rejects --source values outside {live, local-mirror}."""
        with pytest.raises(CommandError, match="invalid choice"):
            call_command("fetch_euregio_bulletins", "--source", "remote-cdn")

    @patch(PATCH_PIPELINE)
    def test_stash_off_by_default(self, mock_run: MagicMock) -> None:
        """Without --stash, on_fetched is None."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_euregio_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["on_fetched"] is None

    @patch(PATCH_PIPELINE)
    def test_stash_collects_and_writes_archive(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--stash`` flushes collected records to EUREGIO_ARCHIVE_PATH."""
        archive_path = tmp_path / "euregio.ndjson"
        collected_records = [
            {
                "bulletinID": "b1",
                "customData": {"ALBINA": {"mainDate": "2026-03-15"}},
                "validTime": {
                    "startTime": "2026-03-15T16:00:00Z",
                    "endTime": "2026-03-16T16:00:00Z",
                },
                "regions": [{"regionID": "AT-07-01"}],
            },
            {
                "bulletinID": "b2",
                "customData": {"ALBINA": {"mainDate": "2026-03-16"}},
                "validTime": {
                    "startTime": "2026-03-16T16:00:00Z",
                    "endTime": "2026-03-17T16:00:00Z",
                },
                "regions": [{"regionID": "IT-32-BZ-01"}],
            },
        ]

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                for record in collected_records:
                    on_fetched(record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--stash")

        # Archive was written with both records.
        import json

        archived = [
            json.loads(line)
            for line in archive_path.read_text().splitlines()
            if line.strip()
        ]
        assert {r["bulletinID"] for r in archived} == {"b1", "b2"}

        out = capsys.readouterr().out
        assert "Stashed 2 fetched bulletin(s)" in out

    @patch(PATCH_PIPELINE)
    def test_stash_merges_with_existing_archive(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """``--stash`` overlays new records onto the existing archive."""
        import json

        archive_path = tmp_path / "euregio.ndjson"
        existing_record = {
            "bulletinID": "old",
            "validTime": {
                "startTime": "2026-03-10T16:00:00Z",
                "endTime": "2026-03-11T16:00:00Z",
            },
        }
        archive_path.write_text(json.dumps(existing_record) + "\n")

        new_record = {
            "bulletinID": "new",
            "validTime": {
                "startTime": "2026-03-15T16:00:00Z",
                "endTime": "2026-03-16T16:00:00Z",
            },
        }

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                on_fetched(new_record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--stash")

        archived = [
            json.loads(line)
            for line in archive_path.read_text().splitlines()
            if line.strip()
        ]
        # Archive is sorted ascending by validTime.startTime.
        assert [r["bulletinID"] for r in archived] == ["old", "new"]

    @patch(PATCH_PIPELINE)
    def test_stash_independent_of_commit(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """``--stash`` writes the archive regardless of --commit."""
        import json

        archive_path = tmp_path / "euregio.ndjson"

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            on_fetched = kwargs.get("on_fetched")
            assert on_fetched is not None
            on_fetched(  # type: ignore[operator]
                {
                    "bulletinID": "x",
                    "validTime": {
                        "startTime": "2026-03-15T16:00:00Z",
                        "endTime": "2026-03-16T16:00:00Z",
                    },
                }
            )
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(EUREGIO_ARCHIVE_PATH=archive_path):
            # Read-only run (no --commit) + --stash: archive still written.
            call_command("fetch_euregio_bulletins", "--date", "2026-03-15", "--stash")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True
        assert archive_path.exists()
        archived = [
            json.loads(line)
            for line in archive_path.read_text().splitlines()
            if line.strip()
        ]
        assert len(archived) == 1
