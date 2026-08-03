"""
tests/bulletins/management/commands/test_backfill_bulletin_source.py

Covers:
  - Read-only by default (no Bulletin.source values written without --commit).
  - --commit backfills every bulletin missing a source, per provider.
  - The provider is read from raw_data.customData, not render_model["source"] —
    a row whose render model carries a stale/legacy value is still detected
    correctly from its raw payload.
  - Idempotence: a second --commit run selects only the undetectable rows.
  - An undetectable payload raises CommandError (non-zero exit) while the
    detectable bulletins around it are still written.
  - Nothing-to-do path (no eligible bulletins) exits cleanly.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.models import Bulletin
from tests.factories import BulletinFactory, PipelineRunFactory

# customData markers detect_source discriminates on, per provider.
_CUSTOM_DATA: dict[str, dict[str, Any]] = {
    Bulletin.Source.SLF: {"CH": {}},
    Bulletin.Source.ALBINA: {"ALBINA": {}},
    Bulletin.Source.METEOFRANCE: {"MF": {}},
}


def _make_bulletin(
    index: int,
    *,
    custom_data: dict[str, Any],
    render_model: dict[str, Any] | None = None,
) -> Bulletin:
    """Seed one Bulletin with a blank source and the given customData."""
    return BulletinFactory.create(
        bulletin_id=f"source-backfill-{index:04d}",
        pipeline_run=PipelineRunFactory.create(),
        source="",
        raw_data={
            "type": "Feature",
            "geometry": None,
            "properties": {"customData": custom_data},
        },
        render_model=render_model if render_model is not None else {"version": 0},
    )


def _seed_one_per_provider() -> None:
    """Seed one source-less bulletin for each of the three providers."""
    for index, custom_data in enumerate(_CUSTOM_DATA.values()):
        _make_bulletin(index, custom_data=custom_data)


@pytest.mark.django_db
class TestBackfillBulletinSourceCommand:
    """Tests for the backfill_bulletin_source management command."""

    # ------------------------------------------------------------------
    # Read-only (default)
    # ------------------------------------------------------------------

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no Bulletin.source values are persisted."""
        _seed_one_per_provider()

        call_command("backfill_bulletin_source")

        assert Bulletin.objects.filter(source="").count() == 3

    def test_dry_run_reports_the_per_provider_breakdown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dry run names what it would write, per provider."""
        _seed_one_per_provider()

        call_command("backfill_bulletin_source")

        out = capsys.readouterr().out
        assert "would populate 3 source(s)" in out
        for source in _CUSTOM_DATA:
            assert f"{source}: 1" in out

    # ------------------------------------------------------------------
    # --commit
    # ------------------------------------------------------------------

    def test_commit_populates_every_provider(self) -> None:
        """--commit detects and persists each provider from its customData."""
        _seed_one_per_provider()

        call_command("backfill_bulletin_source", "--commit")

        assert Bulletin.objects.filter(source="").count() == 0
        assert set(Bulletin.objects.values_list("source", flat=True)) == set(
            _CUSTOM_DATA
        )

    def test_detection_ignores_a_stale_render_model_source(self) -> None:
        """The raw payload wins over a legacy render_model["source"] value.

        Older pipeline versions wrote "euregio" into the render model. The
        backfill must not propagate that into the new column.
        """
        _make_bulletin(
            0,
            custom_data=_CUSTOM_DATA[Bulletin.Source.ALBINA],
            render_model={"version": 4, "source": "euregio"},
        )

        call_command("backfill_bulletin_source", "--commit")

        bulletin = Bulletin.objects.get(bulletin_id="source-backfill-0000")
        assert bulletin.source == Bulletin.Source.ALBINA

    def test_detection_survives_a_missing_render_model(self) -> None:
        """A row whose render model never built still gets a source."""
        _make_bulletin(
            0,
            custom_data=_CUSTOM_DATA[Bulletin.Source.METEOFRANCE],
            render_model={},
        )

        call_command("backfill_bulletin_source", "--commit")

        bulletin = Bulletin.objects.get(bulletin_id="source-backfill-0000")
        assert bulletin.source == Bulletin.Source.METEOFRANCE

    # ------------------------------------------------------------------
    # Idempotence
    # ------------------------------------------------------------------

    def test_second_run_selects_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running after a successful commit finds no eligible rows."""
        _seed_one_per_provider()
        call_command("backfill_bulletin_source", "--commit")

        capsys.readouterr()
        call_command("backfill_bulletin_source", "--commit")

        out = capsys.readouterr().out
        assert "Bulletins missing a source: 0" in out
        assert "Nothing to do." in out

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    def test_undetectable_payload_raises_but_writes_the_rest(self) -> None:
        """An unknown payload fails the run without blocking its neighbours."""
        _make_bulletin(0, custom_data=_CUSTOM_DATA[Bulletin.Source.SLF])
        _make_bulletin(1, custom_data={"UNKNOWN_PROVIDER": {}})

        with pytest.raises(CommandError, match="had no detectable source"):
            call_command("backfill_bulletin_source", "--commit")

        assert (
            Bulletin.objects.get(bulletin_id="source-backfill-0000").source
            == Bulletin.Source.SLF
        )
        assert Bulletin.objects.get(bulletin_id="source-backfill-0001").source == ""

    def test_empty_raw_data_is_reported_as_a_failure(self) -> None:
        """A row with no raw_data at all cannot be detected and is counted."""
        BulletinFactory.create(
            bulletin_id="source-backfill-empty",
            pipeline_run=PipelineRunFactory.create(),
            source="",
            raw_data={},
        )

        with pytest.raises(CommandError, match="had no detectable source"):
            call_command("backfill_bulletin_source", "--commit")

        assert Bulletin.objects.get(bulletin_id="source-backfill-empty").source == ""

    # ------------------------------------------------------------------
    # Nothing to do
    # ------------------------------------------------------------------

    def test_no_eligible_bulletins_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With every source already set, the command reports and returns."""
        _make_bulletin(0, custom_data=_CUSTOM_DATA[Bulletin.Source.SLF])
        Bulletin.objects.update(source=Bulletin.Source.SLF)

        call_command("backfill_bulletin_source", "--commit")

        out = capsys.readouterr().out
        assert "Bulletins missing a source: 0" in out
        assert "Nothing to do." in out

    # ------------------------------------------------------------------
    # Countdown (SNOW-602)
    # ------------------------------------------------------------------

    def test_stdout_carries_processed_ids_in_descending_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Each processed bulletin's pk is printed, newest id first."""
        _seed_one_per_provider()
        pks = sorted(Bulletin.objects.values_list("pk", flat=True), reverse=True)

        call_command("backfill_bulletin_source", "--commit")

        out_lines = capsys.readouterr().out.splitlines()
        printed_pks = [int(line) for line in out_lines if line.strip().isdigit()]
        assert printed_pks == pks
