"""
tests/bulletins/management/commands/test_seed_test_week.py — seed_test_week tests.

Covers the command contract from ``docs/management-commands.md``:
  - Runs with no arguments (the bare invocation is a useful dry run).
  - Never alters data by default; only ``--commit`` writes.
  - Refuses to run when ``DEBUG`` is off, so it cannot touch a production DB.
  - Fails early and with a copy-pasteable fix when the region reference data
    the week depends on has not been loaded.
  - Exits non-zero when any record fails to ingest, including a partially
    failed batch.
  - Respects ``--verbosity``.

The corpus's own invariants (target-day resolution, region partitioning,
morning supersession, danger spread) live in
``tests/bulletins/services/test_golden_week.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from pytest_django import DjangoDbBlocker

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.golden_week import (
    SOURCE_SLF,
    GoldenWeekResult,
    select_records,
)
from apps.bulletins.services.render_model import RenderModelBuildError

# Region fixtures the week spans — CH (SLF), AT + IT (ALBINA), FR (Météo-France).
_REGION_FIXTURES = ("eaws_CH", "eaws_AT", "eaws_IT", "eaws_FR")


@pytest.fixture(scope="class")
def _regions(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Load region fixtures once per command-test class, then clean the worker DB."""
    del django_db_setup
    with django_db_blocker.unblock():
        call_command("loaddata", *_REGION_FIXTURES, verbosity=0)

    yield

    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)


@pytest.fixture(scope="module")
def one_slf_record() -> dict:
    """Return one real selected bulletin for the render-failure contract test."""
    return select_records(SOURCE_SLF)[0]


