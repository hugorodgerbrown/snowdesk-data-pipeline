"""
tests/pipeline/management/commands/test_fetch_bulletins.py — Tests for fetch_bulletins.

Covers argument defaults (no-args invocation), --date / --start-date /
--end-date semantics, --commit / --force forwarding, error handling, and
the records_failed exit-code path.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from pipeline.models import PipelineRun
from pipeline.services.slf_archive import read_archive, write_archive
from tests.factories import BulletinFactory, PipelineRunFactory


def _make_successful_run(**overrides: int) -> PipelineRun:
    """
    Build a PipelineRun in SUCCESS state for mocking run_pipeline return.

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
    Build a PipelineRun in FAILED state for mocking run_pipeline return.

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
        A persisted PipelineRun whose status is success but with
        records_failed > 0 — i.e. one or more render-model build errors.

    """
    return PipelineRunFactory.create(
        status=PipelineRun.Status.SUCCESS,
        records_created=5,
        records_updated=0,
        records_failed=records_failed,
    )


PATCH_TARGET = "pipeline.management.commands.fetch_bulletins.run_pipeline"


@pytest.mark.django_db
class TestFetchBulletinsCommand:
    """Tests for the fetch_bulletins management command."""

    # ------------------------------------------------------------------
    # Defaults — bare invocation
    # ------------------------------------------------------------------

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_TARGET)
    def test_no_args_empty_db_falls_back_to_season_start(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Empty DB: bare invocation runs from SEASON_START_DATE to today."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "pipeline.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2025, 11, 1)
        assert kwargs["end"] == date(2026, 4, 16)
        # Read-only by default — dry_run forwarded as True.
        assert kwargs["dry_run"] is True
        assert kwargs["force"] is False
        # Banner surfaces the backstop source.
        assert "SEASON_START_DATE backstop" in capsys.readouterr().out

    @patch(PATCH_TARGET)
    def test_no_args_uses_latest_bulletin_valid_from_day(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Populated DB: start defaults to latest bulletin's valid_from day."""
        mock_run.return_value = _make_successful_run()
        # Latest bulletin valid from midday 2026-04-15 UTC.
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )
        # Older bulletin — must be ignored by the MAX aggregate.
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 10, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 11, 12, 0, tzinfo=UTC),
        )

        with patch(
            "pipeline.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins")

        _, kwargs = mock_run.call_args
        # Same-day overlap so earlier-in-day issues get re-fetched.
        assert kwargs["start"] == date(2026, 4, 15)
        assert kwargs["end"] == date(2026, 4, 16)
        assert "from latest bulletin valid_from day" in capsys.readouterr().out

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_TARGET)
    def test_explicit_start_date_overrides_smart_default(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--start-date wins over the latest-bulletin default even with data in DB."""
        mock_run.return_value = _make_successful_run()
        BulletinFactory.create(
            issued_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_from=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
            valid_to=datetime(2026, 4, 16, 12, 0, tzinfo=UTC),
        )

        with patch(
            "pipeline.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins", "--start-date", "2026-01-01")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 1)
        assert kwargs["end"] == date(2026, 4, 16)
        # Explicit source suppresses the start-source suffix.
        out = capsys.readouterr().out
        assert "from latest bulletin" not in out
        assert "SEASON_START_DATE backstop" not in out

    @patch(PATCH_TARGET)
    def test_no_args_does_not_pass_commit(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Bare invocation prints the read-only confirmation message."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins")

        out = capsys.readouterr().out
        assert "Read-only run complete" in out
        assert "--commit to persist" in out

    # ------------------------------------------------------------------
    # --date single-day shortcut
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_date_sets_both_start_and_end(self, mock_run: MagicMock) -> None:
        """--date forwards the same value for start and end."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--date", "2026-01-15")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 1, 15)
        assert kwargs["end"] == date(2026, 1, 15)

    def test_date_conflicts_with_start_date(self) -> None:
        """--date and --start-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_bulletins",
                "--date",
                "2026-01-15",
                "--start-date",
                "2026-01-01",
            )

    def test_date_conflicts_with_end_date(self) -> None:
        """--date and --end-date together raise CommandError."""
        with pytest.raises(CommandError, match="mutually exclusive"):
            call_command(
                "fetch_bulletins",
                "--date",
                "2026-01-15",
                "--end-date",
                "2026-01-31",
            )

    # ------------------------------------------------------------------
    # Range arguments
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_explicit_range_forwarded(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Explicit --start-date / --end-date are forwarded verbatim."""
        mock_run.return_value = _make_successful_run(
            records_created=15, records_updated=3
        )

        call_command(
            "fetch_bulletins",
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
        assert "15 created" in out
        assert "3 updated" in out

    def test_rejects_end_before_start(self) -> None:
        """CommandError when --end-date precedes --start-date."""
        with pytest.raises(CommandError, match="on or after"):
            call_command(
                "fetch_bulletins",
                "--start-date",
                "2026-03-31",
                "--end-date",
                "2026-03-01",
            )

    @override_settings(SEASON_START_DATE=date(2025, 11, 1))
    @patch(PATCH_TARGET)
    def test_end_only_uses_season_start(self, mock_run: MagicMock) -> None:
        """Omitting --start-date falls back to settings.SEASON_START_DATE."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--end-date", "2026-01-31")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2025, 11, 1)
        assert kwargs["end"] == date(2026, 1, 31)

    @patch(PATCH_TARGET)
    def test_start_only_uses_today_for_end(self, mock_run: MagicMock) -> None:
        """Omitting --end-date defaults to today (UTC)."""
        mock_run.return_value = _make_successful_run()

        with patch(
            "pipeline.management.commands.fetch_bulletins.timezone.localdate",
            return_value=date(2026, 4, 16),
        ):
            call_command("fetch_bulletins", "--start-date", "2026-04-01")

        _, kwargs = mock_run.call_args
        assert kwargs["start"] == date(2026, 4, 1)
        assert kwargs["end"] == date(2026, 4, 16)

    # ------------------------------------------------------------------
    # --commit / --force
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_commit_disables_dry_run(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--commit forwards dry_run=False and prints the success summary."""
        mock_run.return_value = _make_successful_run(
            records_created=5, records_updated=2
        )

        call_command("fetch_bulletins", "--date", "2026-03-15", "--commit")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is False

        out = capsys.readouterr().out
        assert "5 created" in out
        assert "2 updated" in out
        assert "Read-only" not in out

    @patch(PATCH_TARGET)
    def test_force_flag_forwarded(self, mock_run: MagicMock) -> None:
        """--force forwards force=True to the pipeline."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--date", "2026-03-15", "--commit", "--force")

        _, kwargs = mock_run.call_args
        assert kwargs["force"] is True

    # ------------------------------------------------------------------
    # Triggered-by label & error paths
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_sets_triggered_by(self, mock_run: MagicMock) -> None:
        """The triggered_by label identifies the command in PipelineRun history."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["triggered_by"] == "fetch_bulletins command"

    @patch(PATCH_TARGET)
    def test_raises_on_failed_run(self, mock_run: MagicMock) -> None:
        """CommandError is raised when run_pipeline returns a failed run."""
        mock_run.return_value = _make_failed_run("connection refused")

        with pytest.raises(CommandError, match="connection refused"):
            call_command("fetch_bulletins", "--date", "2026-03-15", "--commit")

    @patch(PATCH_TARGET)
    def test_raises_on_pipeline_exception(self, mock_run: MagicMock) -> None:
        """CommandError wraps an unexpected exception from run_pipeline."""
        mock_run.side_effect = RuntimeError("unexpected error")

        with pytest.raises(CommandError, match="unexpected error"):
            call_command("fetch_bulletins", "--date", "2026-03-15", "--commit")

    @patch(PATCH_TARGET)
    def test_raises_when_records_failed_nonzero(self, mock_run: MagicMock) -> None:
        """records_failed > 0 surfaces as a non-zero exit (CommandError)."""
        mock_run.return_value = _make_failed_records_run(records_failed=2)

        with pytest.raises(CommandError, match="render-model"):
            call_command("fetch_bulletins", "--date", "2026-03-15", "--commit")

    # ------------------------------------------------------------------
    # --source / --stash
    # ------------------------------------------------------------------

    @patch(PATCH_TARGET)
    def test_default_source_passes_no_base_url(self, mock_run: MagicMock) -> None:
        """Bare invocation passes ``base_url=None`` so the live setting wins."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] is None
        assert kwargs["on_fetched"] is None

    @override_settings(
        SLF_API_LOCAL_MIRROR_URL="http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml"
    )
    @patch(PATCH_TARGET)
    def test_source_local_mirror_forwards_setting_url(
        self, mock_run: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--source local-mirror`` forwards SLF_API_LOCAL_MIRROR_URL."""
        mock_run.return_value = _make_successful_run()

        call_command(
            "fetch_bulletins",
            "--date",
            "2026-03-15",
            "--source",
            "local-mirror",
        )

        _, kwargs = mock_run.call_args
        assert kwargs["base_url"] == (
            "http://localhost:8000/dev/slf-mirror/api/bulletin-list/caaml"
        )
        # Banner surfaces the non-default source so it's obvious in logs.
        assert "SOURCE=LOCAL-MIRROR" in capsys.readouterr().out

    @override_settings(SLF_API_LOCAL_MIRROR_URL="")
    @patch(PATCH_TARGET)
    def test_source_local_mirror_without_setting_raises(
        self, mock_run: MagicMock
    ) -> None:
        """Empty/unset SLF_API_LOCAL_MIRROR_URL → CommandError naming the setting.

        Simulates the production-like environment where development.py
        (which defines the mirror URL) is not loaded. ``--source
        local-mirror`` must abort before invoking the pipeline.
        """
        with pytest.raises(CommandError, match="SLF_API_LOCAL_MIRROR_URL"):
            call_command(
                "fetch_bulletins",
                "--date",
                "2026-03-15",
                "--source",
                "local-mirror",
            )

        # run_pipeline must not be invoked when the source check fails.
        mock_run.assert_not_called()

    def test_source_rejects_unknown_value(self) -> None:
        """argparse rejects --source values outside {live,local-mirror}.

        Django's ``CommandParser`` translates ``argparse.error`` into a
        ``CommandError`` when invoked via ``call_command`` (rather than
        the default ``SystemExit`` you'd see on the command line).
        """
        with pytest.raises(CommandError, match="invalid choice"):
            call_command("fetch_bulletins", "--source", "remote-cluster")

    @patch(PATCH_TARGET)
    def test_stash_off_by_default(self, mock_run: MagicMock) -> None:
        """Without --stash, no on_fetched callback is wired up."""
        mock_run.return_value = _make_successful_run()

        call_command("fetch_bulletins", "--date", "2026-03-15")

        _, kwargs = mock_run.call_args
        assert kwargs["on_fetched"] is None

    @patch(PATCH_TARGET)
    def test_stash_collects_and_writes_archive(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``--stash`` flushes collected records to SLF_ARCHIVE_PATH."""
        archive_path = tmp_path / "archive.ndjson"
        # Side effect: simulate run_pipeline pumping records into on_fetched.
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
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                for record in collected_records:
                    on_fetched(record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            call_command("fetch_bulletins", "--date", "2026-03-15", "--stash")

        # Archive was written with both records.
        archived = list(read_archive(archive_path))
        assert {r["bulletinID"] for r in archived} == {"b1", "b2"}

        out = capsys.readouterr().out
        assert "Stashed 2 fetched bulletin(s)" in out
        assert "STASH" in out  # banner flag

    @patch(PATCH_TARGET)
    def test_stash_merges_with_existing_archive(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """``--stash`` overlays new records onto the existing archive (later wins)."""
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
            on_fetched = kwargs.get("on_fetched")
            if on_fetched is not None:
                on_fetched(new_record)  # type: ignore[operator]
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            call_command("fetch_bulletins", "--date", "2026-03-15", "--stash")

        archived = list(read_archive(archive_path))
        # Archive is sorted ascending by validTime.startTime.
        assert [r["bulletinID"] for r in archived] == ["old", "new"]

    @patch(PATCH_TARGET)
    def test_stash_independent_of_commit(
        self, mock_run: MagicMock, tmp_path: Path
    ) -> None:
        """``--stash`` writes the archive regardless of --commit; both forwarded."""
        archive_path = tmp_path / "archive.ndjson"

        def fake_run_pipeline(**kwargs: object) -> PipelineRun:
            on_fetched = kwargs.get("on_fetched")
            assert on_fetched is not None
            on_fetched(  # type: ignore[operator]
                {
                    "bulletinID": "x",
                    "publicationTime": "2025-03-15T08:00:00Z",
                    "validTime": {
                        "startTime": "2025-03-15T17:00:00Z",
                        "endTime": "2025-03-16T17:00:00Z",
                    },
                    "lang": "en",
                }
            )
            return _make_successful_run()

        mock_run.side_effect = fake_run_pipeline

        with override_settings(SLF_ARCHIVE_PATH=archive_path):
            # Read-only run + stash: archive still written.
            call_command("fetch_bulletins", "--date", "2026-03-15", "--stash")

        _, kwargs = mock_run.call_args
        assert kwargs["dry_run"] is True
        assert archive_path.exists()
        assert len(list(read_archive(archive_path))) == 1
