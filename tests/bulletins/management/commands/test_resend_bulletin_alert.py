"""
tests/bulletins/management/commands/test_resend_bulletin_alert.py — The resend command.

SNOW-1493: the bare run does nothing; a dry run shows the last alert
and sends nothing; a commit resends the stored message by the account's own
channels, or by ``--channel`` overriding them; the send log is never
written; and the failures exit non-zero.

SNOW-1496: a resend is written afresh in today's cold wording from the
message's bulletins, whatever was stored; only the url is kept.

SNOW-1495: a resend goes with the urgency and expiry its rows stored (or
``normal`` to the bulletin's ``valid_to`` on a row from before them), and
is refused once the bulletin has ended.
"""

from __future__ import annotations

import datetime
from io import StringIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.accounts.models import Account
from apps.bulletins.models import BulletinAlert
from apps.core import remote_commands
from apps.core.models import CommandRun
from tests.factories import (
    AccountFactory,
    BulletinAlertFactory,
    BulletinFactory,
    PushSubscriptionFactory,
)

COMMAND = "apps.bulletins.management.commands.resend_bulletin_alert"
SENT_AT = datetime.datetime(2026, 11, 12, 16, 10, tzinfo=datetime.UTC)
TITLE = "Bulletins for your 4 places"
BODY = "Andermatt, Engelberg, Montana, Anzère and Verbier. Valid from Thursday."
URL = "/map/?d=2026-11-13"
ISSUED = datetime.datetime(2026, 11, 12, 16, 0, tzinfo=datetime.UTC)
NEW_TITLE = "New avalanche bulletin"
NEW_BODY = "Bulletin released at 17:00 by SLF."

pytestmark = pytest.mark.django_db


def make_message(
    account: Account,
    *,
    title: str = TITLE,
    at: datetime.datetime = SENT_AT,
    rows: int = 2,
    outcome: str = BulletinAlert.Outcome.SENT,
    issued: datetime.datetime = ISSUED,
    valid_to: datetime.datetime | None = None,
) -> list[BulletinAlert]:
    """Write one delivered message: ``rows`` send-log rows sharing a time.

    The bulletin is valid until ``valid_to``, by default six hours from now,
    so the message has not ended.
    """
    bulletin = BulletinFactory.create(
        source="SLF",
        issued_at=issued,
        valid_to=valid_to or timezone.now() + datetime.timedelta(hours=6),
    )
    made = [
        BulletinAlertFactory.create(
            account=account,
            title=title,
            body=BODY,
            url=URL,
            outcome=outcome,
            bulletin=bulletin,
        )
        for _ in range(rows)
    ]
    BulletinAlert.objects.filter(pk__in=[r.pk for r in made]).update(updated_at=at)
    return made


def make_account(
    alert_email: str = Account.AlertEmail.ALWAYS, devices: int = 1, **kwargs: Any
) -> Account:
    """Return an account with one delivered message and ``devices`` devices."""
    account = AccountFactory.create(alert_email=alert_email, **kwargs)
    for _ in range(devices):
        PushSubscriptionFactory.create(account=account)
    make_message(account)
    return account


def run(
    *args: str, ok: bool = True, trusted: bool | None = None, retired: bool = False
) -> tuple[str, MagicMock]:
    """Call the command with dispatch_push mocked; return stdout and the mock.

    ``trusted`` defaults to ``ok``: an accepted push to a confirmed device.
    """
    out = StringIO()
    result = {
        "ok": ok,
        "status": 201,
        "trusted": ok if trusted is None else trusted,
        "retired": retired,
    }
    with patch(f"{COMMAND}.dispatch_push", return_value=result) as push:
        call_command("resend_bulletin_alert", *args, stdout=out)
    return out.getvalue(), push


def counts() -> dict[str, int]:
    """Return the latest run's tally."""
    return dict(CommandRun.objects.latest("id").counts)


def test_bare_run_does_nothing() -> None:
    """With no arguments: no account, title or send is shown or made."""
    account = make_account()
    out, push = run()
    assert "--email" in out
    assert TITLE not in out
    assert str(account.pk) not in out
    assert counts() == {}
    push.assert_not_called()
    assert mail.outbox == []


def test_fallback_with_no_device_would_email() -> None:
    """A FALLBACK account with no device would be emailed."""
    account = make_account(Account.AlertEmail.FALLBACK, devices=0)
    run("--email", account.user.email)
    assert counts()["would_email"] == 1


