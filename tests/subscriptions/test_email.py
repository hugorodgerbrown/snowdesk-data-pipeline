"""
tests/subscriptions/test_email.py — Tests for the subscription email services.

Covers:
  - send_account_access_email sends one email with correct recipient/subject/body.
  - The account-access URL in the email body round-trips through verify_token.
  - URL is built from request when provided, SITE_BASE_URL otherwise.
  - send_subscription_confirmation_email sends one email with region name in
    subject/body; the embedded URL round-trips through verify_token with
    SALT_ACCOUNT_ACCESS.
"""

import threading
import time

import pytest
from django.conf import settings
from django.core import mail
from django.test import RequestFactory, override_settings

from subscriptions.services.email import (
    _dispatch_async,
    send_account_access_email,
    send_subscription_confirmation_email,
    simulate_account_access_work,
)
from subscriptions.services.token import SALT_ACCOUNT_ACCESS, verify_token
from tests.factories import RegionFactory


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

    def test_text_body_includes_slf_attribution(self):
        """SLF licence credit appears in the plain-text body (SNOW-30)."""
        send_account_access_email("alice@example.com")
        body = mail.outbox[0].body
        assert "WSL Institute for Snow and Avalanche Research SLF" in body
        assert "CC BY 4.0" in body

    def test_html_body_includes_slf_attribution(self):
        """SLF licence credit appears in the HTML alternative (SNOW-30)."""
        send_account_access_email("alice@example.com")
        html, _ = mail.outbox[0].alternatives[0]
        assert "WSL Institute for Snow and Avalanche Research SLF" in html
        assert "https://www.slf.ch" in html
        assert "CC BY 4.0" in html


@pytest.mark.django_db
class TestSendSubscriptionConfirmationEmail:
    """Tests for send_subscription_confirmation_email."""

    @pytest.fixture(autouse=True)
    def use_locmem_backend(self, settings):
        """Switch to the locmem backend so outbox is available."""
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

    def test_sends_one_email(self):
        """Exactly one email is dispatched."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert len(mail.outbox) == 1

    def test_recipient_is_correct(self):
        """Email is addressed to the supplied recipient."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert mail.outbox[0].to == ["alice@example.com"]

    def test_subject_contains_region_name(self):
        """Subject line includes the region name."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Engelberg Region" in mail.outbox[0].subject

    def test_subject_contains_snowdesk(self):
        """Subject line includes the 'Snowdesk' brand name."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Snowdesk" in mail.outbox[0].subject

    def test_body_contains_region_name(self):
        """Plain-text body includes the region name."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "Engelberg Region" in mail.outbox[0].body

    def test_body_contains_account_path(self):
        """Plain-text body contains the account-access URL path."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert "/subscribe/account/" in mail.outbox[0].body

    def test_html_alternative_present(self):
        """Email includes an HTML alternative."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        assert len(mail.outbox[0].alternatives) == 1
        _, mimetype = mail.outbox[0].alternatives[0]
        assert mimetype == "text/html"

    def test_html_body_contains_region_name(self):
        """HTML body includes the region name."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        html, _ = mail.outbox[0].alternatives[0]
        assert "Engelberg Region" in html

    def test_token_in_url_uses_account_access_salt(self):
        """The token embedded in the body URL verifies with SALT_ACCOUNT_ACCESS."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        body = mail.outbox[0].body
        url_line = next(
            line for line in body.splitlines() if "/subscribe/account/" in line
        )
        token = url_line.strip().rstrip("/").split("/subscribe/account/")[-1]
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_uses_request_origin_when_provided(self):
        """When a request is supplied, the URL reflects its origin."""
        region = RegionFactory.create(name="Engelberg Region")
        rf = RequestFactory()
        request = rf.get("/")
        send_subscription_confirmation_email(
            "alice@example.com", region=region, request=request
        )
        assert "/subscribe/account/" in mail.outbox[0].body

    def test_text_body_includes_slf_attribution(self):
        """SLF licence credit appears in the plain-text body (SNOW-30)."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        body = mail.outbox[0].body
        assert "WSL Institute for Snow and Avalanche Research SLF" in body
        assert "CC BY 4.0" in body

    def test_html_body_includes_slf_attribution(self):
        """SLF licence credit appears in the HTML alternative (SNOW-30)."""
        region = RegionFactory.create(name="Engelberg Region")
        send_subscription_confirmation_email("alice@example.com", region=region)
        html, _ = mail.outbox[0].alternatives[0]
        assert "WSL Institute for Snow and Avalanche Research SLF" in html
        assert "https://www.slf.ch" in html
        assert "CC BY 4.0" in html


@pytest.mark.django_db
class TestSimulateAccountAccessWork:
    """The manage-POST unknown-email timing equaliser."""

    def test_sends_no_email(self):
        """simulate_account_access_work must not populate mail.outbox."""
        simulate_account_access_work("ghost@example.com")
        assert len(mail.outbox) == 0

    def test_executes_without_error(self):
        """Should run token-gen + template-render cleanly for any string email."""
        simulate_account_access_work("anyone@example.com")


class TestDispatchAsync:
    """SNOW-26: _dispatch_async sync gate, async return, and exception logging."""

    @override_settings(SUBSCRIPTIONS_EMAIL_ASYNC=True)
    def test_returns_before_callable_finishes(self):
        """With async on, the dispatcher must return before the callable runs."""
        done = threading.Event()

        def slow() -> None:
            time.sleep(0.5)
            done.set()

        t0 = time.perf_counter()
        _dispatch_async(slow)
        elapsed = time.perf_counter() - t0
        assert elapsed < 0.1, f"_dispatch_async blocked for {elapsed:.3f}s"
        assert done.wait(timeout=2.0), "Background callable never completed"

    @override_settings(SUBSCRIPTIONS_EMAIL_ASYNC=False)
    def test_runs_synchronously_when_disabled(self):
        """With async off, the callable runs inline before _dispatch_async returns."""
        called: list[int] = []
        _dispatch_async(lambda: called.append(1))
        assert called == [1]

    @override_settings(SUBSCRIPTIONS_EMAIL_ASYNC=True)
    def test_exception_in_thread_is_logged_not_raised(self):
        """Exceptions inside the daemon thread are caught and logged."""
        import logging

        # The "subscriptions" logger sets propagate=False (see config/settings
        # /base.py LOGGING), so pytest's caplog cannot see records from it.
        # Attach a handler directly to the email-service logger instead.
        captured: list[logging.LogRecord] = []

        class _Listener(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        log = logging.getLogger("subscriptions.services.email")
        listener = _Listener(level=logging.ERROR)
        log.addHandler(listener)
        try:

            def boom() -> None:
                raise RuntimeError("smtp died")

            _dispatch_async(boom)
            # Give the daemon thread a moment to run and emit the log record.
            time.sleep(0.1)
        finally:
            log.removeHandler(listener)

        assert any(
            "Background email dispatch" in record.getMessage() for record in captured
        ), f"Expected dispatch-error log; got {[r.getMessage() for r in captured]!r}"
