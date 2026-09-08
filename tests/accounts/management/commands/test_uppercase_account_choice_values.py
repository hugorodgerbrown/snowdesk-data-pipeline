"""
tests/accounts/management/commands/test_uppercase_account_choice_values.py

Covers the ``uppercase_account_choice_values`` management command (SNOW-582).
It handled two fields until SNOW-805 dropped ``Subscription``; the surviving
field is ``PushSubscription.mechanism``:
  - Read-only by default (nothing is written without --commit).
  - --commit uppercases legacy lower-case values.
  - Idempotence: a second --commit run selects nothing.
  - Nothing-to-do path (no eligible rows) exits cleanly.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import PushSubscription
from tests.factories import PushSubscriptionFactory


def _seed_legacy_push(*, value: str = "sw") -> None:
    """Create a PushSubscription and force mechanism to a legacy value."""
    sub = PushSubscriptionFactory.create()
    PushSubscription.objects.filter(pk=sub.pk).update(mechanism=value)


@pytest.mark.django_db
class TestUppercaseAccountChoiceValuesCommand:
    """Tests for the uppercase_account_choice_values management command."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, the legacy value is left as it was found."""
        _seed_legacy_push(value="sw")

        call_command("uppercase_account_choice_values")

        assert PushSubscription.objects.filter(mechanism="sw").count() == 1
        assert (
            PushSubscription.objects.filter(
                mechanism=PushSubscription.Mechanism.SW
            ).count()
            == 0
        )

    def test_dry_run_reports_the_breakdown_per_field(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The dry run names what it would convert, per field and value."""
        _seed_legacy_push(value="declarative")

        call_command("uppercase_account_choice_values")

        out = capsys.readouterr().out
        assert "PushSubscription.mechanism:" in out
        assert "DECLARATIVE: 1" in out

    def test_commit_uppercases_the_field(self) -> None:
        """--commit rewrites the field's legacy values."""
        _seed_legacy_push(value="declarative")

        call_command("uppercase_account_choice_values", "--commit")

        assert PushSubscription.objects.filter(mechanism="declarative").count() == 0
        assert (
            PushSubscription.objects.filter(
                mechanism=PushSubscription.Mechanism.DECLARATIVE
            ).count()
            == 1
        )

    def test_second_run_selects_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Re-running after a successful commit finds no eligible rows."""
        _seed_legacy_push(value="sw")
        call_command("uppercase_account_choice_values", "--commit")

        capsys.readouterr()
        call_command("uppercase_account_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out

    def test_no_eligible_rows_exits_cleanly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With every value already upper case, the command reports and returns."""
        PushSubscriptionFactory.create(mechanism=PushSubscription.Mechanism.SW)

        call_command("uppercase_account_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out
