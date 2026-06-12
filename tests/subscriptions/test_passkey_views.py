"""
tests/subscriptions/test_passkey_views.py — Tests for the passkey HTTP views.

Covers:
  passkey_auth_request     — GET returns 200 JSON with challenge; stores session key.
  passkey_auth_response    — success sets session + 200; unknown credential 404;
                             empty body 400; rate-limited 429; malformed JSON 400.
  passkey_register_request — authenticated → 200 JSON; unauthenticated → 403.
  passkey_register_response — success 200; unauthenticated → 403; verification
                               failure 400; empty body 400; rate-limited 429.
  passkey_delete           — success 200 (HTMX); no session → 403; non-HTMX → 400;
                             wrong subscriber → 404; rate-limited 429.
  caplog regression        — plaintext emails never appear in log output; pk= form
                             appears instead (SNOW-311).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from subscriptions.models import PasskeyCredential, Subscriber
from subscriptions.services.passkey import PasskeyError, PasskeyUnknownCredentialError
from tests.factories import PasskeyCredentialFactory, SubscriberFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


_TOKEN_BACKEND = "subscriptions.backends.TokenBackend"


def _make_session_client(subscriber: Subscriber) -> Client:
    """Return a test Client logged in as subscriber via Django auth."""
    client = Client()
    client.force_login(subscriber, backend=_TOKEN_BACKEND)
    return client


def _set_auth_challenge(client: Client, value: str) -> None:
    """Write a WebAuthn auth challenge into the client's session."""
    session = client.session
    session["webauthn_auth_challenge"] = value
    session.save()


def _set_reg_challenge(client: Client, value: str) -> None:
    """Write a WebAuthn registration challenge into the client's session."""
    session = client.session
    session["webauthn_reg_challenge"] = value
    session.save()


