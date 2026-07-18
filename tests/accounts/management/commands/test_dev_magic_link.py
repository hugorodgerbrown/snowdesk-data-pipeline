"""
tests/accounts/management/commands/test_dev_magic_link.py — Tests for the
dev_magic_link management command.

Covers:
  - A new subscriber is created and a magic-link URL is printed.
  - An existing pending subscriber is reactivated to ACTIVE.
  - Email addresses are normalised to lowercase (Invariant 2).
  - The --email argument is required.
  - Production refusal: when DEBUG=False the command raises CommandError.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone

from accounts.models import Subscriber
from accounts.services.token import SALT_ACCOUNT_ACCESS, generate_token
from tests.factories import SubscriberFactory

User = get_user_model()


def _run(email: str, capsys: pytest.CaptureFixture[str]) -> str:
    """Invoke dev_magic_link for ``email`` and return the captured stdout."""
    call_command("dev_magic_link", email=email)
    return capsys.readouterr().out


@pytest.mark.django_db
class TestDevMagicLinkCreatesSubscriber:
    """Subscriber-creation behaviour."""

    def test_creates_subscriber_when_absent(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A subscriber is created for an unknown email."""
        assert not Subscriber.objects.by_email("new@example.com").exists()
        _run("new@example.com", capsys)
        subscriber = Subscriber.objects.get(user__email="new@example.com")
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert subscriber.confirmed_at is not None

    def test_prints_magic_link_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The printed URL is the account-access magic link for the email."""
        out = _run("new@example.com", capsys)
        expected_token = generate_token("new@example.com", salt=SALT_ACCOUNT_ACCESS)
        base = str(settings.WEBAUTHN_ORIGIN).rstrip("/")
        assert f"{base}/account/access/{expected_token}/" in out


@pytest.mark.django_db
class TestDevMagicLinkReactivates:
    """Reactivation of an existing pending subscriber."""

    def test_reactivates_pending_subscriber(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A pending subscriber is promoted to ACTIVE with confirmed_at set."""
        subscriber = SubscriberFactory.create(
            user__email="pending@example.com",
            status=Subscriber.Status.PENDING,
            confirmed_at=None,
        )
        _run("pending@example.com", capsys)
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert subscriber.confirmed_at is not None

    def test_does_not_create_duplicate_for_existing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Running against an existing email does not create a second subscriber."""
        SubscriberFactory.create(
            user__email="pending@example.com",
            status=Subscriber.Status.PENDING,
        )
        _run("pending@example.com", capsys)
        assert Subscriber.objects.by_email("pending@example.com").count() == 1

    def test_leaves_active_subscriber_active(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An already-active subscriber stays active and keeps its confirmed_at."""
        confirmed = timezone.now()
        subscriber = SubscriberFactory.create(
            user__email="active@example.com",
            status=Subscriber.Status.ACTIVE,
            confirmed_at=confirmed,
        )
        _run("active@example.com", capsys)
        subscriber.refresh_from_db()
        assert subscriber.status == Subscriber.Status.ACTIVE
        assert subscriber.confirmed_at == confirmed


@pytest.mark.django_db
class TestDevMagicLinkEmailNormalisation:
    """Email-lowercasing (Invariant 2)."""

    def test_email_lowercased(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A mixed-case email is stored lowercase and resolves to one subscriber."""
        _run("MixedCase@Example.com", capsys)
        assert Subscriber.objects.by_email("mixedcase@example.com").exists()
        user = User.objects.get(username="mixedcase@example.com")
        assert user.email == "mixedcase@example.com"

    def test_mixed_case_matches_existing_lowercase(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A mixed-case argument reuses an existing lowercase subscriber."""
        SubscriberFactory.create(user__email="user@example.com")
        _run("User@Example.com", capsys)
        assert Subscriber.objects.by_email("user@example.com").count() == 1


@pytest.mark.django_db
class TestDevMagicLinkArguments:
    """Argument handling."""

    def test_email_argument_required(self) -> None:
        """Omitting --email raises CommandError."""
        with pytest.raises(CommandError):
            call_command("dev_magic_link")


@pytest.mark.django_db
class TestDevMagicLinkProductionRefusal:
    """Production-guard tests."""

    @override_settings(DEBUG=False)
    def test_raises_command_error_when_debug_false(self) -> None:
        """dev_magic_link raises CommandError when DEBUG=False."""
        with pytest.raises(CommandError):
            call_command("dev_magic_link", email="new@example.com")

    def test_runs_when_debug_true(self, capsys: pytest.CaptureFixture[str]) -> None:
        """dev_magic_link runs without error when DEBUG=True (the test default)."""
        assert settings.DEBUG
        _run("new@example.com", capsys)
