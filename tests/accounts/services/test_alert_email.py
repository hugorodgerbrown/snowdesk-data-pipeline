"""
tests/accounts/services/test_alert_email.py — The bulletin alert email (SNOW-1327).

The worker sends from the alerts sender with RFC 8058 one-click headers,
and declines an account that turned email off or never verified.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives

from apps.accounts.models import Account
from apps.accounts.services.alert_email import (
    account_for_unsubscribe,
    send_alert_email,
    unsubscribe_token,
)
from tests.factories import AccountFactory


@pytest.mark.django_db
class TestSendAlertEmail:
    """send_alert_email enqueues a worker that runs inline under test."""

    def test_sends_with_one_click_headers(self, settings: Any) -> None:
        """The message carries List-Unsubscribe and the one-click POST header."""
        settings.ALERTS_FROM_EMAIL = "alerts@example.com"
        account = AccountFactory.create()
        send_alert_email(account, "Verbier · Saturday: Moderate (2)", "Body.", "/map/")
        (message,) = mail.outbox
        assert isinstance(message, EmailMultiAlternatives)
        assert message.from_email == "alerts@example.com"
        assert message.subject == "Verbier · Saturday: Moderate (2)"
        assert message.extra_headers["List-Unsubscribe-Post"] == (
            "List-Unsubscribe=One-Click"
        )
        assert (
            "/account/alerts/unsubscribe/" in message.extra_headers["List-Unsubscribe"]
        )
        assert "Body." in message.body
        html, _ = message.alternatives[0]
        assert "Open the bulletin" in str(html)

    def test_never_is_not_emailed(self) -> None:
        """An account that stopped alert email is skipped by the worker."""
        account = AccountFactory.create(alert_email=Account.AlertEmail.NEVER)
        send_alert_email(account, "t", "b", "/")
        assert mail.outbox == []

    def test_unverified_is_not_emailed(self) -> None:
        """An unproven address is never emailed."""
        account = AccountFactory.create(is_verified=False)
        send_alert_email(account, "t", "b", "/")
        assert mail.outbox == []


@pytest.mark.django_db
def test_unsubscribe_token_round_trip() -> None:
    """A token names its account; a forged one names nobody."""
    account = AccountFactory.create()
    assert account_for_unsubscribe(unsubscribe_token(account)) == account
    assert account_for_unsubscribe("forged:token") is None