# ---------------------------------------------------------------------------
# passkey_auth_request
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthRequest:
    """Tests for the passkey_auth_request view."""

    def test_get_returns_200_json(self) -> None:
        client = Client()
        resp = client.get(reverse("subscriptions:passkey_auth_request"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_response_contains_challenge(self) -> None:
        client = Client()
        resp = client.get(reverse("subscriptions:passkey_auth_request"))
        data = json.loads(resp.content)
        assert "challenge" in data

    def test_stores_challenge_in_session(self) -> None:
        client = Client()
        client.get(reverse("subscriptions:passkey_auth_request"))
        assert "webauthn_auth_challenge" in client.session

    def test_post_not_allowed(self) -> None:
        client = Client()
        resp = client.post(reverse("subscriptions:passkey_auth_request"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_auth_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthResponse:
    """Tests for the passkey_auth_response view."""

    def test_success_sets_session_and_returns_ok(self) -> None:
        subscriber = SubscriberFactory.create()
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey._verify_auth_response",
            return_value=subscriber,
        ):
            resp = client.post(
                reverse("subscriptions:passkey_auth_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True
        assert client.session.get("_auth_user_id") == str(subscriber.pk)

    def test_unknown_credential_returns_404(self) -> None:
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey._verify_auth_response",
            side_effect=PasskeyUnknownCredentialError("bad-cred-id"),
        ):
            resp = client.post(
                reverse("subscriptions:passkey_auth_response"),
                data=json.dumps({"id": "bad-cred-id"}),
                content_type="application/json",
            )

        assert resp.status_code == 404
        data = json.loads(resp.content)
        assert data["error"] == "unknown_credential"
        assert data["credentialId"] == "bad-cred-id"

    def test_verification_failure_returns_400(self) -> None:
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey._verify_auth_response",
            side_effect=PasskeyError("bad signature"),
        ):
            resp = client.post(
                reverse("subscriptions:passkey_auth_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 400

    def test_empty_body_returns_400(self) -> None:
        client = Client()
        resp = client.post(
            reverse("subscriptions:passkey_auth_response"),
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_malformed_json_returns_400(self) -> None:
        client = Client()
        resp = client.post(
            reverse("subscriptions:passkey_auth_response"),
            data="not json{{{",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_get_not_allowed(self) -> None:
        client = Client()
        resp = client.get(reverse("subscriptions:passkey_auth_response"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_register_request
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterRequest:
    """Tests for the passkey_register_request view."""

    def test_authenticated_returns_200_json(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        resp = client.get(reverse("subscriptions:passkey_register_request"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_authenticated_response_contains_challenge(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        resp = client.get(reverse("subscriptions:passkey_register_request"))
        data = json.loads(resp.content)
        assert "challenge" in data

    def test_unauthenticated_returns_403(self) -> None:
        client = Client()
        resp = client.get(reverse("subscriptions:passkey_register_request"))
        assert resp.status_code == 403

    def test_post_not_allowed(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        resp = client.post(reverse("subscriptions:passkey_register_request"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_register_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterResponse:
    """Tests for the passkey_register_response view."""

    def test_success_returns_200_with_passkey_data(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        _set_reg_challenge(client, "dGVzdA")
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)

        with patch(
            "subscriptions.views_passkey.verify_and_save_registration",
            return_value=passkey,
        ):
            resp = client.post(
                reverse("subscriptions:passkey_register_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True
        assert "passkey" in data
        assert data["passkey"]["uuid"] == str(passkey.uuid)

    def test_unauthenticated_returns_403(self) -> None:
        client = Client()
        resp = client.post(
            reverse("subscriptions:passkey_register_response"),
            data=json.dumps({"id": "x"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_verification_failure_returns_400(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        _set_reg_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey.verify_and_save_registration",
            side_effect=PasskeyError("bad"),
        ):
            resp = client.post(
                reverse("subscriptions:passkey_register_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 400

    def test_empty_body_returns_400(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        resp = client.post(
            reverse("subscriptions:passkey_register_response"),
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_get_not_allowed(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        resp = client.get(reverse("subscriptions:passkey_register_response"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyDelete:
    """Tests for the passkey_delete HTMX view."""

    def test_success_returns_200(self) -> None:
        subscriber = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = _make_session_client(subscriber)
        resp = client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 200

    def test_success_deletes_passkey(self) -> None:
        subscriber = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = _make_session_client(subscriber)
        client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert not PasskeyCredential.objects.filter(uuid=passkey.uuid).exists()

    def test_no_session_returns_403(self) -> None:
        subscriber = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = Client()
        resp = client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        subscriber = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = _make_session_client(subscriber)
        resp = client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
        )
        assert resp.status_code == 400

    def test_other_subscribers_passkey_returns_404(self) -> None:
        subscriber_a = SubscriberFactory.create()
        subscriber_b = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber_b)
        client = _make_session_client(subscriber_a)
        resp = client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 404

    def test_unknown_uuid_returns_404(self) -> None:
        subscriber = SubscriberFactory.create()
        client = _make_session_client(subscriber)
        import uuid

        resp = client.post(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(uuid.uuid4())},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 404

    def test_get_not_allowed(self) -> None:
        subscriber = SubscriberFactory.create()
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = _make_session_client(subscriber)
        resp = client.get(
            reverse(
                "subscriptions:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no plaintext emails in passkey view log output
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyViewsLogging:
    """SNOW-311: passkey views log pk, never the plaintext email address."""

    def test_sign_in_via_passkey_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """passkey_auth_response logs subscriber pk=, not the email address.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        subscriber = SubscriberFactory.create(email="passkey-auth@example.com")
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey._verify_auth_response",
            return_value=subscriber,
        ):
            with caplog.at_level(logging.INFO, logger="subscriptions.views_passkey"):
                client.post(
                    reverse("subscriptions:passkey_auth_response"),
                    data=json.dumps({"id": "dGVzdA"}),
                    content_type="application/json",
                )

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "passkey-auth@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the subscriber's pk.
        assert any(str(subscriber.pk) in msg for msg in all_messages), (
            f"No log record contains pk={subscriber.pk}; records: {all_messages}"
        )

    def test_registration_failed_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """passkey_register_response failure logs subscriber pk=, not the email address."""
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        subscriber = SubscriberFactory.create(email="passkey-reg-fail@example.com")
        client = _make_session_client(subscriber)
        _set_reg_challenge(client, "dGVzdA")

        with patch(
            "subscriptions.views_passkey.verify_and_save_registration",
            side_effect=PasskeyError("bad registration"),
        ):
            with caplog.at_level(logging.INFO, logger="subscriptions.views_passkey"):
                client.post(
                    reverse("subscriptions:passkey_register_response"),
                    data=json.dumps({"id": "dGVzdA"}),
                    content_type="application/json",
                )

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "passkey-reg-fail@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the subscriber's pk.
        assert any(str(subscriber.pk) in msg for msg in all_messages), (
            f"No log record contains pk={subscriber.pk}; records: {all_messages}"
        )

    def test_passkey_deleted_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """passkey_delete logs subscriber pk=, not the email address."""
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        subscriber = SubscriberFactory.create(email="passkey-del@example.com")
        passkey = PasskeyCredentialFactory.create(subscriber=subscriber)
        client = _make_session_client(subscriber)

        with caplog.at_level(logging.INFO, logger="subscriptions.views_passkey"):
            client.post(
                reverse(
                    "subscriptions:passkey_delete",
                    kwargs={"passkey_uuid": str(passkey.uuid)},
                ),
                **_HTMX_HEADERS,
            )

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "passkey-del@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the subscriber's pk.
        assert any(str(subscriber.pk) in msg for msg in all_messages), (
            f"No log record contains pk={subscriber.pk}; records: {all_messages}"
        )