@pytest.fixture(scope="class")
def _loaded_golden_week(
    _regions: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Perform the first committed load once for the commit-contract tests."""
    del _regions
    with django_db_blocker.unblock():
        call_command("seed_test_week", commit=True, verbosity=0)
    yield


@pytest.mark.django_db
class TestGuards:
    """The command refuses to run outside the situations it is safe in."""

    def test_requires_debug(self) -> None:
        """With DEBUG off the command refuses rather than touching the DB."""
        with override_settings(DEBUG=False), pytest.raises(CommandError, match="DEBUG"):
            call_command("seed_test_week", verbosity=0)

    def test_missing_region_data_is_a_clear_error(self) -> None:
        """An unseeded database fails early, naming the loaddata command.

        ``upsert_bulletin`` only raises when a bulletin resolves *none* of its
        regions, so without this guard an empty DB would produce a confusing
        partial load rather than an actionable error.
        """
        with pytest.raises(CommandError, match="loaddata"):
            call_command("seed_test_week", verbosity=0)


@pytest.mark.django_db
@pytest.mark.xdist_group(name="seed_week_read_only")
@pytest.mark.usefixtures("_regions")
class TestReadOnlyByDefault:
    """No writes happen without --commit."""

    def test_bare_invocation_writes_nothing(self) -> None:
        """Running with no arguments creates no Bulletin and no PipelineRun.

        The dry run deliberately does not even record a PipelineRun — a
        read-only probe should leave no trace at all.
        """
        call_command("seed_test_week", verbosity=0)
        assert Bulletin.objects.count() == 0
        assert PipelineRun.objects.count() == 0

    def test_dry_run_reports_what_it_would_load(self) -> None:
        """The dry run names the week and reports a non-zero selection."""
        buffer = _run_capturing_stdout()
        assert "2026-02-09" in buffer
        assert "2026-02-15" in buffer
        assert "record(s) would be loaded" in buffer


@pytest.mark.django_db
@pytest.mark.xdist_group(name="seed_week_commit")
@pytest.mark.usefixtures("_loaded_golden_week")
class TestCommit:
    """--commit persists the corpus and records the run."""

    def test_commit_creates_bulletins_and_a_successful_run(self) -> None:
        """Rows are written and the PipelineRun is marked successful."""
        assert Bulletin.objects.exists()
        run = PipelineRun.objects.get()
        assert run.status == PipelineRun.Status.SUCCESS
        assert run.records_created == Bulletin.objects.count()
        assert run.triggered_by.startswith("seed_test_week:")

    def test_rerun_updates_rather_than_duplicating(self) -> None:
        """Loading twice is idempotent — the second run updates in place.

        ``upsert_bulletin`` keys on ``bulletinID``, so re-seeding a populated
        database must not multiply the corpus.
        """
        first = Bulletin.objects.count()

        call_command("seed_test_week", commit=True, verbosity=0)
        assert Bulletin.objects.count() == first

        latest = PipelineRun.objects.order_by("-started_at").first()
        assert latest is not None
        assert latest.records_created == 0
        assert latest.records_updated == first


@pytest.mark.django_db
@pytest.mark.xdist_group(name="seed_week_failures")
@pytest.mark.usefixtures("_regions")
class TestFailureReporting:
    """A partially failed batch is a command failure (contract rule 4)."""

    def test_partial_failure_exits_non_zero(self) -> None:
        """A non-zero failed count raises, so cron and CI can detect it."""
        partial = GoldenWeekResult(selected={"SLF": 2}, created=1, updated=0, failed=1)
        with (
            mock.patch(
                "apps.bulletins.management.commands.seed_test_week.load_golden_week",
                return_value=partial,
            ),
            pytest.raises(CommandError, match="completed with failures"),
        ):
            call_command("seed_test_week", commit=True, verbosity=0)

    def test_render_model_failure_fails_the_command(
        self, one_slf_record: dict
    ) -> None:
        """A stored-but-broken record fails the command too.

        ``upsert_bulletin`` does not raise when a render model cannot be built —
        it stores a version-0 error sentinel and bumps ``run.records_failed``.
        Those records must still fail the command, or a batch that silently
        degraded to error sentinels would report success.
        """
        with (
            mock.patch(
                "apps.bulletins.services.golden_week.select_records",
                side_effect=([one_slf_record], [], []),
            ),
            mock.patch(
                "apps.bulletins.services.slf_fetcher.build_render_model",
                side_effect=RenderModelBuildError("bad payload"),
            ),
            pytest.raises(CommandError, match="completed with failures"),
        ):
            call_command("seed_test_week", commit=True, verbosity=0)

    def test_load_error_marks_the_run_failed(self) -> None:
        """An exception mid-load is surfaced and the PipelineRun records it."""
        with (
            mock.patch(
                "apps.bulletins.management.commands.seed_test_week.load_golden_week",
                side_effect=RuntimeError("archive unreadable"),
            ),
            pytest.raises(CommandError, match="archive unreadable"),
        ):
            call_command("seed_test_week", commit=True, verbosity=0)

        run = PipelineRun.objects.get()
        assert run.status == PipelineRun.Status.FAILED
        assert "archive unreadable" in run.error_message


@pytest.mark.django_db
@pytest.mark.xdist_group(name="seed_week_verbosity")
@pytest.mark.usefixtures("_regions")
class TestVerbosity:
    """--verbosity is respected."""

    def test_verbosity_zero_is_silent(self) -> None:
        """At verbosity 0 the per-provider breakdown is suppressed."""
        buffer = _run_capturing_stdout(verbosity=0)
        assert "Records selected" not in buffer

    def test_verbosity_one_lists_each_provider(self) -> None:
        """At the default verbosity each provider's count is reported."""
        buffer = _run_capturing_stdout(verbosity=1)
        assert "Records selected" in buffer
        for source in ("SLF", "ALBINA", "METEOFRANCE"):
            assert source in buffer


def _run_capturing_stdout(**options: object) -> str:
    """Run ``seed_test_week`` in dry-run mode and return its stdout.

    Args:
        **options: Extra options forwarded to ``call_command``.

    Returns:
        Everything the command wrote to stdout.

    """
    import io

    buffer = io.StringIO()
    call_command("seed_test_week", stdout=buffer, **options)
    return buffer.getvalue()
