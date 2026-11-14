"""
tests/accounts/services/test_push_health.py — Delivery receipts and retirement.

Covers ``apps.accounts.services.push_health`` (SNOW-1494):
  - receipt tokens: a good one confirms; a bad signature, a malformed body
    and a token past its TTL plus the grace are refused, at both the 60 s
    default TTL and a 12 h alert TTL;
  - confirm() clears the count and never un-retires a row;
  - count_send / uncount_send / retire_if_dead and their boundaries;
  - retire_replaced, only on the same account;
  - the retirement email: sent once, at least once (a failed send leaves
    the row owed, a later sweep sends it, a replay after success does not).
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest
from django.core import mail, signing
from django.utils import timezone
from freezegun import freeze_time

from apps.accounts.models import PushSubscription
from apps.accounts.services import push_health
from apps.accounts.services.push_health import (
    RECEIPT_GRACE_SECONDS,
    RECEIPT_SALT,
    RETIRE_AFTER,
    RETIRE_AFTER_PUSHES,
    _worker_send_device_retired_email,
    confirm,
    count_send,
    enqueue_outstanding_retired_notices,
    receipt_token,
    retire_if_dead,
    retire_replaced,
    uncount_send,
)
from tests.factories import AccountFactory, PushSubscriptionFactory

NOW = datetime.datetime(2026, 11, 14, 9, 0, tzinfo=datetime.UTC)
TWELVE_HOURS = 12 * 3600

UNCONFIRMED = PushSubscription.RetiredReason.UNCONFIRMED


def _token_at(sub: PushSubscription, ttl: int, when: datetime.datetime) -> str:
    """Return a receipt token for ``sub`` signed at ``when``."""
    with freeze_time(when):
        return receipt_token(sub, ttl)


@pytest.mark.django_db
class TestConfirm:
    """confirm() takes a good token and refuses everything else."""

    @pytest.mark.parametrize("ttl", [60, TWELVE_HOURS])
    def test_a_receipt_within_ttl_and_grace_confirms(self, ttl: int) -> None:
        """A token at exactly its TTL plus the grace still confirms."""
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=2, unconfirmed_since=NOW
        )
        token = _token_at(sub, ttl, NOW)
        late = NOW + datetime.timedelta(seconds=ttl + RECEIPT_GRACE_SECONDS)
        assert confirm(token, late) is True
        sub.refresh_from_db()
        assert sub.confirmed_at == late
        assert sub.unconfirmed_pushes == 0
        assert sub.unconfirmed_since is None

    @pytest.mark.parametrize("ttl", [60, TWELVE_HOURS])
    def test_a_receipt_past_ttl_and_grace_is_refused(self, ttl: int) -> None:
        """One second past the TTL plus the grace, the token is refused."""
        sub = PushSubscriptionFactory.create(unconfirmed_pushes=1)
        token = _token_at(sub, ttl, NOW)
        late = NOW + datetime.timedelta(seconds=ttl + RECEIPT_GRACE_SECONDS + 1)
        assert confirm(token, late) is False
        sub.refresh_from_db()
        assert sub.confirmed_at is None
        assert sub.unconfirmed_pushes == 1

    def test_a_tampered_token_is_refused(self) -> None:
        """A token whose signature does not match is refused."""
        sub = PushSubscriptionFactory.create()
        token = _token_at(sub, 60, NOW)
        assert confirm(token[:-2] + "xx", NOW) is False

    def test_a_token_for_another_salt_is_refused(self) -> None:
        """A token signed with another salt does not pass as a receipt."""
        sub = PushSubscriptionFactory.create()
        token = signing.TimestampSigner(salt="other").sign_object(
            {"s": str(sub.uuid), "ttl": 60}
        )
        assert confirm(token, timezone.now()) is False

    def test_a_signed_body_without_the_fields_is_refused(self) -> None:
        """A correctly signed body that is not a receipt is refused."""
        token = signing.TimestampSigner(salt=RECEIPT_SALT).sign_object({"x": 1})
        assert confirm(token, timezone.now()) is False

    def test_garbage_is_refused(self) -> None:
        """A string that is not a token at all is refused."""
        assert confirm("not-a-token", NOW) is False

    def test_a_receipt_never_unretires(self) -> None:
        """A late receipt from a retired device leaves it retired."""
        sub = PushSubscriptionFactory.create(
            inactive_at=NOW, retired_reason=UNCONFIRMED, unconfirmed_pushes=3
        )
        assert confirm(_token_at(sub, 60, NOW), NOW) is True
        sub.refresh_from_db()
        assert sub.inactive_at == NOW
        assert sub.retired_reason == UNCONFIRMED

    def test_a_receipt_for_a_deleted_row_is_still_good(self) -> None:
        """The token was good; the row just is not there to update."""
        sub = PushSubscriptionFactory.create()
        token = _token_at(sub, 60, NOW)
        sub.delete()
        assert confirm(token, NOW) is True


@pytest.mark.django_db
class TestCounting:
    """count_send, uncount_send and their trust."""

    def test_a_device_with_nothing_unconfirmed_is_trusted(self) -> None:
        """The first push, or one after a receipt, is trusted."""
        sub = PushSubscriptionFactory.create()
        assert count_send(sub.pk, NOW) is True
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 1
        assert sub.unconfirmed_since == NOW

    def test_a_device_owing_a_receipt_is_not_trusted(self) -> None:
        """A push after an unconfirmed one is not trusted; since is kept."""
        earlier = NOW - datetime.timedelta(hours=3)
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=1, unconfirmed_since=earlier
        )
        assert count_send(sub.pk, NOW) is False
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 2
        assert sub.unconfirmed_since == earlier

    def test_a_missing_row_counts_nothing(self) -> None:
        """A row deleted before the send gives None."""
        assert count_send(999_999, NOW) is None

    def test_uncount_takes_back_one_and_clears_since_at_zero(self) -> None:
        """Back at zero, unconfirmed_since is cleared too."""
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=1, unconfirmed_since=NOW
        )
        uncount_send(sub.pk)
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 0
        assert sub.unconfirmed_since is None

    def test_uncount_above_one_keeps_since(self) -> None:
        """With pushes still owed, since stays."""
        sub = PushSubscriptionFactory.create(
            unconfirmed_pushes=2, unconfirmed_since=NOW
        )
        uncount_send(sub.pk)
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 1
        assert sub.unconfirmed_since == NOW

    def test_uncount_never_goes_below_zero(self) -> None:
        """A receipt that cleared the count first leaves nothing to take back."""
        sub = PushSubscriptionFactory.create()
        uncount_send(sub.pk)
        sub.refresh_from_db()
        assert sub.unconfirmed_pushes == 0


@pytest.mark.django_db
class TestRetireIfDead:
    """retire_if_dead's boundaries, and the email it enqueues."""

    def _dead(self, **kwargs: Any) -> PushSubscription:
        """Return a row exactly at the retirement threshold."""
        values: dict[str, Any] = {
            "account": AccountFactory.create(),
            "unconfirmed_pushes": RETIRE_AFTER_PUSHES,
            "unconfirmed_since": NOW - RETIRE_AFTER,
        }
        values.update(kwargs)
        return PushSubscriptionFactory.create(**values)

    def test_retires_at_the_threshold_and_emails_on_commit(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """Three pushes over two days: retired, and the email goes on commit."""
        sub = self._dead()
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            assert retire_if_dead(sub.pk, NOW) is True
        assert len(callbacks) == 1
        sub.refresh_from_db()
        assert sub.inactive_at == NOW
        assert sub.retired_reason == UNCONFIRMED
        assert len(mail.outbox) == 1
        assert sub.retired_notice_sent_at is not None

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"unconfirmed_pushes": RETIRE_AFTER_PUSHES - 1},
            {"unconfirmed_since": NOW - RETIRE_AFTER + datetime.timedelta(seconds=1)},
            {"mechanism": PushSubscription.Mechanism.DECLARATIVE},
            {"inactive_at": NOW - datetime.timedelta(days=1)},
        ],
        ids=["too-few-pushes", "too-recent", "declarative", "already-retired"],
    )
    def test_does_not_retire_short_of_the_threshold(
        self, kwargs: dict[str, Any], django_capture_on_commit_callbacks: Any
    ) -> None:
        """Short of any condition, nothing is retired and nothing enqueued."""
        sub = self._dead(**kwargs)
        before = sub.inactive_at
        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            assert retire_if_dead(sub.pk, NOW) is False
        assert callbacks == []
        sub.refresh_from_db()
        assert sub.inactive_at == before
        assert mail.outbox == []


