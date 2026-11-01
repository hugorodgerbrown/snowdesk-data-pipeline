"""
tests/bulletins/management/commands/test_rebuild_render_models.py — Tests
for the rebuild_render_models management command.

Covers (SNOW-1346: the command enqueues one task per bulletin; tests run
under ImmediateBackend, so each task runs inline):
  - Default mode (no --commit) is read-only and enqueues nothing.
  - --commit enqueues one task per stale row; a second run enqueues none.
  - --all and --bulletin-id force; --skip-day-ratings drops the refresh.
  - A failing task does not abort the batch; the run exits non-zero.
  - --commit + default selection: only stale rows are rebuilt.
  - --commit --all: every row is rebuilt.
  - --commit --bulletin-id: exactly one row is rebuilt.
  - --bulletin-id with unknown id: raises CommandError.
  - After a successful --commit run, render_model_version == RENDER_MODEL_VERSION.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.bulletins.models import Bulletin, RegionDayRating
from apps.bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from apps.core.models import CommandRun
from tests.factories import BulletinFactory, MicroRegionFactory, RegionBulletinFactory


def _make_bulletin(
    render_model_version: int = 0,
    bulletin_id: str | None = None,
) -> Bulletin:
    """Create a Bulletin with specified render_model_version."""
    kwargs: dict = {
        "render_model_version": render_model_version,
        "render_model": {"version": render_model_version, "traits": []},
        "issued_at": datetime(2025, 3, 15, 8, 0, tzinfo=UTC),
        "valid_from": datetime(2025, 3, 15, 7, 0, tzinfo=UTC),
        "valid_to": datetime(2025, 3, 16, 7, 0, tzinfo=UTC),
        "held": {
            "bulletinID": bulletin_id or "test",
            "dangerRatings": [{"mainValue": "low"}],
            "avalancheProblems": [],
            "customData": {"CH": {}},
        },
    }
    if bulletin_id:
        kwargs["bulletin_id"] = bulletin_id
    return BulletinFactory.create(**kwargs)


@pytest.mark.django_db
class TestRebuildRenderModelsDefault:
    """Tests for default (stale-only, --commit) mode."""

    def test_rebuilds_stale_rows_only(self) -> None:
        """With --commit, only rows with version < RENDER_MODEL_VERSION change."""
        stale = _make_bulletin(render_model_version=0, bulletin_id="stale-001")
        fresh = _make_bulletin(
            render_model_version=RENDER_MODEL_VERSION, bulletin_id="fresh-001"
        )

        call_command("rebuild_render_models", commit=True, verbosity=0)

        stale.refresh_from_db()
        fresh.refresh_from_db()

        assert stale.render_model_version == RENDER_MODEL_VERSION
        assert fresh.render_model_version == RENDER_MODEL_VERSION

    def test_nothing_to_do_when_all_current(self) -> None:
        """No rows are touched when all bulletins are up-to-date."""
        _make_bulletin(
            render_model_version=RENDER_MODEL_VERSION, bulletin_id="fresh-002"
        )

        call_command("rebuild_render_models", commit=True, verbosity=0)

        assert (
            Bulletin.objects.filter(
                render_model_version__lt=RENDER_MODEL_VERSION
            ).count()
            == 0
        )

    def test_stale_row_gets_version_updated(self) -> None:
        """After --commit rebuild, render_model_version equals RENDER_MODEL_VERSION."""
        b = _make_bulletin(render_model_version=0, bulletin_id="stale-003")

        call_command("rebuild_render_models", commit=True, verbosity=0)

        b.refresh_from_db()
        assert b.render_model_version == RENDER_MODEL_VERSION

    def test_stale_row_render_model_is_dict_with_version_key(self) -> None:
        """After --commit rebuild, render_model has a 'version' key."""
        b = _make_bulletin(render_model_version=0, bulletin_id="stale-004")

        call_command("rebuild_render_models", commit=True, verbosity=0)

        b.refresh_from_db()
        assert isinstance(b.render_model, dict)
        assert b.render_model.get("version") == RENDER_MODEL_VERSION

    def test_a_v9_row_gains_the_derived_block_and_its_levels(self) -> None:
        """SNOW-1341: the Deploy-Step fills derived and Bulletin.danger_levels."""
        b = _make_bulletin(render_model_version=9, bulletin_id="v9-001")
        assert b.danger_levels == ""

        call_command("rebuild_render_models", commit=True, verbosity=0)

        b.refresh_from_db()
        assert b.render_model["derived"]["danger_levels"]["levels"] == ["low"]
        assert b.danger_levels == "|low|"


@pytest.mark.django_db
class TestRebuildRenderModelsAll:
    """Tests for --all mode."""

    def test_all_flag_rebuilds_every_row(self) -> None:
        """--all --commit rebuilds all bulletins regardless of version."""
        _make_bulletin(render_model_version=0, bulletin_id="all-stale-001")
        _make_bulletin(
            render_model_version=RENDER_MODEL_VERSION, bulletin_id="all-fresh-001"
        )

        call_command(
            "rebuild_render_models", rebuild_all=True, commit=True, verbosity=0
        )

        for b in Bulletin.objects.all():
            assert b.render_model_version == RENDER_MODEL_VERSION


@pytest.mark.django_db
class TestRebuildRenderModelsBulletinId:
    """Tests for --bulletin-id mode."""

    def test_rebuilds_exactly_one_row(self) -> None:
        """--bulletin-id --commit rebuilds only the specified bulletin."""
        target = _make_bulletin(render_model_version=0, bulletin_id="target-001")
        other = _make_bulletin(render_model_version=0, bulletin_id="other-001")

        call_command(
            "rebuild_render_models",
            bulletin_id="target-001",
            commit=True,
            verbosity=0,
        )

        target.refresh_from_db()
        other.refresh_from_db()

        assert target.render_model_version == RENDER_MODEL_VERSION
        # Other row untouched.
        assert other.render_model_version == 0

    def test_raises_command_error_on_unknown_id(self) -> None:
        """Unknown bulletin_id raises CommandError (with or without --commit)."""
        with pytest.raises(CommandError, match="No bulletin found"):
            call_command(
                "rebuild_render_models",
                bulletin_id="does-not-exist",
                verbosity=0,
            )


@pytest.mark.django_db
class TestRebuildRenderModelsReadOnly:
    """Tests for the default (no --commit) read-only mode."""

    def test_default_run_leaves_db_untouched(self) -> None:
        """Without --commit the command does not write to the database."""
        b = _make_bulletin(render_model_version=0, bulletin_id="ro-001")

        call_command("rebuild_render_models", verbosity=0)

        b.refresh_from_db()
        assert b.render_model_version == 0

    def test_default_run_reports_correct_count(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The read-only run reports the number of bulletins it would process."""
        _make_bulletin(render_model_version=0, bulletin_id="ro-count-001")
        _make_bulletin(render_model_version=0, bulletin_id="ro-count-002")

        call_command("rebuild_render_models", verbosity=1)

        captured = capsys.readouterr()
        # Should mention how many it would rebuild.
        assert "2" in captured.out or "2" in captured.err

    def test_default_run_prints_read_only_banner_and_hint(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """The read-only run flags itself in the heading and prompts for --commit."""
        _make_bulletin(render_model_version=0, bulletin_id="ro-banner-001")

        call_command("rebuild_render_models", verbosity=1)

        out = capsys.readouterr().out
        assert "[READ-ONLY]" in out
        assert "--commit to persist" in out


_BUILD = "apps.bulletins.services.render_rebuild.build_render_model"
_TASK = "apps.bulletins.management.commands.rebuild_render_models.rebuild_bulletin_task"


@pytest.mark.django_db
class TestRebuildRenderModelsEnqueue:
    """SNOW-1346: the command enqueues one task per row; the task works."""

    def test_a_bare_run_enqueues_nothing(self) -> None:
        """Without --commit no task is enqueued."""
        _make_bulletin(render_model_version=0, bulletin_id="enq-ro-001")

        with patch(_TASK) as task:
            call_command("rebuild_render_models", verbosity=0)

        task.enqueue.assert_not_called()

    def test_commit_enqueues_one_task_per_stale_row(self) -> None:
        """Each stale row is one task, unforced, rating its pairs."""
        stale = [
            _make_bulletin(render_model_version=0, bulletin_id=f"enq-{i:03d}")
            for i in range(2)
        ]
        _make_bulletin(render_model_version=RENDER_MODEL_VERSION, bulletin_id="cur")

        with patch(_TASK) as task:
            call_command("rebuild_render_models", commit=True, verbosity=0)

        calls = sorted(call.args for call in task.enqueue.call_args_list)
        assert calls == sorted((b.pk, False, True) for b in stale)

    def test_all_bulletin_id_and_skip_day_ratings_shape_the_task(self) -> None:
        """--all and --bulletin-id force; --skip-day-ratings drops the refresh."""
        b = _make_bulletin(render_model_version=RENDER_MODEL_VERSION, bulletin_id="f")

        with patch(_TASK) as task:
            call_command(
                "rebuild_render_models", rebuild_all=True, commit=True, verbosity=0
            )
            call_command(
                "rebuild_render_models",
                bulletin_id="f",
                skip_day_ratings=True,
                commit=True,
                verbosity=0,
            )

        assert [call.args for call in task.enqueue.call_args_list] == [
            (b.pk, True, True),
            (b.pk, True, False),
        ]

    def test_a_second_commit_enqueues_nothing(self) -> None:
        """The first run brought every row current: nothing is a candidate."""
        b = _make_bulletin(render_model_version=0, bulletin_id="twice-001")
        call_command("rebuild_render_models", commit=True, verbosity=0)
        b.refresh_from_db()
        stamp = b.updated_at

        with patch(_TASK) as task:
            call_command("rebuild_render_models", commit=True, verbosity=0)

        task.enqueue.assert_not_called()
        b.refresh_from_db()
        assert b.updated_at == stamp

    def test_the_run_is_registered_with_its_tallies(self) -> None:
        """enqueued and failed are tallied, zeros included."""
        _make_bulletin(render_model_version=0, bulletin_id="reg-001")

        call_command("rebuild_render_models", commit=True, verbosity=0)

        run = CommandRun.objects.get(command="rebuild_render_models")
        assert run.counts == {"enqueued": 1, "failed": 0}

    def test_the_walk_loads_neither_caaml_nor_regions(self) -> None:
        """The command reads only the label's columns; the task reads the rest."""
        region = MicroRegionFactory.create(region_id="CH-slim-test")
        for index in range(3):
            b = _make_bulletin(render_model_version=0, bulletin_id=f"slim-{index}")
            RegionBulletinFactory.create(bulletin=b, region=region)

        with patch(_TASK), CaptureQueriesContext(connection) as queries:
            call_command("rebuild_render_models", commit=True, verbosity=0)

        for query in queries.captured_queries:
            assert '"ingest_sourcebulletin"' not in query["sql"]
            assert '"regions_microregion"' not in query["sql"]


@pytest.mark.django_db
class TestRebuildRenderModelsErrorHandling:
    """Tests for error handling during rebuild."""

    def test_error_during_build_does_not_abort_run(self) -> None:
        """A build error fails its task; the next bulletin is still rebuilt."""
        failing = _make_bulletin(render_model_version=0, bulletin_id="error-001")
        passing = _make_bulletin(render_model_version=0, bulletin_id="error-002")
        real = build_render_model

        def _flaky(props: dict) -> dict:
            if props["bulletinID"] == "error-002":
                return real(props)
            raise RenderModelBuildError("simulated failure")

        with (
            patch(_BUILD, side_effect=_flaky),
            pytest.raises(CommandError, match="1 bulletin rebuild task"),
        ):
            call_command("rebuild_render_models", commit=True, verbosity=0)

        failing.refresh_from_db()
        passing.refresh_from_db()
        assert failing.render_model["error"] == "simulated failure"
        assert passing.render_model_version == RENDER_MODEL_VERSION
        run = CommandRun.objects.get(command="rebuild_render_models")
        assert run.counts == {"enqueued": 2, "failed": 1}

    def test_error_summary_printed_and_exits_nonzero(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """When failures occur, command prints summary and exits non-zero."""
        _make_bulletin(render_model_version=0, bulletin_id="fail-sum-001")
        _make_bulletin(render_model_version=0, bulletin_id="fail-sum-002")

        with patch(_BUILD, side_effect=RenderModelBuildError("simulated failure")):
            with pytest.raises(CommandError):
                call_command("rebuild_render_models", commit=True, verbosity=1)

        assert "Enqueued 2 bulletin(s); 2 failed." in capsys.readouterr().out


@pytest.mark.django_db
class TestRebuildRenderModelsDayRatings:
    """Tests for day-rating refresh in each task."""

    def test_commit_refreshes_day_ratings(self) -> None:
        """After --commit, RegionDayRating rows are created for rebuilt bulletins."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        b = _make_bulletin(render_model_version=0, bulletin_id="dr-rebuild-001")
        RegionBulletinFactory.create(bulletin=b, region=region)

        call_command("rebuild_render_models", commit=True, verbosity=0)

        assert RegionDayRating.objects.filter(region=region).exists()

    def test_skip_day_ratings_flag(self) -> None:
        """--skip-day-ratings prevents day-rating rows from being created."""
        region = MicroRegionFactory.create(region_id="CH-skip-test")
        b = _make_bulletin(render_model_version=0, bulletin_id="dr-skip-001")
        RegionBulletinFactory.create(bulletin=b, region=region)

        call_command(
            "rebuild_render_models", commit=True, skip_day_ratings=True, verbosity=0
        )

        b.refresh_from_db()
        assert b.render_model_version == RENDER_MODEL_VERSION
        assert not RegionDayRating.objects.filter(region=region).exists()

    def test_day_rating_failure_exits_nonzero_and_rolls_back(self) -> None:
        """A failed recompute fails its task and leaves the row stale.

        The row write and its recompute share one transaction (SNOW-1346),
        so the bulletin is enqueued again by the next run.
        """
        region = MicroRegionFactory.create(region_id="CH-dr-fail")
        b = _make_bulletin(render_model_version=0, bulletin_id="dr-fail-001")
        RegionBulletinFactory.create(bulletin=b, region=region)

        with patch(
            "apps.bulletins.services.render_rebuild.recompute_region_day",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(CommandError, match="failed"):
                call_command("rebuild_render_models", commit=True, verbosity=0)

        b.refresh_from_db()
        assert b.render_model_version == 0


@pytest.mark.django_db
class TestRebuildRenderModelsStreaming:
    """SNOW-602: every row is visited exactly once, even across chunk boundaries.

    The default queryset filters on ``render_model_version < RENDER_MODEL_VERSION``
    and the loop body updates that very column — the shape that broke the old
    OFFSET/LIMIT batching (a row could drop out of a later page's re-queried
    slice once an earlier page's write shrank the filtered set).
    """

    def test_every_stale_row_rebuilt_exactly_once_across_chunk_boundaries(
        self,
    ) -> None:
        """A row count exceeding --batch-size is still rebuilt exactly once each."""
        bulletins = [
            _make_bulletin(render_model_version=0, bulletin_id=f"chunked-{i:03d}")
            for i in range(5)
        ]

        call_command(
            "rebuild_render_models",
            commit=True,
            batch_size=2,
            verbosity=0,
        )

        for b in bulletins:
            b.refresh_from_db()
            assert b.render_model_version == RENDER_MODEL_VERSION

    def test_stdout_carries_processed_bulletin_ids_in_descending_order(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """Each processed bulletin's bulletin_id is printed, newest pk first."""
        first = _make_bulletin(render_model_version=0, bulletin_id="countdown-001")
        second = _make_bulletin(render_model_version=0, bulletin_id="countdown-002")
        expected = [
            b.bulletin_id
            for b in Bulletin.objects.filter(pk__in=[first.pk, second.pk]).order_by(
                "-id"
            )
        ]

        call_command("rebuild_render_models", commit=True, verbosity=1)

        out_lines = capsys.readouterr().out.splitlines()
        # Each line is ``<pk> <source> <day> <bulletin_id>`` (Bulletin.row_label).
        printed = [
            line.rsplit(" ", 1)[-1]
            for line in out_lines
            if line.rsplit(" ", 1)[-1] in {"countdown-001", "countdown-002"}
        ]
        assert printed == expected
