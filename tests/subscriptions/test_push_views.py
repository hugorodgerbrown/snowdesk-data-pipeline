"""
tests/subscriptions/test_push_views.py — Tests for Web Push JSON endpoints.

Covers all three push endpoints (push_register, push_unregister, push_test):
  - Anonymous users are redirected to /admin/login/ (302).
  - Non-staff authenticated users are also redirected (302).
  - Staff POST without CSRF token is rejected (403) when enforce_csrf_checks=True.
  - Staff POST succeeds (200) and the expected side effect occurs.
  - SNOW-311 caplog regression: log records contain pk=, never the subscriber email.

dispatch_push is patched throughout so no real push deliveries occur.

Note on the CSRF+credential test: Django's test Client with enforce_csrf_checks=True
and a CSRF token sourced from the middleware directly is used for the 403 test. The
functional happy-path tests use the default Client (CSRF checks disabled) to verify
the side effects — the CSRF enforcement itself is already exercised by the explicit
403 test.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client

from subscriptions.models import PushSubscription
from tests.factories import PushSubscriptionFactory, SubscriberFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REGISTER_URL = "/subscribe/push/register/"
_UNREGISTER_URL = "/subscribe/push/unregister/"
_TEST_URL = "/subscribe/push/test/"

_REGISTER_BODY: dict[str, Any] = {
    "endpoint": "https://push.example.com/unique-endpoint-views-test",
    "keys": {
        "p256dh": "test-p256dh-key",
        "auth": "test-auth-secret",
    },
}

_UNREGISTER_BODY: dict[str, Any] = {
    "endpoint": "https://push.example.com/unique-endpoint-views-test",
}

_TEST_BODY: dict[str, Any] = {
    "endpoint": "https://push.example.com/unique-endpoint-views-test",
    "title": "Test",
    "body": "Hello",
    "url": "/",
}


def _post_json(client: Client, url: str, data: dict[str, Any]) -> Any:
    """POST JSON with Content-Type header."""
    return client.post(
        url,
        data=json.dumps(data),
        content_type="application/json",
    )


@pytest.fixture()
def staff_user(db: Any) -> Any:
    """Return a staff Subscriber."""
    return UserFactory.create()


@pytest.fixture()
def regular_user(db: Any) -> Any:
    """Return a non-staff (regular) Subscriber."""
    return SubscriberFactory.create()


@pytest.fixture()
def staff_client(staff_user: Any) -> Client:
    """Django test Client logged in as a staff user (CSRF checks disabled)."""
    c = Client()
    c.force_login(staff_user)
    return c


# ---------------------------------------------------------------------------
# push_register
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushRegister:
    """Tests for /subscribe/push/register/."""

    def test_anonymous_redirected_to_admin_login(self) -> None:
        """Anonymous POST is redirected to admin login."""
        response = _post_json(Client(), _REGISTER_URL, _REGISTER_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_redirected_to_admin_login(self, regular_user: Any) -> None:
        """Non-staff authenticated user is also redirected."""
        c = Client()
        c.force_login(regular_user.user)
        response = _post_json(c, _REGISTER_URL, _REGISTER_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_without_csrf_rejected(self, staff_user: Any) -> None:
        """Staff POST without CSRF token is rejected with 403.

        Uses enforce_csrf_checks=True to activate Django's CSRF middleware
        check — the default test Client bypasses it.
        """
        c = Client(enforce_csrf_checks=True)
        c.force_login(staff_user)
        response = _post_json(c, _REGISTER_URL, _REGISTER_BODY)
        assert response.status_code == 403

    def test_staff_creates_row(self, staff_client: Client, staff_user: Any) -> None:
        """Staff POST creates a PushSubscription row."""
        response = _post_json(staff_client, _REGISTER_URL, _REGISTER_BODY)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["created"] is True
        assert PushSubscription.objects.filter(
            endpoint=_REGISTER_BODY["endpoint"]
        ).exists()

    def test_staff_register_without_profile_has_null_subscriber(
        self, staff_client: Client, staff_user: Any
    ) -> None:
        """A staff user with no Subscriber profile registers with subscriber=None.

        Push registrations are staff-only; a plain staff User (no Subscriber
        profile) will result in a PushSubscription with a null subscriber FK.
        """
        _post_json(staff_client, _REGISTER_URL, _REGISTER_BODY)
        sub = PushSubscription.objects.get(endpoint=_REGISTER_BODY["endpoint"])
        assert sub.subscriber_id is None

    def test_missing_fields_returns_400(self, staff_client: Client) -> None:
        """POST with missing fields returns 400."""
        response = _post_json(staff_client, _REGISTER_URL, {"endpoint": "only-this"})
        assert response.status_code == 400
        assert response.json()["ok"] is False

    def test_duplicate_endpoint_upserts_not_duplicates(
        self, staff_client: Client
    ) -> None:
        """Registering the same endpoint twice upserts the row rather than creating a second.

        First registration returns created=True; the second returns created=False.
        Only one row exists after both calls, and its p256dh/auth reflect the
        second registration's values.
        """
        endpoint = "https://push.example.com/upsert-test-endpoint"
        first_body: dict[str, Any] = {
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-first", "auth": "auth-first"},
        }
        second_body: dict[str, Any] = {
            "endpoint": endpoint,
            "keys": {"p256dh": "p256dh-second", "auth": "auth-second"},
        }

        resp1 = _post_json(staff_client, _REGISTER_URL, first_body)
        assert resp1.status_code == 200
        assert resp1.json()["created"] is True

        resp2 = _post_json(staff_client, _REGISTER_URL, second_body)
        assert resp2.status_code == 200
        assert resp2.json()["created"] is False

        # Exactly one row for this endpoint.
        assert PushSubscription.objects.filter(endpoint=endpoint).count() == 1

        # The row carries the updated keys from the second registration.
        row = PushSubscription.objects.get(endpoint=endpoint)
        assert row.p256dh == "p256dh-second"
        assert row.auth == "auth-second"


# ---------------------------------------------------------------------------
# push_unregister
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushUnregister:
    """Tests for /subscribe/push/unregister/."""

    def test_anonymous_redirected_to_admin_login(self) -> None:
        """Anonymous POST is redirected to admin login."""
        response = _post_json(Client(), _UNREGISTER_URL, _UNREGISTER_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_redirected_to_admin_login(self, regular_user: Any) -> None:
        """Non-staff authenticated user is also redirected."""
        c = Client()
        c.force_login(regular_user.user)
        response = _post_json(c, _UNREGISTER_URL, _UNREGISTER_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_without_csrf_rejected(self, staff_user: Any) -> None:
        """Staff POST without CSRF token is rejected with 403."""
        c = Client(enforce_csrf_checks=True)
        c.force_login(staff_user)
        response = _post_json(c, _UNREGISTER_URL, _UNREGISTER_BODY)
        assert response.status_code == 403

    def test_staff_deletes_row(self, staff_client: Client) -> None:
        """Staff POST deletes the matching PushSubscription."""
        push_sub = PushSubscriptionFactory.create(
            endpoint="https://push.example.com/unregister-me"
        )
        body = {"endpoint": push_sub.endpoint}
        response = _post_json(staff_client, _UNREGISTER_URL, body)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["deleted"] == 1
        assert not PushSubscription.objects.filter(pk=push_sub.pk).exists()

    def test_missing_endpoint_returns_400(self, staff_client: Client) -> None:
        """POST without endpoint returns 400."""
        response = _post_json(staff_client, _UNREGISTER_URL, {})
        assert response.status_code == 400
        assert response.json()["ok"] is False


# ---------------------------------------------------------------------------
# push_test
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushTest:
    """Tests for /subscribe/push/test/."""

    def test_anonymous_redirected_to_admin_login(self) -> None:
        """Anonymous POST is redirected to admin login."""
        response = _post_json(Client(), _TEST_URL, _TEST_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_non_staff_redirected_to_admin_login(self, regular_user: Any) -> None:
        """Non-staff authenticated user is also redirected."""
        c = Client()
        c.force_login(regular_user.user)
        response = _post_json(c, _TEST_URL, _TEST_BODY)
        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_staff_without_csrf_rejected(self, staff_user: Any) -> None:
        """Staff POST without CSRF token is rejected with 403."""
        c = Client(enforce_csrf_checks=True)
        c.force_login(staff_user)
        response = _post_json(c, _TEST_URL, _TEST_BODY)
        assert response.status_code == 403

    def test_staff_dispatches_push(self, staff_client: Client) -> None:
        """Staff POST calls dispatch_push and returns per-row results."""
        push_sub = PushSubscriptionFactory.create(
            endpoint="https://push.example.com/test-target"
        )
        body = {
            "endpoint": push_sub.endpoint,
            "title": "Hi",
            "body": "Test",
            "url": "/",
        }
        fake_result = {"ok": True, "status": 201}
        with patch(
            "subscriptions.push_views.dispatch_push", return_value=fake_result
        ) as mock_dispatch:
            response = _post_json(staff_client, _TEST_URL, body)
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["sent"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["ok"] is True
        mock_dispatch.assert_called_once()

    def test_dispatch_push_called_for_every_matching_row(
        self, staff_client: Client
    ) -> None:
        """Without endpoint filter, dispatch_push is called for every stored sub."""
        PushSubscriptionFactory.create()
        PushSubscriptionFactory.create()
        fake_result = {"ok": True, "status": 201}
        with patch(
            "subscriptions.push_views.dispatch_push", return_value=fake_result
        ) as mock_dispatch:
            response = _post_json(staff_client, _TEST_URL, {})
        assert response.status_code == 200
        data = response.json()
        assert data["sent"] == mock_dispatch.call_count


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no subscriber email in push_views log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPushRegisterLogging:
    """SNOW-311: push_register logs pk + endpoint, never the subscriber email."""

    def test_register_log_contains_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """push_register log record contains pk= and never the subscriber email.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)
        staff = UserFactory.create()
        client = Client()
        client.force_login(staff)

        body: dict[str, Any] = {
            "endpoint": "https://push.example.com/caplog-test-endpoint",
            "keys": {
                "p256dh": "test-p256dh-caplog",
                "auth": "test-auth-caplog",
            },
        }

        with caplog.at_level(logging.INFO, logger="subscriptions.push_views"):
            _post_json(client, _REGISTER_URL, body)

        all_messages = [r.getMessage() for r in caplog.records]

        # Plaintext email must not appear.
        for msg in all_messages:
            assert staff.email not in msg, f"Staff email found in log: {msg!r}"

        # At least one record must mention pk=.
        obj = PushSubscription.objects.get(endpoint=body["endpoint"])
        assert any(f"pk={obj.pk}" in msg for msg in all_messages), (
            f"No log record contains pk={obj.pk}; records: {all_messages}"
        )