def test_commit_needs_email() -> None:
    """--commit with no --email is refused."""
    with pytest.raises(CommandError, match="--email"):
        run("--commit")


def test_dry_run_shows_and_sends_nothing() -> None:
    """Without --commit: today's wording and the plan, no send, no log write."""
    account = make_account()
    before = list(BulletinAlert.objects.values_list("pk", "updated_at"))
    out, push = run("--email", account.user.email)
    assert "[READ-ONLY]" in out
    assert NEW_TITLE in out
    assert NEW_BODY in out
    assert TITLE not in out
    assert "push and email" in out
    push.assert_not_called()
    assert mail.outbox == []
    assert list(BulletinAlert.objects.values_list("pk", "updated_at")) == before
    assert counts()["would_push"] == 1
    assert counts()["would_email"] == 1


def test_commit_always_sends_both_in_todays_wording() -> None:
    """ALWAYS: every active device gets today's wording; one email goes."""
    account = make_account(devices=2)
    PushSubscriptionFactory.create(account=account, inactive_at=SENT_AT)
    before = list(BulletinAlert.objects.values_list("pk", "updated_at", "outcome"))
    _, push = run("--email", account.user.email, "--commit")
    assert push.call_count == 2
    for call in push.call_args_list:
        assert call.args[0].inactive_at is None
        assert call.args[1] == {"title": NEW_TITLE, "body": NEW_BODY, "url": URL}
        assert call.kwargs["delivery"].urgency == "normal"
    (message,) = mail.outbox
    assert isinstance(message, EmailMultiAlternatives)
    assert message.subject == NEW_TITLE
    assert NEW_BODY in message.body
    assert "Andermatt" not in message.body
    assert URL in message.body
    assert counts()["pushed"] == 2
    assert counts()["emailed"] == 1
    after = list(BulletinAlert.objects.values_list("pk", "updated_at", "outcome"))
    assert after == before


def test_never_account_gets_push_only() -> None:
    """NEVER, no --channel: push, no email."""
    account = make_account(Account.AlertEmail.NEVER)
    _, push = run("--email", account.user.email, "--commit")
    assert push.call_count == 1
    assert mail.outbox == []


def test_fallback_emails_only_when_no_push_accepted() -> None:
    """FALLBACK: an accepted push means no email; a refused one means email."""
    account = make_account(Account.AlertEmail.FALLBACK)
    run("--email", account.user.email, "--commit")
    assert mail.outbox == []
    run("--email", account.user.email, "--commit", ok=False)
    assert len(mail.outbox) == 1


def test_resend_pushes_are_counted() -> None:
    """A resend push is counted as unconfirmed, as an alert's is (SNOW-1494)."""
    account = make_account(Account.AlertEmail.NEVER)
    out, push = run("--email", account.user.email, "--commit")
    assert push.call_args.kwargs["count_unconfirmed"] is True
    assert "accepted (201)" in out


def test_fallback_emails_when_the_device_is_unconfirmed() -> None:
    """FALLBACK: accepted for a device owing a receipt still means email."""
    account = make_account(Account.AlertEmail.FALLBACK)
    out, _ = run("--email", account.user.email, "--commit", trusted=False)
    assert "accepted (unconfirmed device)" in out
    assert len(mail.outbox) == 1
    assert counts()["pushed"] == 1


def test_a_resend_that_retires_the_device_says_so() -> None:
    """The push that retires a dead device is reported as such."""
    account = make_account(Account.AlertEmail.NEVER)
    out, _ = run("--email", account.user.email, "--commit", trusted=False, retired=True)
    assert "accepted, device retired" in out


def test_channel_email_overrides_never() -> None:
    """--channel email emails a NEVER account and pushes nothing."""
    account = make_account(Account.AlertEmail.NEVER)
    _, push = run("--email", account.user.email, "--channel", "email", "--commit")
    push.assert_not_called()
    assert len(mail.outbox) == 1


def test_channel_push_skips_email_for_always() -> None:
    """--channel push sends no email even to an ALWAYS account."""
    account = make_account()
    _, push = run("--email", account.user.email, "--channel", "push", "--commit")
    assert push.call_count == 1
    assert mail.outbox == []


def test_channel_both() -> None:
    """--channel both uses both channels for a NEVER account."""
    account = make_account(Account.AlertEmail.NEVER)
    _, push = run("--email", account.user.email, "--channel", "both", "--commit")
    assert push.call_count == 1
    assert len(mail.outbox) == 1


