"""
tests/regions/management/commands/test_uppercase_resort_choice_values.py

Covers the ``uppercase_resort_choice_values`` management command (SNOW-582):
  - Read-only by default (no Resort.geocode_source values written without
    --commit).
  - --commit uppercases every legacy lower-case value, per target value.
  - Idempotence: a second --commit run selects nothing.
  - Nothing-to-do path (no eligible rows) exits cleanly.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.regions.models import Resort
from tests.factories import ResortFactory


def _seed_legacy_lowercase(count: int = 1, *, value: str = "manual") -> None:
    """Create ``count`` resorts and force their geocode_source to a legacy value."""
    pks = [ResortFactory.create().pk for _ in range(count)]
    Resort.objects.filter(pk__in=pks).update(geocode_source=value)


@pytest.mark.django_db
class TestUppercaseResortChoiceValuesCommand:
    """Tests for the uppercase_resort_choice_values management command."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, no Resort.geocode_source values are persisted."""
        _seed_legacy_lowercase(2, value="manual")

        call_command("uppercase_resort_choice_values")

        assert Resort.objects.filter(geocode_source="manual").count() == 2
        assert (
            Resort.objects.filter(geocode_source=Resort.GeocodeSource.MANUAL).count()
            == 0
        )

    def test_dry_run_reports_the_breakdown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dry run names what it would convert, per target value."""
        _seed_legacy_lowercase(2, value="manual")
        _seed_legacy_lowercase(1, value="import")

        call_command("uppercase_resort_choice_values")

        out = capsys.readouterr().out
        assert "would uppercase 3 resort(s)" in out
        assert "MANUAL: 2" in out
        assert "IMPORT: 1" in out

    def test_commit_uppercases_every_legacy_row(self) -> None:
        """--commit rewrites every legacy value to its upper-case form."""
        _seed_legacy_lowercase(2, value="manual")
        _seed_legacy_lowercase(1, value="import")

        call_command("uppercase_resort_choice_values", "--commit")

        assert Resort.objects.filter(geocode_source="manual").count() == 0
        assert Resort.objects.filter(geocode_source="import").count() == 0
        assert (
            Resort.objects.filter(geocode_source=Resort.GeocodeSource.MANUAL).count()
            == 2
        )
        assert (
            Resort.objects.filter(geocode_source=Resort.GeocodeSource.IMPORT).count()
            == 1
        )

    def test_second_run_selects_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running after a successful commit finds no eligible rows."""
        _seed_legacy_lowercase(2, value="manual")
        call_command("uppercase_resort_choice_values", "--commit")

        capsys.readouterr()
        call_command("uppercase_resort_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out

    def test_no_eligible_resorts_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With no legacy values present, the command reports and returns."""
        ResortFactory.create(geocode_source=Resort.GeocodeSource.MANUAL)

        call_command("uppercase_resort_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out

    def test_stdout_carries_processed_ids_in_descending_order(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Each processed resort's pk is printed, newest id first."""
        _seed_legacy_lowercase(3, value="manual")
        pks = sorted(
            Resort.objects.filter(geocode_source="manual").values_list("pk", flat=True),
            reverse=True,
        )

        call_command("uppercase_resort_choice_values", "--commit", verbosity=1)

        out_lines = capsys.readouterr().out.splitlines()
        printed_pks = [int(line) for line in out_lines if line.strip().isdigit()]
        assert printed_pks == pks
