"""
tests/bulletins/management/commands/test_resend_bulletin_alert.py — The resend command.

SNOW-1493: the bare run does nothing; a dry run shows the last alert
and sends nothing; a commit resends the stored message by the account's own
channels, or by ``--channel`` overriding them; the send log is never
written; and the failures exit non-zero.

SNOW-1496: a resend is written afresh in today's cold wording from the
message's bulletins, whatever was stored; only the url is kept.
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

from apps.accounts.models import Account
from apps.accounts.push_service import ALERT_TTL_SECONDS, ALERT_URGENCY
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
) -> list[BulletinAlert]:
    """Write one delivered message: ``rows`` send-log rows sharing a time."""
    bulletin = BulletinFactory.create(source="SLF", issued_at=issued)
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


def run(*args: str, ok: bool = True) -> tuple[str, MagicMock]:
    """Call the command with dispatch_push mocked; return stdout and the mock."""
    out = StringIO()
    with patch(
        f"{COMMAND}.dispatch_push", return_value={"ok": ok, "status": 201}
    ) as push:
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
        assert call.kwargs == {"ttl": ALERT_TTL_SECONDS, "urgency": ALERT_URGENCY}
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