def test_channel_email_unverified_fails() -> None:
    """--channel email to an unverified account sends nothing and fails."""
    account = make_account(is_verified=False)
    with pytest.raises(CommandError, match="reached no channel"):
        run("--email", account.user.email, "--channel", "email", "--commit")
    assert mail.outbox == []


def test_failed_push_with_no_email_fails() -> None:
    """Every requested channel failing exits non-zero and tallies it."""
    account = make_account(Account.AlertEmail.NEVER)
    with pytest.raises(CommandError):
        run("--email", account.user.email, "--commit", ok=False)
    assert counts()["push_failed"] == 1


def test_push_raising_counts_as_failed() -> None:
    """A device raising is a failed push, not a crash."""
    account = make_account(Account.AlertEmail.NEVER)
    with (
        patch(f"{COMMAND}.dispatch_push", side_effect=RuntimeError("down")),
        pytest.raises(CommandError, match="reached no channel"),
    ):
        call_command(
            "resend_bulletin_alert",
            "--email",
            account.user.email,
            "--commit",
            stdout=StringIO(),
        )


def test_newest_message_is_resent() -> None:
    """With two delivered messages, the newer is the one resent."""
    account = make_account(Account.AlertEmail.NEVER)
    make_message(
        account,
        title="Newer",
        at=SENT_AT + datetime.timedelta(hours=1),
        issued=ISSUED + datetime.timedelta(hours=1),
    )
    _, push = run("--email", account.user.email, "--commit")
    assert push.call_args.args[1]["body"] == "Bulletin released at 18:00 by SLF."


def test_email_is_case_insensitive() -> None:
    """Mixed-case input finds the account (invariant 2)."""
    account = make_account()
    out, _ = run("--email", account.user.email.upper())
    assert NEW_BODY in out


def test_unknown_email_fails() -> None:
    """An address with no account exits non-zero."""
    with pytest.raises(CommandError, match="No account"):
        run("--email", "nobody@example.com")


def test_no_delivered_alert_fails() -> None:
    """Only FAILED or BASELINE rows: nothing to resend."""
    account = AccountFactory.create()
    make_message(account, outcome=BulletinAlert.Outcome.FAILED)
    make_message(account, outcome=BulletinAlert.Outcome.BASELINE)
    with pytest.raises(CommandError, match="no delivered alert"):
        run("--email", account.user.email)


def test_is_offered_to_the_ops_tools() -> None:
    """The command can be run from the ops MCP server."""
    assert "resend_bulletin_alert" in remote_commands.runnable_commands()


def test_old_wording_never_resends() -> None:
    """The 12 Nov message, stored with place names, resends cold (SNOW-1496)."""
    account = make_account()
    _, push = run("--email", account.user.email, "--commit")
    sent = push.call_args.args[1]
    assert sent["title"] == NEW_TITLE
    assert "places" not in sent["title"]
    assert "Andermatt" not in sent["body"]


def test_rows_with_no_bulletin_still_say_a_bulletin_is_out() -> None:
    """A message whose bulletins are gone resends as "New bulletin."."""
    account = make_account()
    BulletinAlert.objects.filter(account=account).update(bulletin=None)
    _, push = run("--email", account.user.email, "--commit")
    assert push.call_args.args[1]["body"] == "New bulletin."


def test_resend_goes_with_the_stored_delivery() -> None:
    """The stored urgency, the TTL left to the stored expiry, the day's topic."""
    account = make_account()
    ends = timezone.now() + datetime.timedelta(hours=5)
    BulletinAlert.objects.filter(account=account).update(
        push_urgency="high",
        push_expires_at=ends,
        target_date=datetime.date(2026, 11, 13),
    )
    _, push = run("--email", account.user.email, "--commit")
    delivery = push.call_args.kwargs["delivery"]
    assert delivery.urgency == "high"
    assert delivery.expires_at == ends
    assert delivery.topic == "alert-20261113"
    assert 5 * 3600 - 60 <= delivery.ttl(timezone.now()) <= 5 * 3600


def test_bundle_resends_its_highest_urgency_and_latest_expiry() -> None:
    """Rows differing in what they stored give the most urgent and latest."""
    account = make_account()
    first, second = BulletinAlert.objects.filter(account=account).order_by("pk")
    later = timezone.now() + datetime.timedelta(hours=9)
    BulletinAlert.objects.filter(pk=first.pk).update(
        push_urgency="high",
        push_expires_at=timezone.now() + datetime.timedelta(hours=2),
        target_date=datetime.date(2026, 11, 13),
    )
    BulletinAlert.objects.filter(pk=second.pk).update(
        push_urgency="low",
        push_expires_at=later,
        target_date=datetime.date(2026, 11, 14),
    )
    _, push = run("--email", account.user.email, "--commit")
    delivery = push.call_args.kwargs["delivery"]
    assert (delivery.urgency, delivery.expires_at) == ("high", later)
    assert delivery.topic == "alert-20261114"


