"""
tests/core/test_uppercase_choices.py — Tests for apps.core.uppercase_choices.

Covers ``uppercase_field_values`` (SNOW-582): read-only by default, converts
matching rows on commit, ignores rows already upper-case or blank, groups
the returned breakdown by new value, and is idempotent — a second run after
a commit selects nothing.

Exercised against ``Resort.geocode_source`` — a real model already carrying
upper-case ``GeocodeSource`` choices — with legacy lower-case values written
directly via ``.update()`` (bypassing ``full_clean``) to simulate rows
persisted before the SNOW-582 migration.
"""

from __future__ import annotations

from collections import Counter
from io import StringIO

import pytest
from django.core.management.base import BaseCommand

from apps.core.uppercase_choices import uppercase_field_values
from apps.regions.models import Resort
from tests.factories import ResortFactory


def _seed_legacy_lowercase(count: int = 1, *, value: str = "manual") -> list[int]:
    """Create ``count`` resorts and force their geocode_source to a legacy value.

    ``.update()`` bypasses model validation, mirroring rows written before
    ``GeocodeSource`` existed as a proper ``TextChoices`` class.
    """
    pks = [ResortFactory.create().pk for _ in range(count)]
    Resort.objects.filter(pk__in=pks).update(geocode_source=value)
    return pks


@pytest.mark.django_db
class TestUppercaseFieldValues:
    """Tests for uppercase_field_values."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without commit=True, no row is rewritten."""
        _seed_legacy_lowercase(2, value="manual")

        converted = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=False, verbosity=0
        )

        assert converted == Counter({Resort.GeocodeSource.MANUAL: 2})
        assert Resort.objects.filter(geocode_source="manual").count() == 2
        assert (
            Resort.objects.filter(geocode_source=Resort.GeocodeSource.MANUAL).count()
            == 0
        )

    def test_commit_converts_matching_rows(self) -> None:
        """commit=True rewrites every matching row to its upper-case value."""
        _seed_legacy_lowercase(3, value="import")

        converted = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        assert converted == Counter({Resort.GeocodeSource.IMPORT: 3})
        assert Resort.objects.filter(geocode_source="import").count() == 0
        assert (
            Resort.objects.filter(geocode_source=Resort.GeocodeSource.IMPORT).count()
            == 3
        )

    def test_groups_breakdown_by_new_value(self) -> None:
        """The returned Counter groups converted rows per target value."""
        _seed_legacy_lowercase(2, value="manual")
        _seed_legacy_lowercase(1, value="import")

        converted = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        assert converted == Counter(
            {
                Resort.GeocodeSource.MANUAL: 2,
                Resort.GeocodeSource.IMPORT: 1,
            }
        )

    def test_ignores_values_already_upper_case(self) -> None:
        """A row already storing the upper-case value is not selected."""
        ResortFactory.create(geocode_source=Resort.GeocodeSource.MANUAL)

        converted = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        assert converted == Counter()

    def test_ignores_blank_values(self) -> None:
        """A blank geocode_source (the default) is never selected."""
        ResortFactory.create(geocode_source="")

        converted = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        assert converted == Counter()

    def test_second_run_selects_nothing(self) -> None:
        """Re-running after a successful commit converts nothing further."""
        _seed_legacy_lowercase(2, value="manual")
        uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        second = uppercase_field_values(
            BaseCommand(), Resort, "geocode_source", commit=True, verbosity=0
        )

        assert second == Counter()

    def test_stdout_carries_processed_ids_in_descending_order(self) -> None:
        """Countdown output (via iterate_rows) prints newest id first."""
        pks = sorted(_seed_legacy_lowercase(3, value="manual"), reverse=True)
        buf = StringIO()
        cmd = BaseCommand(stdout=buf)

        uppercase_field_values(cmd, Resort, "geocode_source", commit=True, verbosity=1)

        out_lines = buf.getvalue().splitlines()
        printed_pks = [int(line) for line in out_lines if line.strip().isdigit()]
        assert printed_pks == pks