@pytest.mark.django_db
class TestRetireReplaced:
    """retire_replaced retires only the same account's old live row."""

    def test_retires_the_old_endpoint_as_replaced(self) -> None:
        """The old row is retired as REPLACED, and no email goes."""
        account = AccountFactory.create()
        old = PushSubscriptionFactory.create(account=account)
        assert retire_replaced(account, old.endpoint, "https://push.example/new") == 1
        old.refresh_from_db()
        assert old.inactive_at is not None
        assert old.retired_reason == PushSubscription.RetiredReason.REPLACED
        assert mail.outbox == []

    def test_another_accounts_row_is_left_alone(self) -> None:
        """One account cannot retire another's device."""
        theirs = PushSubscriptionFactory.create(account=AccountFactory.create())
        mine = AccountFactory.create()
        assert retire_replaced(mine, theirs.endpoint, "https://push.example/n") == 0
        theirs.refresh_from_db()
        assert theirs.inactive_at is None

    @pytest.mark.parametrize("old", ["", "same"])
    def test_no_old_or_the_same_endpoint_retires_nothing(self, old: str) -> None:
        """Nothing to replace when there is no old endpoint or it did not change."""
        account = AccountFactory.create()
        sub = PushSubscriptionFactory.create(account=account)
        old_endpoint = sub.endpoint if old == "same" else ""
        assert retire_replaced(account, old_endpoint, sub.endpoint) == 0
        sub.refresh_from_db()
        assert sub.inactive_at is None

    def test_no_account_retires_nothing(self) -> None:
        """An anonymous staff row has no account to match on."""
        sub = PushSubscriptionFactory.create()
        assert retire_replaced(None, sub.endpoint, "https://push.example/n") == 0


