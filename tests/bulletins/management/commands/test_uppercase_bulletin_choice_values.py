"""
tests/bulletins/management/commands/test_uppercase_bulletin_choice_values.py

Covers the ``uppercase_bulletin_choice_values`` management command
(SNOW-582):
  - Read-only by default (no writes to any field/key without --commit).
  - --commit uppercases legacy lower-case values on PipelineRun.status,
    Bulletin.source, RegionDayRating.source, and the targeted
    Bulletin.render_model["source"] JSON key.
  - Idempotence: a second --commit run selects nothing across every pass.
  - Nothing-to-do path (no eligible rows) exits cleanly.
  - The JSON pass rewrites only the "source" key, leaving the rest of the
    render model untouched.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.bulletins.models import Bulletin, PipelineRun, RegionDayRating
from tests.factories import BulletinFactory, PipelineRunFactory, RegionDayRatingFactory


def _seed_legacy_run(*, value: str = "success") -> PipelineRun:
    """Create a PipelineRun and force its status to a legacy value."""
    run = PipelineRunFactory.create()
    PipelineRun.objects.filter(pk=run.pk).update(status=value)
    return run


def _seed_legacy_bulletin_source(*, value: str = "slf") -> None:
    """Create a Bulletin and force its source column to a legacy value."""
    bulletin = BulletinFactory.create()
    Bulletin.objects.filter(pk=bulletin.pk).update(source=value)


def _seed_legacy_day_rating_source(*, value: str = "albina") -> None:
    """Create a RegionDayRating and force its source to a legacy value."""
    rating = RegionDayRatingFactory.create()
    RegionDayRating.objects.filter(pk=rating.pk).update(source=value)


def _seed_legacy_render_model_source(*, value: str = "meteofrance") -> Bulletin:
    """Create a Bulletin whose render_model["source"] is a legacy value."""
    return BulletinFactory.create(
        render_model={"version": 6, "source": value, "danger": {"key": "low"}}
    )


@pytest.mark.django_db
class TestUppercaseBulletinChoiceValuesCommand:
    """Tests for the uppercase_bulletin_choice_values management command."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no field or JSON key is persisted."""
        _seed_legacy_run(value="success")
        _seed_legacy_bulletin_source(value="slf")
        _seed_legacy_day_rating_source(value="albina")
        bulletin = _seed_legacy_render_model_source(value="meteofrance")

        call_command("uppercase_bulletin_choice_values")

        assert PipelineRun.objects.filter(status="success").count() == 1
        assert Bulletin.objects.filter(source="slf").count() == 1
        assert RegionDayRating.objects.filter(source="albina").count() == 1
        bulletin.refresh_from_db()
        assert bulletin.render_model["source"] == "meteofrance"

    def test_dry_run_reports_the_breakdown_per_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dry run names what it would convert, per field/key and value."""
        _seed_legacy_run(value="success")
        _seed_legacy_bulletin_source(value="slf")
        _seed_legacy_day_rating_source(value="albina")
        _seed_legacy_render_model_source(value="meteofrance")

        call_command("uppercase_bulletin_choice_values")

        out = capsys.readouterr().out
        assert "PipelineRun.status:" in out
        assert "SUCCESS: 1" in out
        assert "Bulletin.source:" in out
        assert "SLF: 1" in out
        assert "RegionDayRating.source:" in out
        assert "ALBINA: 1" in out
        assert 'Bulletin.render_model["source"]:' in out
        assert "METEOFRANCE: 1" in out

    def test_commit_uppercases_every_field_and_the_json_key(self) -> None:
        """--commit rewrites every legacy value, including the JSON key."""
        # PipelineRun.status="pending" (a legacy value) is indistinguishable
        # from the factory's own PENDING default, and every BulletinFactory
        # call below creates its own PipelineRun via a sub-factory — so this
        # asserts against the specific seeded objects, not an aggregate count.
        run = _seed_legacy_run(value="pending")
        source_bulletin = BulletinFactory.create()
        Bulletin.objects.filter(pk=source_bulletin.pk).update(source="slf")
        rating = RegionDayRatingFactory.create()
        RegionDayRating.objects.filter(pk=rating.pk).update(source="albina")
        bulletin = _seed_legacy_render_model_source(value="meteofrance")

        call_command("uppercase_bulletin_choice_values", "--commit")

        run.refresh_from_db()
        assert run.status == PipelineRun.Status.PENDING
        source_bulletin.refresh_from_db()
        assert source_bulletin.source == Bulletin.Source.SLF
        rating.refresh_from_db()
        assert rating.source == Bulletin.Source.ALBINA
        bulletin.refresh_from_db()
        assert bulletin.render_model["source"] == Bulletin.Source.METEOFRANCE

    def test_json_pass_only_rewrites_the_source_key(self) -> None:
        """The rest of the render model survives the JSON rewrite untouched."""
        bulletin = _seed_legacy_render_model_source(value="slf")

        call_command("uppercase_bulletin_choice_values", "--commit")

        bulletin.refresh_from_db()
        assert bulletin.render_model["version"] == 6
        assert bulletin.render_model["danger"] == {"key": "low"}
        assert bulletin.render_model["source"] == Bulletin.Source.SLF

    def test_second_run_selects_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running after a successful commit finds no eligible rows anywhere."""
        _seed_legacy_run(value="success")
        _seed_legacy_bulletin_source(value="slf")
        _seed_legacy_day_rating_source(value="albina")
        _seed_legacy_render_model_source(value="meteofrance")
        call_command("uppercase_bulletin_choice_values", "--commit")

        capsys.readouterr()
        call_command("uppercase_bulletin_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out

    def test_no_eligible_rows_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With everything already upper case, the command reports and returns."""
        PipelineRunFactory.create(status=PipelineRun.Status.SUCCESS)
        BulletinFactory.create(source=Bulletin.Source.SLF)
        RegionDayRatingFactory.create(source=Bulletin.Source.ALBINA)

        call_command("uppercase_bulletin_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out
