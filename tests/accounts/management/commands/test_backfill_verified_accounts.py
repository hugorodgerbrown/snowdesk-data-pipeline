"""
tests/accounts/management/commands/test_backfill_verified_accounts.py

Tests for the ``backfill_verified_accounts`` command (SNOW-430):
  - dry-run (default) writes nothing;
  - --commit creates a verified Account for a confirmed subscriber with none;
  - --commit flips an unverified Account to verified;
  - already-verified accounts are skipped;
  - the run is idempotent (a second commit changes nothing);
  - verified_at is taken from the subscriber's confirmed_at when present;
  - pending (unconfirmed) subscribers are not backfilled.
"""

from __future__ import annotations

import datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import Account, Subscriber
from tests.factories import AccountFactory, SubscriberFactory


def _run(commit: bool = False) -> str:
    """Invoke the command and return captured stdout."""
    out = StringIO()
    args = ["backfill_verified_accounts"]
    if commit:
        args.append("--commit")
    call_command(*args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestBackfillVerifiedAccounts:
    """Behaviour of the backfill command."""

    def test_dry_run_writes_nothing(self) -> None:
        sub = SubscriberFactory.create()  # active, no Account
        output = _run(commit=False)
        assert "would verify 1" in output
        assert not Account.objects.filter(user=sub.user).exists()

    def test_commit_creates_verified_account(self) -> None:
        sub = SubscriberFactory.create()
        _run(commit=True)
        account = Account.objects.get(user=sub.user)
        assert account.is_verified is True
        assert account.verified_at is not None

    def test_commit_flips_unverified_account(self) -> None:
        sub = SubscriberFactory.create()
        AccountFactory.create(user=sub.user, is_verified=False)
        _run(commit=True)
        account = Account.objects.get(user=sub.user)
        assert account.is_verified is True

    def test_already_verified_is_skipped(self) -> None:
        sub = SubscriberFactory.create()
        original = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        AccountFactory.create(user=sub.user, is_verified=True, verified_at=original)
        _run(commit=True)
        account = Account.objects.get(user=sub.user)
        assert account.verified_at == original  # untouched

    def test_idempotent_second_run(self) -> None:
        SubscriberFactory.create()
        _run(commit=True)
        output = _run(commit=True)
        assert "created=0" in output
        assert "updated=0" in output

    def test_verified_at_uses_confirmed_at(self) -> None:
        confirmed = datetime.datetime(2026, 2, 2, tzinfo=datetime.UTC)
        sub = SubscriberFactory.create(confirmed_at=confirmed)
        _run(commit=True)
        account = Account.objects.get(user=sub.user)
        assert account.verified_at == confirmed

    def test_pending_subscriber_not_backfilled(self) -> None:
        sub = SubscriberFactory.create(status=Subscriber.Status.PENDING)
        _run(commit=True)
        assert not Account.objects.filter(user=sub.user).exists()

    def test_per_row_error_raises_command_error(self) -> None:
        """A per-row failure is isolated but forces a non-zero exit (CommandError)."""
        SubscriberFactory.create()
        with patch(
            "accounts.management.commands.backfill_verified_accounts."
            "Account.objects.create",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(CommandError):
                _run(commit=True)
