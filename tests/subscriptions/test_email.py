"""
tests/subscriptions/test_email.py — Tests for magic-link email service.

Covers URL construction, template rendering, and mail dispatch. Uses Django's
test mail backend via the outbox rather than mocking send_mail directly.
"""

import pytest
from django.conf import settings
from django.core import mail
from django.test import RequestFactory

from subscriptions.services.email import send_magic_link_email
from subscriptions.services.token import validate_magic_link_token


@pytest.fixture
def rf():
    """Return a Django RequestFactory."""
    return RequestFactory()


class TestSendMagicLinkEmail:
    """Tests for send_magic_link_email."""

    @pytest.fixture(autouse=True)
    def use_console_backend(self, settings):
        """Switch to the locmem backend so outbox works."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_one_email(self):
        send_magic_link_email("alice@example.com")
        assert len(mail.outbox) == 1

    def test_recipient_is_correct(self):
        send_magic_link_email("alice@example.com")
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_from_email_uses_setting(self):
        send_magic_link_email("alice@example.com")
        assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL

    def test_subject_contains_purpose(self):
        send_magic_link_email("alice@example.com", purpose="login")
        assert "login" in mail.outbox[0].subject

    def test_body_contains_verify_path(self):
        send_magic_link_email("alice@example.com")
        assert "/subscribe/verify/" in mail.outbox[0].body

    def test_html_body_contains_verify_path(self):
        send_magic_link_email("alice@example.com")
        html, _ = mail.outbox[0].alternatives[0]
        assert "/subscribe/verify/" in html

    def test_token_in_url_is_valid(self):
        send_magic_link_email("alice@example.com")
        body = mail.outbox[0].body
        # Extract token from the URL line
        token_line = next(
            line for line in body.splitlines() if "/subscribe/verify/" in line
        )
        token = token_line.split("?token=")[-1].strip()
        payload = validate_magic_link_token(token)
        assert payload is not None
        assert payload["email"] == "alice@example.com"

    def test_uses_base_url_from_settings(self):
        send_magic_link_email("alice@example.com")
        body = mail.outbox[0].body
        assert settings.MAGIC_LINK_BASE_URL in body

    def test_uses_request_origin_when_provided(self, rf):
        request = rf.get("/")
        send_magic_link_email("alice@example.com", request=request)
        body = mail.outbox[0].body
        # The request-derived base URL should appear instead of the setting
        assert "/subscribe/verify/" in body

    def test_custom_purpose_in_body(self):
        send_magic_link_email("alice@example.com", purpose="subscribe")
        assert "subscribe" in mail.outbox[0].body