def test_season_start_resends_under_the_season_topic() -> None:
    """Every row a season start: topic ``season``."""
    account = make_account()
    BulletinAlert.objects.filter(account=account).update(
        kind=BulletinAlert.Kind.SEASON_START
    )
    _, push = run("--email", account.user.email, "--commit")
    assert push.call_args.kwargs["delivery"].topic == "season"


def test_ended_bulletin_is_refused() -> None:
    """Past the stored expiry: non-zero, nothing pushed or emailed."""
    account = make_account()
    BulletinAlert.objects.filter(account=account).update(
        push_urgency="high",
        push_expires_at=timezone.now() - datetime.timedelta(minutes=1),
    )
    with (
        patch(f"{COMMAND}.dispatch_push") as push,
        pytest.raises(CommandError, match="ended"),
    ):
        call_command(
            "resend_bulletin_alert",
            "--email",
            account.user.email,
            "--commit",
            stdout=StringIO(),
        )
    push.assert_not_called()
    assert mail.outbox == []
    assert counts()["refused_ended"] == 1
    out, push = run("--email", account.user.email)
    assert "a resend is refused" in out
    push.assert_not_called()


def test_ended_legacy_bulletin_is_refused() -> None:
    """A row with nothing stored is refused once its bulletin's valid_to passed."""
    account = AccountFactory.create()
    PushSubscriptionFactory.create(account=account)
    make_message(account, valid_to=timezone.now() - datetime.timedelta(hours=1))
    with (
        patch(f"{COMMAND}.dispatch_push") as push,
        pytest.raises(CommandError, match="ended"),
    ):
        call_command(
            "resend_bulletin_alert",
            "--email",
            account.user.email,
            "--commit",
            stdout=StringIO(),
        )
    push.assert_not_called()


def test_legacy_row_resends_normal_to_its_bulletin_valid_to() -> None:
    """Nothing stored: normal urgency, held until the bulletin's valid_to."""
    account = AccountFactory.create(alert_email=Account.AlertEmail.NEVER)
    PushSubscriptionFactory.create(account=account)
    ends = timezone.now() + datetime.timedelta(hours=3)
    make_message(account, valid_to=ends)
    out, push = run("--email", account.user.email, "--commit")
    delivery = push.call_args.kwargs["delivery"]
    assert (delivery.urgency, delivery.expires_at) == ("normal", ends)
    assert "urgency normal" in out


def test_an_older_send_with_the_same_copy_is_left_out() -> None:
    """Only the latest send's rows decide the delivery, not earlier lookalikes."""
    account = make_account()
    BulletinAlert.objects.filter(account=account).update(
        push_urgency="high",
        push_expires_at=timezone.now() + datetime.timedelta(hours=9),
    )
    ends = timezone.now() + datetime.timedelta(hours=2)
    newer = make_message(account, at=SENT_AT + datetime.timedelta(hours=1))
    BulletinAlert.objects.filter(pk__in=[r.pk for r in newer]).update(
        push_urgency="normal", push_expires_at=ends
    )
    _, push = run("--email", account.user.email, "--commit")
    delivery = push.call_args.kwargs["delivery"]
    assert (delivery.urgency, delivery.expires_at) == ("normal", ends)


def test_ended_latest_send_is_refused_whatever_came_before() -> None:
    """An older lookalike still running does not revive an ended latest send."""
    account = make_account()
    BulletinAlert.objects.filter(account=account).update(
        push_expires_at=timezone.now() + datetime.timedelta(hours=9)
    )
    newer = make_message(account, at=SENT_AT + datetime.timedelta(hours=1))
    BulletinAlert.objects.filter(pk__in=[r.pk for r in newer]).update(
        push_expires_at=timezone.now() - datetime.timedelta(minutes=1)
    )
    with (
        patch(f"{COMMAND}.dispatch_push") as push,
        pytest.raises(CommandError, match="ended"),
    ):
        call_command(
            "resend_bulletin_alert",
            "--email",
            account.user.email,
            "--commit",
            stdout=StringIO(),
        )
    push.assert_not_called()
