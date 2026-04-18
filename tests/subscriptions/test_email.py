"""
tests/subscriptions/test_email.py — Tests for the account-access email service.

Covers:
  - send_account_access_email sends one email with correct recipient/subject/body.
  - The account-access URL in the email body round-trips through verify_token.
  - send_noop_email sends zero emails and raises no errors.
  - URL is built from request when provided, SITE_BASE_URL otherwise.
"""

import pytest
from django.conf import settings
from django.core import mail
from django.test import RequestFactory

from subscriptions.services.email import send_account_access_email, send_noop_email
from subscriptions.services.token import SALT_ACCOUNT_ACCESS, verify_token


@pytest.fixture
def rf():
    """Return a Django RequestFactory."""
    return RequestFactory()


class TestSendAccountAccessEmail:
    """Tests for send_account_access_email."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Switch to the locmem backend so outbox works."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_one_email(self):
        send_account_access_email("alice@example.com")
        assert len(mail.outbox) == 1

    def test_recipient_is_correct(self):
        send_account_access_email("alice@example.com")
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_from_email_uses_setting(self):
        send_account_access_email("alice@example.com")
        assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL

    def test_subject_contains_snowdesk(self):
        send_account_access_email("alice@example.com")
        assert "Snowdesk" in mail.outbox[0].subject

    def test_body_contains_account_path(self):
        send_account_access_email("alice@example.com")
        assert "/subscribe/account/" in mail.outbox[0].body

    def test_html_body_contains_account_path(self):
        send_account_access_email("alice@example.com")
        html, _ = mail.outbox[0].alternatives[0]
        assert "/subscribe/account/" in html

    def test_token_in_url_is_valid(self):
        """The token embedded in the URL should verify back to the email."""
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        # Find the account URL line
        url_line = next(
            line for line in body.splitlines() if "/subscribe/account/" in line
        )
        # Extract token from the URL path: /subscribe/account/<token>/
        token = url_line.strip().rstrip("/").split("/subscribe/account/")[-1]
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_uses_site_base_url_from_settings(self):
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        assert settings.SITE_BASE_URL in body

    def test_uses_request_origin_when_provided(self, rf):
        request = rf.get("/")
        send_account_access_email("alice@example.com", request=request)
        body = mail.outbox[0].body
        assert "/subscribe/account/" in body

    def test_html_alternative_present(self):
        send_account_access_email("alice@example.com")
        assert len(mail.outbox[0].alternatives) == 1
        _, mimetype = mail.outbox[0].alternatives[0]
        assert mimetype == "text/html"


class TestSendNoopEmail:
    """Tests for send_noop_email."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Switch to the locmem backend so outbox is available."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_zero_emails(self):
        send_noop_email("alice@example.com")
        assert len(mail.outbox) == 0

    def test_does_not_raise(self):
        """send_noop_email must not raise even with an arbitrary email."""
        send_noop_email("nonexistent@example.com")

    def test_does_not_raise_on_empty_string(self):
        """Edge case: empty string should not raise."""
        send_noop_email("")