@pytest.mark.django_db
class TestRetiredEmail:
    """The retirement email goes at least once, and once it went, not again."""

    def _retired(self) -> PushSubscription:
        """Return a row retired as unconfirmed, still owed its email."""
        return PushSubscriptionFactory.create(
            account=AccountFactory.create(user__email="Skier@Example.com"),
            inactive_at=NOW,
            retired_reason=UNCONFIRMED,
        )

    def test_the_email_names_the_setup_date_and_the_settings_link(self) -> None:
        """Subject, setup date and the absolute settings link."""
        sub = self._retired()
        PushSubscription.objects.filter(pk=sub.pk).update(
            created_at=datetime.datetime(2026, 1, 5, 12, 0, tzinfo=datetime.UTC)
        )
        _worker_send_device_retired_email.enqueue(sub.pk)
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.subject == "Alerts stopped reaching one of your devices"
        assert message.to == ["skier@example.com"]
        assert "5 January 2026" in message.body
        assert "/account/settings/#settings-alerts" in message.body
        assert message.body.count("http") == 1
        html = message.alternatives[0][0]  # type: ignore[attr-defined]  # EmailMultiAlternatives
        assert "/account/settings/#settings-alerts" in str(html)
        sub.refresh_from_db()
        assert sub.retired_notice_sent_at is not None

    def test_a_replay_after_success_does_not_send_again(self) -> None:
        """The second run sees retired_notice_sent_at and stops."""
        sub = self._retired()
        _worker_send_device_retired_email.enqueue(sub.pk)
        _worker_send_device_retired_email.enqueue(sub.pk)
        assert len(mail.outbox) == 1

    def test_a_failed_send_leaves_it_owed_and_a_sweep_sends_it(self) -> None:
        """A send that raises leaves the field null; the next sweep sends once."""
        sub = self._retired()
        with (
            patch.object(push_health, "send_mail", side_effect=OSError("smtp down")),
            pytest.raises(OSError, match="smtp down"),
        ):
            _worker_send_device_retired_email.call(sub.pk)
        sub.refresh_from_db()
        assert sub.retired_notice_sent_at is None
        assert mail.outbox == []

        assert enqueue_outstanding_retired_notices() == 1
        assert len(mail.outbox) == 1
        assert enqueue_outstanding_retired_notices() == 0
        assert len(mail.outbox) == 1

    def test_a_reregistered_row_is_not_emailed(self) -> None:
        """A row live again (or never retired as unconfirmed) gets nothing."""
        sub = self._retired()
        PushSubscription.objects.filter(pk=sub.pk).update(
            inactive_at=None, retired_reason=""
        )
        _worker_send_device_retired_email.enqueue(sub.pk)
        assert mail.outbox == []

    def test_an_unverified_account_is_not_emailed(self) -> None:
        """An address never proven reachable is not written to."""
        sub = PushSubscriptionFactory.create(
            account=AccountFactory.create(is_verified=False),
            inactive_at=NOW,
            retired_reason=UNCONFIRMED,
        )
        _worker_send_device_retired_email.enqueue(sub.pk)
        assert mail.outbox == []

    def test_a_missing_row_sends_nothing(self) -> None:
        """A row deleted before the task ran is not an error."""
        _worker_send_device_retired_email.enqueue(999_999)
        assert mail.outbox == []
