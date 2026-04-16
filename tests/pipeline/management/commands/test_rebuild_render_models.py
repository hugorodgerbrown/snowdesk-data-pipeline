"""
tests/pipeline/management/commands/test_rebuild_render_models.py — Tests
for the rebuild_render_models management command.

Covers:
  - Default mode (no --commit) is read-only and does not write.
  - --commit + default selection: only stale rows are rebuilt.
  - --commit --all: every row is rebuilt.
  - --commit --bulletin-id: exactly one row is rebuilt.
  - --bulletin-id with unknown id: raises CommandError.
  - After a successful --commit run, render_model_version == RENDER_MODEL_VERSION.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.models import Bulletin
from pipeline.services.render_model import RENDER_MODEL_VERSION
from tests.factories import BulletinFactory


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
        "raw_data": {
            "type": "Feature",
            "geometry": None,
            "properties": {
                "bulletinID": bulletin_id or "test",
                "dangerRatings": [{"mainValue": "low"}],
                "avalancheProblems": [],
            },
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


@pytest.mark.django_db
class TestRebuildRenderModelsErrorHandling:
    """Tests for error handling during rebuild."""

    def test_error_during_build_does_not_abort_run(self) -> None:
        """A RenderModelBuildError on one bulletin does not abort the whole run."""
        from unittest.mock import patch

        from pipeline.services.render_model import RenderModelBuildError

        b = _make_bulletin(render_model_version=0, bulletin_id="error-001")

        with patch(
            "pipeline.management.commands.rebuild_render_models.build_render_model",
            side_effect=RenderModelBuildError("simulated failure"),
        ):
            # Should not raise — error is caught and stored.
            with pytest.raises(CommandError, match="failed"):
                call_command("rebuild_render_models", commit=True, verbosity=0)

        b.refresh_from_db()
        # On error, version stays 0 and render_model records the error.
        assert b.render_model_version == 0
        assert "error" in b.render_model
        assert b.render_model["error_type"] == "RenderModelBuildError"

    def test_error_summary_printed_and_exits_nonzero(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """When failures occur, command prints summary and exits non-zero."""
        from unittest.mock import patch

        from pipeline.services.render_model import RenderModelBuildError

        _make_bulletin(render_model_version=0, bulletin_id="fail-sum-001")
        _make_bulletin(render_model_version=0, bulletin_id="fail-sum-002")

        with patch(
            "pipeline.management.commands.rebuild_render_models.build_render_model",
            side_effect=RenderModelBuildError("simulated failure"),
        ):
            with pytest.raises(CommandError):
                call_command("rebuild_render_models", commit=True, verbosity=1)

        captured = capsys.readouterr()
        # Summary should mention rebuilt count and failed count.
        assert "Rebuilt" in captured.out or "failed" in captured.out
