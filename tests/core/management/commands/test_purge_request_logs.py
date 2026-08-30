"""
tests/core/management/commands/test_purge_request_logs.py — retention (SNOW-775).

The Privacy Policy states a retention period for request data. Before
SNOW-775 nothing enforced one: ``RequestLog`` rows accumulated forever while
the page claimed fourteen days. This command is what makes the stated period
true, so the tests here are as much about the promise as about the code.

Pinned:

* Rows older than the window go; rows inside it stay. The boundary is
  checked from both sides, because an off-by-one in the comparison is the
  one bug that would silently delete a day of live data or keep a day of
  expired data.
* The command writes nothing without ``--commit`` — the project-wide
  management-command contract.
* A row still referenced by ``Account.acquisition_request`` or
  ``Subscription.subscribed_via`` is deleted like any other, and the
  referring row survives with the pointer nulled. That is the intended
  outcome and worth pinning: it is what lets an account outlive the
  identifiers captured when it was created.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import Account, Subscription
from apps.bulletins.models import BulletinShareClick
from apps.core.management.commands.purge_request_logs import RETENTION_DAYS
from apps.core.models import RequestLog
from tests.factories import (
    AccountFactory,
    BulletinShareFactory,
    MicroRegionFactory,
    RequestLogFactory,
    SubscriptionFactory,
)


def _aged(days: int) -> RequestLog:
    """Create a RequestLog whose ``created_at`` is *days* in the past.

    ``created_at`` is ``auto_now_add``, so it cannot be passed to the
    factory — the row is created first and the timestamp forced afterwards
    with a queryset update, which bypasses the auto field.

    Args:
        days: How many days old the row should be.

    Returns:
        The row, refreshed so ``created_at`` reflects the update.

    """
    log = RequestLogFactory.create()
    RequestLog.objects.filter(pk=log.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    log.refresh_from_db()
    return log


def _run(*args: str) -> str:
    """Run the command, returning its stdout."""
    out = StringIO()
    call_command("purge_request_logs", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestRetentionWindow:
    """What goes and what stays."""

    def test_an_expired_row_is_deleted(self) -> None:
        old = _aged(RETENTION_DAYS + 30)

        _run("--commit")

        assert not RequestLog.objects.filter(pk=old.pk).exists()

    def test_a_fresh_row_is_kept(self) -> None:
        fresh = _aged(1)

        _run("--commit")

        assert RequestLog.objects.filter(pk=fresh.pk).exists()

    def test_the_boundary_is_checked_from_both_sides(self) -> None:
        """One day either side of the cutoff, in a single run.

        Asserted together rather than as two tests so the pair cannot drift
        apart: the failure being guarded against is a comparison that moves
        the boundary, which shows up only when both sides are present.
        """
        just_inside = _aged(RETENTION_DAYS - 1)
        just_outside = _aged(RETENTION_DAYS + 1)

        _run("--commit")

        assert RequestLog.objects.filter(pk=just_inside.pk).exists()
        assert not RequestLog.objects.filter(pk=just_outside.pk).exists()

    def test_the_window_is_overridable(self) -> None:
        """``--days`` lets an operator see what a stricter policy would take."""
        row = _aged(100)

        _run("--days", "90", "--commit")

        assert not RequestLog.objects.filter(pk=row.pk).exists()

    def test_a_non_positive_window_is_refused(self) -> None:
        """A cutoff in the future would delete rows inside their window."""
        with pytest.raises(CommandError):
            _run("--days", "0", "--commit")


@pytest.mark.django_db
class TestReadOnlyByDefault:
    """The management-command contract: no writes without --commit."""

    def test_nothing_is_deleted_without_commit(self) -> None:
        old = _aged(RETENTION_DAYS + 30)

        output = _run()

        assert RequestLog.objects.filter(pk=old.pk).exists()
        assert "Would delete 1" in output

    def test_a_clean_table_says_so(self) -> None:
        _aged(1)

        output = _run()

        assert "No RequestLog rows older than" in output


@pytest.mark.django_db
class TestReferencedRows:
    """A referenced row expires; the row referring to it does not."""

    def test_an_expired_acquisition_row_is_deleted_and_the_account_survives(
        self,
    ) -> None:
        """The account keeps its history; the identifiers behind it expire.

        ``acquisition_request`` is SET_NULL, so this is the intended
        outcome rather than a cascade accident — pinned because a future
        change to PROTECT would turn the nightly purge into a crash.
        """
        signup = _aged(RETENTION_DAYS + 30)
        account = AccountFactory.create(acquisition_request=signup)

        _run("--commit")
        account.refresh_from_db()

        assert not RequestLog.objects.filter(pk=signup.pk).exists()
        assert Account.objects.filter(pk=account.pk).exists()
        assert account.acquisition_request_id is None

    def test_an_expired_subscribed_via_row_is_deleted_and_the_sub_survives(
        self,
    ) -> None:
        via = _aged(RETENTION_DAYS + 30)
        subscription = SubscriptionFactory.create(
            account=AccountFactory.create(),
            region=MicroRegionFactory.create(),
            subscribed_via=via,
        )

        _run("--commit")
        subscription.refresh_from_db()

        assert not RequestLog.objects.filter(pk=via.pk).exists()
        assert Subscription.objects.filter(pk=subscription.pk).exists()
        assert subscription.subscribed_via_id is None


@pytest.mark.django_db
class TestShareClicks:
    """A share click must not be able to stop the nightly purge.

    ``BulletinShareClick.request`` was ``PROTECT`` until SNOW-774. A purge
    that met one would raise ``ProtectedError`` and abort — not for the one
    row, but for the whole run, so a single aged click would have frozen
    retention for every other row in the table and done it silently, at
    03:30, in a scheduled job nobody was watching.
    """

    def test_an_expired_click_does_not_block_the_purge(self) -> None:
        """The regression test: this raised ProtectedError."""
        log = _aged(RETENTION_DAYS + 30)
        click = BulletinShareClick.objects.create(
            share=BulletinShareFactory.create(),
            request=log,
            visitor_hash="0123456789abcdef",
        )

        _run("--commit")

        assert not RequestLog.objects.filter(pk=log.pk).exists()
        assert not BulletinShareClick.objects.filter(pk=click.pk).exists()

    def test_one_expired_click_does_not_strand_the_other_rows(self) -> None:
        """The blast radius of the old bug, pinned.

        ``ProtectedError`` aborted the whole delete, so an unrelated expired
        row would have survived because someone else's click happened to be
        in the same sweep.
        """
        blocked = _aged(RETENTION_DAYS + 30)
        BulletinShareClick.objects.create(
            share=BulletinShareFactory.create(),
            request=blocked,
            visitor_hash="0123456789abcdef",
        )
        unrelated = _aged(RETENTION_DAYS + 30)

        _run("--commit")

        assert not RequestLog.objects.filter(pk=unrelated.pk).exists()

    def test_a_fresh_click_is_kept(self) -> None:
        """Cascade follows the retention window, it does not widen it."""
        log = _aged(1)
        click = BulletinShareClick.objects.create(
            share=BulletinShareFactory.create(),
            request=log,
            visitor_hash="0123456789abcdef",
        )

        _run("--commit")

        assert BulletinShareClick.objects.filter(pk=click.pk).exists()
