"""
tests/accounts/test_email.py — Tests for the subscription email services.

Covers:
  - send_account_access_email sends one email with correct recipient/subject/body.
  - The account-access URL in the email body round-trips through verify_token.
  - URL is built from request when provided, SITE_BASE_URL otherwise.
  - send_subscription_confirmation_email sends one email with region name in
    subject/body; the embedded URL round-trips through verify_token with
    SALT_ACCOUNT_ACCESS.
  - caplog regression: worker functions log the masked address, not the plaintext
    email (SNOW-311).
"""

from typing import cast

import pytest
from django.conf import settings
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import RequestFactory
from pytest_django.fixtures import SettingsWrapper

from apps.accounts.services.email import (
    send_account_access_email,
    send_subscription_confirmation_email,
)
from apps.accounts.services.token import SALT_ACCOUNT_ACCESS, verify_token
from tests.factories import MicroRegionFactory


@pytest.fixture
def rf() -> RequestFactory:
    """Return a Django RequestFactory."""
    return RequestFactory()


class TestSendAccountAccessEmail:
    """Tests for send_account_access_email."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Switch to the locmem backend so outbox works."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_one_email(self) -> None:
        send_account_access_email("alice@example.com")
        assert len(mail.outbox) == 1

    def test_recipient_is_correct(self) -> None:
        send_account_access_email("alice@example.com")
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_from_email_uses_setting(self) -> None:
        send_account_access_email("alice@example.com")
        assert mail.outbox[0].from_email == settings.DEFAULT_FROM_EMAIL

    def test_subject_contains_snowdesk(self) -> None:
        send_account_access_email("alice@example.com")
        assert "Snowdesk" in mail.outbox[0].subject

    def test_body_contains_account_path(self) -> None:
        send_account_access_email("alice@example.com")
        assert "/account/access/" in mail.outbox[0].body

    def test_html_body_contains_account_path(self) -> None:
        send_account_access_email("alice@example.com")
        html_body, _ = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        html = str(html_body)
        assert "/account/access/" in html

    def test_token_in_url_is_valid(self) -> None:
        """The token embedded in the URL should verify back to the email."""
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        # Find the account URL line
        url_line = next(
            line for line in body.splitlines() if "/account/access/" in line
        )
        # Extract token from the URL path: /account/access/<token>/
        token = url_line.strip().rstrip("/").split("/account/access/")[-1]
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_uses_site_base_url_from_settings(self) -> None:
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        assert settings.SITE_BASE_URL in body

    def test_uses_request_origin_when_provided(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        send_account_access_email("alice@example.com", request=request)
        body = mail.outbox[0].body
        assert "/account/access/" in body

    def test_html_alternative_present(self) -> None:
        send_account_access_email("alice@example.com")
        assert len(cast(EmailMultiAlternatives, mail.outbox[0]).alternatives) == 1
        _, mimetype = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        assert mimetype == "text/html"

    def test_text_body_includes_slf_attribution(self) -> None:
        """SLF licence credit appears in the plain-text body (SNOW-30)."""
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        assert "WSL Institute for Snow and Avalanche Research SLF" in body
        assert "CC BY 4.0" in body

    def test_html_body_includes_slf_attribution(self) -> None:
        """SLF licence credit appears in the HTML alternative (SNOW-30)."""
        send_account_access_email("alice@example.com")
        html_body, _ = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        html = str(html_body)
        assert "WSL Institute for Snow and Avalanche Research SLF" in html
        assert "https://www.slf.ch" in html
        assert "CC BY 4.0" in html


@pytest.mark.django_db
class TestSendSubscriptionConfirmationEmail:
    """Tests for send_subscription_confirmation_email."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Switch to the locmem backend so outbox is available."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_one_email(self) -> None:
        """Exactly one email is dispatched."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert len(mail.outbox) == 1

    def test_recipient_is_correct(self) -> None:
        """Email is addressed to the supplied recipient."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_subject_contains_region_name(self) -> None:
        """Subject line includes the region name."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Engelberg Region" in mail.outbox[0].subject

    def test_subject_contains_snowdesk(self) -> None:
        """Subject line includes the 'Snowdesk' brand name."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Snowdesk" in mail.outbox[0].subject

    def test_body_contains_region_name(self) -> None:
        """Plain-text body includes the region name."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Engelberg Region" in mail.outbox[0].body

    def test_body_contains_account_path(self) -> None:
        """Plain-text body contains the account-access URL path."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "/account/access/" in mail.outbox[0].body

    def test_html_alternative_present(self) -> None:
        """Email includes an HTML alternative."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert len(cast(EmailMultiAlternatives, mail.outbox[0]).alternatives) == 1
        _, mimetype = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        assert mimetype == "text/html"

    def test_html_body_contains_region_name(self) -> None:
        """HTML body includes the region name."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        html_body, _ = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        html = str(html_body)
        assert "Engelberg Region" in html

    def test_token_in_url_uses_account_access_salt(self) -> None:
        """The token embedded in the body URL verifies with SALT_ACCOUNT_ACCESS."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        body = mail.outbox[0].body
        url_line = next(
            line for line in body.splitlines() if "/account/access/" in line
        )
        token = url_line.strip().rstrip("/").split("/account/access/")[-1]
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_uses_request_origin_when_provided(self) -> None:
        """When a request is supplied, the URL reflects its origin."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        rf = RequestFactory()
        request = rf.get("/")
        send_subscription_confirmation_email(
            "alice@example.com", region=region, request=request
        )
        assert "/account/access/" in mail.outbox[0].body

    def test_text_body_includes_slf_attribution(self) -> None:
        """SLF licence credit appears in the plain-text body (SNOW-30)."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        body = mail.outbox[0].body
        assert "WSL Institute for Snow and Avalanche Research SLF" in body
        assert "CC BY 4.0" in body

    def test_html_body_includes_slf_attribution(self) -> None:
        """SLF licence credit appears in the HTML alternative (SNOW-30)."""
        region = MicroRegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        html_body, _ = cast(EmailMultiAlternatives, mail.outbox[0]).alternatives[0]
        html = str(html_body)
        assert "WSL Institute for Snow and Avalanche Research SLF" in html
        assert "https://www.slf.ch" in html
        assert "CC BY 4.0" in html


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no plaintext emails in email service log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEmailServiceLogging:
    """SNOW-311: email worker functions log masked email, never the plaintext address."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings: SettingsWrapper) -> None:
        """Switch to the locmem backend so outbox is populated inline."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_account_access_email_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """send_account_access_email logs the masked address, not the plaintext email.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "caplog-access@example.com"

        with caplog.at_level(logging.INFO, logger="apps.accounts.services.email"):
            send_account_access_email(email)

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form c***@example.com must appear in at least one record.
        assert any("c***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )

    def test_subscription_confirmation_email_logs_masked_not_plaintext(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """send_subscription_confirmation_email logs the masked address, not the plaintext email."""
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        email = "caplog-confirm@example.com"
        region = MicroRegionFactory.create(name="Test Region")

        with caplog.at_level(logging.INFO, logger="apps.accounts.services.email"):
            send_subscription_confirmation_email(email, region=region)

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert email not in msg, f"Plaintext email found in log: {msg!r}"

        # The masked form c***@example.com must appear in at least one record.
        assert any("c***@example.com" in msg for msg in all_messages), (
            f"Masked email not found in any log record; records: {all_messages}"
        )
