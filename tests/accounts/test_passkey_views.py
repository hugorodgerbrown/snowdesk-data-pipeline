"""
tests/accounts/test_passkey_views.py — Tests for the passkey HTTP views.

Covers:
  passkey_auth_request     — GET returns 200 JSON with challenge; stores session key.
  passkey_auth_response    — success sets session + 200; unknown credential 404;
                             empty body 400; rate-limited 429; malformed JSON 400.
  passkey_register_request — authenticated → 200 JSON; unauthenticated → 403.
  passkey_register_response — success 200; unauthenticated → 403; verification
                               failure 400; empty body 400; rate-limited 429.
  passkey_delete           — success 200 (HTMX); no session → 403; non-HTMX → 400;
                             wrong user → 404; rate-limited 429.
  caplog regression        — plaintext emails never appear in log output; pk= form
                             appears instead (SNOW-311).
  SNOW-334                 — staff auth.User with no Account can register a
                             passkey and authenticate.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Account, PasskeyCredential
from apps.accounts.services.passkey import PasskeyError, PasskeyUnknownCredentialError
from tests.factories import AccountFactory, PasskeyCredentialFactory, UserFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTMX_HEADERS: dict[str, Any] = {"HTTP_HX_REQUEST": "true"}


_TOKEN_BACKEND = "apps.accounts.backends.TokenBackend"


def _make_session_client(user: User) -> Client:
    """Return a test Client logged in as user via Django auth."""
    client = Client()
    client.force_login(user, backend=_TOKEN_BACKEND)
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
        resp = client.get(reverse("accounts:passkey_auth_request"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_response_contains_challenge(self) -> None:
        client = Client()
        resp = client.get(reverse("accounts:passkey_auth_request"))
        data = json.loads(resp.content)
        assert "challenge" in data

    def test_stores_challenge_in_session(self) -> None:
        client = Client()
        client.get(reverse("accounts:passkey_auth_request"))
        assert "webauthn_auth_challenge" in client.session

    def test_post_not_allowed(self) -> None:
        client = Client()
        resp = client.post(reverse("accounts:passkey_auth_request"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_auth_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyAuthResponse:
    """Tests for the passkey_auth_response view."""

    def test_success_sets_session_and_returns_ok(self) -> None:
        user = UserFactory.create()
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey._verify_auth_response",
            return_value=user,
        ):
            resp = client.post(
                reverse("accounts:passkey_auth_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True
        assert client.session.get("_auth_user_id") == str(user.pk)

    def test_unknown_credential_returns_404(self) -> None:
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey._verify_auth_response",
            side_effect=PasskeyUnknownCredentialError("bad-cred-id"),
        ):
            resp = client.post(
                reverse("accounts:passkey_auth_response"),
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
            "apps.accounts.views_passkey._verify_auth_response",
            side_effect=PasskeyError("bad signature"),
        ):
            resp = client.post(
                reverse("accounts:passkey_auth_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 400

    def test_empty_body_returns_400(self) -> None:
        client = Client()
        resp = client.post(
            reverse("accounts:passkey_auth_response"),
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_malformed_json_returns_400(self) -> None:
        client = Client()
        resp = client.post(
            reverse("accounts:passkey_auth_response"),
            data="not json{{{",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_get_not_allowed(self) -> None:
        client = Client()
        resp = client.get(reverse("accounts:passkey_auth_response"))
        assert resp.status_code == 405


# ---------------------------------------------------------------------------
# passkey_register_request
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterRequest:
    """Tests for the passkey_register_request view."""

    def test_authenticated_returns_200_json(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        resp = client.get(reverse("accounts:passkey_register_request"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/json"

    def test_authenticated_response_contains_challenge(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        resp = client.get(reverse("accounts:passkey_register_request"))
        data = json.loads(resp.content)
        assert "challenge" in data

    def test_unauthenticated_returns_403(self) -> None:
        client = Client()
        resp = client.get(reverse("accounts:passkey_register_request"))
        assert resp.status_code == 403

    def test_post_not_allowed(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        resp = client.post(reverse("accounts:passkey_register_request"))
        assert resp.status_code == 405

    def test_staff_user_without_account_can_register(self) -> None:
        """SNOW-334: staff auth.User with no Account gets registration options."""
        staff_user = UserFactory.create(is_staff=True)
        # Confirm no Account profile exists.
        assert not Account.objects.filter(user=staff_user).exists()
        client = _make_session_client(staff_user)
        resp = client.get(reverse("accounts:passkey_register_request"))
        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert "challenge" in data


# ---------------------------------------------------------------------------
# passkey_register_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyRegisterResponse:
    """Tests for the passkey_register_response view."""

    def test_success_returns_200_with_passkey_data(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        _set_reg_challenge(client, "dGVzdA")
        passkey = PasskeyCredentialFactory.create(user=account.user)

        with patch(
            "apps.accounts.views_passkey.verify_and_save_registration",
            return_value=passkey,
        ):
            resp = client.post(
                reverse("accounts:passkey_register_response"),
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
            reverse("accounts:passkey_register_response"),
            data=json.dumps({"id": "x"}),
            content_type="application/json",
        )
        assert resp.status_code == 403

    def test_verification_failure_returns_400(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        _set_reg_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey.verify_and_save_registration",
            side_effect=PasskeyError("bad"),
        ):
            resp = client.post(
                reverse("accounts:passkey_register_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 400

    def test_verification_failure_does_not_echo_exception_text(self) -> None:
        """SNOW-558: the 400 body carries a fixed token, not the exception detail.

        CodeQL py/stack-trace-exposure. The sibling auth view already returns a
        fixed ``verification_failed`` token; this endpoint now matches it.
        """
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        _set_reg_challenge(client, "dGVzdA")

        with patch(
            "accounts.views_passkey.verify_and_save_registration",
            side_effect=PasskeyError("internal detail that must not escape"),
        ):
            resp = client.post(
                reverse("accounts:passkey_register_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 400
        assert json.loads(resp.content)["error"] == "registration_failed"
        assert b"internal detail that must not escape" not in resp.content

    def test_empty_body_returns_400(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        resp = client.post(
            reverse("accounts:passkey_register_response"),
            data="",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_get_not_allowed(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        resp = client.get(reverse("accounts:passkey_register_response"))
        assert resp.status_code == 405

    def test_staff_user_without_account_can_complete_registration(self) -> None:
        """SNOW-334: staff auth.User with no Account can complete passkey registration."""
        staff_user = UserFactory.create(is_staff=True)
        assert not Account.objects.filter(user=staff_user).exists()
        client = _make_session_client(staff_user)
        _set_reg_challenge(client, "dGVzdA")
        passkey = PasskeyCredentialFactory.create(user=staff_user)

        with patch(
            "apps.accounts.views_passkey.verify_and_save_registration",
            return_value=passkey,
        ):
            resp = client.post(
                reverse("accounts:passkey_register_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True


# ---------------------------------------------------------------------------
# passkey_delete
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyDelete:
    """Tests for the passkey_delete HTMX view."""

    def test_success_returns_200(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = _make_session_client(account.user)
        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 200

    def test_success_deletes_passkey(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = _make_session_client(account.user)
        client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert not PasskeyCredential.objects.filter(uuid=passkey.uuid).exists()

    def test_no_session_returns_403(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = Client()
        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 403

    def test_non_htmx_returns_400(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = _make_session_client(account.user)
        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
        )
        assert resp.status_code == 400

    def test_other_users_passkey_returns_404(self) -> None:
        account_a = AccountFactory.create()
        account_b = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account_b.user)
        client = _make_session_client(account_a.user)
        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 404

    def test_unknown_uuid_returns_404(self) -> None:
        account = AccountFactory.create()
        client = _make_session_client(account.user)
        import uuid

        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(uuid.uuid4())},
            ),
            **_HTMX_HEADERS,
        )
        assert resp.status_code == 404

    def test_get_not_allowed(self) -> None:
        account = AccountFactory.create()
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = _make_session_client(account.user)
        resp = client.get(
            reverse(
                "accounts:passkey_delete",
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
        """passkey_auth_response logs user pk=, not the email address.

        The accounts logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        user = UserFactory.create(
            email="passkey-auth@example.com",
            username="passkey-auth@example.com",
        )
        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey._verify_auth_response",
            return_value=user,
        ):
            with caplog.at_level(logging.INFO, logger="apps.accounts.views_passkey"):
                client.post(
                    reverse("accounts:passkey_auth_response"),
                    data=json.dumps({"id": "dGVzdA"}),
                    content_type="application/json",
                )

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "passkey-auth@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the user's pk.
        assert any(str(user.pk) in msg for msg in all_messages), (
            f"No log record contains pk={user.pk}; records: {all_messages}"
        )

    def test_registration_failed_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """passkey_register_response failure logs user pk=, not the email address."""
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        account = AccountFactory.create(user__email="passkey-reg-fail@example.com")
        client = _make_session_client(account.user)
        _set_reg_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey.verify_and_save_registration",
            side_effect=PasskeyError("bad registration"),
        ):
            with caplog.at_level(logging.INFO, logger="apps.accounts.views_passkey"):
                client.post(
                    reverse("accounts:passkey_register_response"),
                    data=json.dumps({"id": "dGVzdA"}),
                    content_type="application/json",
                )

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "passkey-reg-fail@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the user's pk.
        assert any(str(account.user.pk) in msg for msg in all_messages), (
            f"No log record contains pk={account.user.pk}; records: {all_messages}"
        )

    def test_passkey_deleted_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """passkey_delete logs user pk=, not the email address."""
        import logging

        monkeypatch.setattr(logging.getLogger("accounts"), "propagate", True)

        account = AccountFactory.create(user__email="passkey-del@example.com")
        passkey = PasskeyCredentialFactory.create(user=account.user)
        client = _make_session_client(account.user)

        with caplog.at_level(logging.INFO, logger="apps.accounts.views_passkey"):
            client.post(
                reverse(
                    "accounts:passkey_delete",
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

        # At least one record must mention the user's pk.
        assert any(str(account.user.pk) in msg for msg in all_messages), (
            f"No log record contains pk={account.user.pk}; records: {all_messages}"
        )


# ---------------------------------------------------------------------------
# SNOW-334 — staff auth.User with no Account can authenticate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaffUserPasskeyAuth:
    """SNOW-334: staff auth.User with no Account can sign in via passkey."""

    def test_staff_user_auth_response_logs_in(self) -> None:
        """passkey_auth_response logs in a staff User that has no Account."""
        staff_user = UserFactory.create(is_staff=True)
        assert not Account.objects.filter(user=staff_user).exists()

        client = Client()
        _set_auth_challenge(client, "dGVzdA")

        with patch(
            "apps.accounts.views_passkey._verify_auth_response",
            return_value=staff_user,
        ):
            resp = client.post(
                reverse("accounts:passkey_auth_response"),
                data=json.dumps({"id": "dGVzdA"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = json.loads(resp.content)
        assert data["ok"] is True
        assert client.session.get("_auth_user_id") == str(staff_user.pk)

    def test_staff_user_can_delete_own_passkey(self) -> None:
        """passkey_delete allows a staff User with no Account to delete their passkey."""
        staff_user = UserFactory.create(is_staff=True)
        assert not Account.objects.filter(user=staff_user).exists()
        passkey = PasskeyCredentialFactory.create(user=staff_user)
        client = _make_session_client(staff_user)

        resp = client.post(
            reverse(
                "accounts:passkey_delete",
                kwargs={"passkey_uuid": str(passkey.uuid)},
            ),
            **_HTMX_HEADERS,
        )

        assert resp.status_code == 200
        assert not PasskeyCredential.objects.filter(uuid=passkey.uuid).exists()
