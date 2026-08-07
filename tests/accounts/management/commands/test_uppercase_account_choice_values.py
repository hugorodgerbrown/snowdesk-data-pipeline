"""
tests/accounts/management/commands/test_uppercase_account_choice_values.py

Covers the ``uppercase_account_choice_values`` management command (SNOW-582):
  - Read-only by default (no writes to either field without --commit).
  - --commit uppercases legacy lower-case values on both
    Subscription.geo_match_kind and PushSubscription.mechanism.
  - Idempotence: a second --commit run selects nothing across both fields.
  - Nothing-to-do path (no eligible rows) exits cleanly.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import PushSubscription, Subscription
from tests.factories import PushSubscriptionFactory, SubscriptionFactory


def _seed_legacy_subscription(*, value: str = "in_region") -> None:
    """Create a Subscription and force geo_match_kind to a legacy value."""
    sub = SubscriptionFactory.create()
    Subscription.objects.filter(pk=sub.pk).update(geo_match_kind=value)


def _seed_legacy_push(*, value: str = "sw") -> None:
    """Create a PushSubscription and force mechanism to a legacy value."""
    sub = PushSubscriptionFactory.create()
    PushSubscription.objects.filter(pk=sub.pk).update(mechanism=value)


@pytest.mark.django_db
class TestUppercaseAccountChoiceValuesCommand:
    """Tests for the uppercase_account_choice_values management command."""

    def test_dry_run_writes_nothing(self) -> None:
        """Without --commit, neither field is persisted."""
        _seed_legacy_subscription(value="in_region")
        _seed_legacy_push(value="sw")

        call_command("uppercase_account_choice_values")

        assert Subscription.objects.filter(geo_match_kind="in_region").count() == 1
        assert PushSubscription.objects.filter(mechanism="sw").count() == 1
        assert (
            Subscription.objects.filter(
                geo_match_kind=Subscription.GeoMatchKind.IN_REGION
            ).count()
            == 0
        )
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
        _seed_legacy_subscription(value="in_region")
        _seed_legacy_push(value="declarative")

        call_command("uppercase_account_choice_values")

        out = capsys.readouterr().out
        assert "Subscription.geo_match_kind:" in out
        assert "IN_REGION: 1" in out
        assert "PushSubscription.mechanism:" in out
        assert "DECLARATIVE: 1" in out

    def test_commit_uppercases_both_fields(self) -> None:
        """--commit rewrites both fields' legacy values."""
        _seed_legacy_subscription(value="elsewhere")
        _seed_legacy_push(value="declarative")

        call_command("uppercase_account_choice_values", "--commit")

        assert Subscription.objects.filter(geo_match_kind="elsewhere").count() == 0
        assert PushSubscription.objects.filter(mechanism="declarative").count() == 0
        assert (
            Subscription.objects.filter(
                geo_match_kind=Subscription.GeoMatchKind.ELSEWHERE
            ).count()
            == 1
        )
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
        _seed_legacy_subscription(value="unknown")
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
        SubscriptionFactory.create(geo_match_kind=Subscription.GeoMatchKind.IN_REGION)
        PushSubscriptionFactory.create(mechanism=PushSubscription.Mechanism.SW)

        call_command("uppercase_account_choice_values", "--commit")

        out = capsys.readouterr().out
        assert "Nothing to do." in out
