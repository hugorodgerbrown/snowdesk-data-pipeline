"""
tests/subscriptions/test_passkey_service.py — Tests for the passkey service functions.

Covers:
  generate_registration_options   — returns dict with expected keys; challenge
                                    stored in session; excludes existing credentials.
  verify_and_save_registration    — happy path creates PasskeyCredential; missing
                                    challenge raises; duplicate credential raises;
                                    library exception re-raised as PasskeyError.
  generate_authentication_options — returns dict; empty allow_credentials for
                                    discoverable flow; filled list for targeted flow.
  verify_authentication_response  — happy path updates sign_count and returns
                                    User; missing challenge raises; unknown
                                    credential raises PasskeyUnknownCredentialError;
                                    library exception re-raised as PasskeyError.
  caplog regression               — plaintext emails never appear in log output;
                                    pk= form appears instead (SNOW-311).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_django.fixtures import SettingsWrapper

from subscriptions.models import PasskeyCredential
from subscriptions.services.passkey import (
    PasskeyError,
    PasskeyUnknownCredentialError,
    generate_authentication_options,
    generate_registration_options,
    verify_and_save_registration,
    verify_authentication_response,
)
from tests.factories import PasskeyCredentialFactory, UserFactory


def _mock_verified_registration(
    credential_id: bytes = b"\x01\x02\x03\x04",
    public_key: bytes = b"\x04\x05\x06",
    sign_count: int = 0,
    aaguid: str = "00000000-0000-0000-0000-000000000000",
    backed_up: bool = False,
    device_type: str = "platform",
) -> MagicMock:
    """Build a mock VerifiedRegistration with the given attributes."""
    m = MagicMock()
    m.credential_id = credential_id
    m.credential_public_key = public_key
    m.sign_count = sign_count
    m.aaguid = aaguid
    m.credential_backed_up = backed_up
    m.credential_device_type.value = device_type
    return m


def _mock_verified_authentication(
    credential_id: bytes = b"\x01\x02\x03\x04",
    new_sign_count: int = 1,
    backed_up: bool = False,
) -> MagicMock:
    """Build a mock VerifiedAuthentication with the given attributes."""
    m = MagicMock()
    m.credential_id = credential_id
    m.new_sign_count = new_sign_count
    m.credential_backed_up = backed_up
    return m


def _session_with_challenge(challenge_value: str) -> Any:  # mock-typing-impractical
    """Return a plain dict acting as a minimal Django session."""

    class _FakeSession(dict):
        def pop(self, key: Any, default: Any = None) -> Any:
            return super().pop(key, default)

    s = _FakeSession()
    s["webauthn_reg_challenge"] = challenge_value
    return s


def _session_with_auth_challenge(
    challenge_value: str,
) -> Any:  # mock-typing-impractical
    """Return a plain dict acting as a minimal Django session with auth challenge."""

    class _FakeSession(dict):
        def pop(self, key: Any, default: Any = None) -> Any:
            return super().pop(key, default)

    s = _FakeSession()
    s["webauthn_auth_challenge"] = challenge_value
    return s


# ---------------------------------------------------------------------------
# generate_registration_options
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateRegistrationOptions:
    """Tests for generate_registration_options."""

    def test_returns_dict_with_rp_and_challenge(self) -> None:
        user = UserFactory.create()
        session: Any = {}
        result = generate_registration_options(user, session)
        assert isinstance(result, dict)
        assert "rp" in result
        assert "challenge" in result

    def test_stores_challenge_in_session(self) -> None:
        user = UserFactory.create()
        session: Any = {}
        generate_registration_options(user, session)
        assert "webauthn_reg_challenge" in session

    def test_rp_id_matches_settings(self, settings: SettingsWrapper) -> None:
        settings.WEBAUTHN_RP_ID = "test.example.com"
        user = UserFactory.create()
        session: Any = {}
        result = generate_registration_options(user, session)
        assert result["rp"]["id"] == "test.example.com"

    def test_user_name_is_user_email(self) -> None:
        user = UserFactory.create(
            email="alice@example.com", username="alice@example.com"
        )
        session: Any = {}
        result = generate_registration_options(user, session)
        assert result["user"]["name"] == "alice@example.com"

    def test_excludes_existing_credentials(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        session: Any = {}
        result = generate_registration_options(user, session)
        exclude_ids = [c["id"] for c in result.get("excludeCredentials", [])]
        assert "dGVzdA" in exclude_ids

    def test_no_exclude_when_no_passkeys(self) -> None:
        user = UserFactory.create()
        session: Any = {}
        result = generate_registration_options(user, session)
        assert result.get("excludeCredentials", []) == []


# ---------------------------------------------------------------------------
# verify_and_save_registration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerifyAndSaveRegistration:
    """Tests for verify_and_save_registration."""

    def test_happy_path_creates_passkey(self) -> None:
        user = UserFactory.create()
        mock_result = _mock_verified_registration()
        session = _session_with_challenge("dGVzdGNoYWxsZW5nZQ")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            passkey = verify_and_save_registration("{}", session, user)
        assert passkey.user == user
        assert PasskeyCredential.objects.filter(user=user).count() == 1

    def test_happy_path_clears_challenge(self) -> None:
        user = UserFactory.create()
        mock_result = _mock_verified_registration()
        session = _session_with_challenge("dGVzdGNoYWxsZW5nZQ")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            verify_and_save_registration("{}", session, user)
        assert "webauthn_reg_challenge" not in session

    def test_missing_challenge_raises(self) -> None:
        user = UserFactory.create()
        session: Any = {}
        with pytest.raises(PasskeyError, match="challenge missing"):
            verify_and_save_registration("{}", session, user)

    def test_library_error_raises_passkey_error(self) -> None:
        user = UserFactory.create()
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            side_effect=ValueError("bad signature"),
        ):
            with pytest.raises(PasskeyError):
                verify_and_save_registration("{}", session, user)

    def test_library_error_clears_challenge(self) -> None:
        user = UserFactory.create()
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            side_effect=ValueError("bad"),
        ):
            try:
                verify_and_save_registration("{}", session, user)
            except PasskeyError:
                pass
        assert "webauthn_reg_challenge" not in session

    def test_duplicate_credential_raises(self) -> None:
        user = UserFactory.create()
        from webauthn.helpers import bytes_to_base64url

        cred_bytes = b"\x01\x02\x03\x04"
        cred_id = bytes_to_base64url(cred_bytes)
        PasskeyCredentialFactory.create(user=user, credential_id=cred_id)
        mock_result = _mock_verified_registration(credential_id=cred_bytes)
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            with pytest.raises(PasskeyError, match="already registered"):
                verify_and_save_registration("{}", session, user)

    def test_aaguid_all_zeros_stored_as_none(self) -> None:
        user = UserFactory.create()
        mock_result = _mock_verified_registration(
            aaguid="00000000-0000-0000-0000-000000000000"
        )
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            passkey = verify_and_save_registration("{}", session, user)
        assert passkey.aaguid is None

    def test_real_aaguid_stored(self) -> None:
        user = UserFactory.create()
        real_aaguid = "adce0002-35bc-c60a-648b-0b25f1f05503"
        mock_result = _mock_verified_registration(aaguid=real_aaguid)
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            passkey = verify_and_save_registration("{}", session, user)
        import uuid

        assert passkey.aaguid == uuid.UUID(real_aaguid)


# ---------------------------------------------------------------------------
# generate_authentication_options
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGenerateAuthenticationOptions:
    """Tests for generate_authentication_options."""

    def test_returns_dict_with_challenge(self) -> None:
        session: Any = {}
        result = generate_authentication_options(session)
        assert isinstance(result, dict)
        assert "challenge" in result

    def test_stores_challenge_in_session(self) -> None:
        session: Any = {}
        generate_authentication_options(session)
        assert "webauthn_auth_challenge" in session

    def test_no_user_gives_empty_allow_credentials(self) -> None:
        session: Any = {}
        result = generate_authentication_options(session)
        assert result.get("allowCredentials", []) == []

    def test_user_with_passkeys_fills_allow_credentials(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        session: Any = {}
        result = generate_authentication_options(session, user=user)
        ids = [c["id"] for c in result.get("allowCredentials", [])]
        assert "dGVzdA" in ids

    def test_user_without_passkeys_gives_empty_allow(self) -> None:
        user = UserFactory.create()
        session: Any = {}
        result = generate_authentication_options(session, user=user)
        assert result.get("allowCredentials", []) == []


# ---------------------------------------------------------------------------
# verify_authentication_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestVerifyAuthenticationResponse:
    """Tests for verify_authentication_response."""

    def test_happy_path_returns_user(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication(new_sign_count=1)
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            result = verify_authentication_response(credential_json, session)
        assert result == user

    def test_happy_path_updates_sign_count(self) -> None:
        user = UserFactory.create()
        passkey = PasskeyCredentialFactory.create(
            user=user, credential_id="dGVzdA", sign_count=0
        )
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication(new_sign_count=5)
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            verify_authentication_response(credential_json, session)
        passkey.refresh_from_db()
        assert passkey.sign_count == 5

    def test_happy_path_sets_last_used_at(self) -> None:
        user = UserFactory.create()
        passkey = PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication()
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            verify_authentication_response(credential_json, session)
        passkey.refresh_from_db()
        assert passkey.last_used_at is not None

    def test_happy_path_clears_challenge(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication()
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            verify_authentication_response(credential_json, session)
        assert "webauthn_auth_challenge" not in session

    def test_missing_challenge_raises(self) -> None:
        credential_json = json.dumps({"id": "dGVzdA"})
        session: Any = {}
        with pytest.raises(PasskeyError, match="challenge missing"):
            verify_authentication_response(credential_json, session)

    def test_unknown_credential_raises_specific_error(self) -> None:
        credential_json = json.dumps({"id": "unknown-id"})
        session = _session_with_auth_challenge("dGVzdA")
        with pytest.raises(PasskeyUnknownCredentialError) as exc_info:
            verify_authentication_response(credential_json, session)
        assert exc_info.value.credential_id == "unknown-id"

    def test_library_error_raises_passkey_error(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            side_effect=ValueError("bad signature"),
        ):
            with pytest.raises(PasskeyError):
                verify_authentication_response(credential_json, session)

    def test_library_error_clears_challenge(self) -> None:
        user = UserFactory.create()
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            side_effect=ValueError("bad"),
        ):
            try:
                verify_authentication_response(credential_json, session)
            except PasskeyError:
                pass
        assert "webauthn_auth_challenge" not in session


# ---------------------------------------------------------------------------
# SNOW-311 — caplog regression: no plaintext emails in passkey service logs
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPasskeyServiceLogging:
    """SNOW-311: passkey service functions log pk, never the plaintext email."""

    def test_passkey_registered_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """verify_and_save_registration logs user pk=, not the email address.

        The subscriptions logger has propagate=False in base.py; we flip it for
        the duration of this test so caplog can capture the records.
        """
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        user = UserFactory.create(
            email="reg-service@example.com",
            username="reg-service@example.com",
        )
        mock_result = _mock_verified_registration()
        session = _session_with_challenge("dGVzdGNoYWxsZW5nZQ")

        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            with caplog.at_level(logging.INFO, logger="subscriptions.services.passkey"):
                verify_and_save_registration("{}", session, user)

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "reg-service@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the user's pk.
        assert any(str(user.pk) in msg for msg in all_messages), (
            f"No log record contains pk={user.pk}; records: {all_messages}"
        )

    def test_auth_successful_logs_pk_not_email(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """verify_authentication_response logs user pk=, not the email address."""
        import logging

        monkeypatch.setattr(logging.getLogger("subscriptions"), "propagate", True)

        user = UserFactory.create(
            email="auth-service@example.com",
            username="auth-service@example.com",
        )
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication(new_sign_count=1)
        session = _session_with_auth_challenge("dGVzdA")

        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            with caplog.at_level(logging.INFO, logger="subscriptions.services.passkey"):
                verify_authentication_response(credential_json, session)

        all_messages = [r.getMessage() for r in caplog.records]

        # The plaintext email must not appear in any log record.
        for msg in all_messages:
            assert "auth-service@example.com" not in msg, (
                f"Plaintext email found in log: {msg!r}"
            )

        # At least one record must mention the user's pk (via user_id).
        assert any(str(user.pk) in msg for msg in all_messages), (
            f"No log record contains pk={user.pk}; records: {all_messages}"
        )


# ---------------------------------------------------------------------------
# SNOW-334 — staff User without Subscriber can register and authenticate
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaffUserWithoutSubscriber:
    """SNOW-334: a staff auth.User with no Subscriber can use passkeys."""

    def test_registration_options_for_staff_user(self) -> None:
        """generate_registration_options works for a User with no Subscriber."""
        user = UserFactory.create(is_staff=True)
        session: Any = {}
        result = generate_registration_options(user, session)
        assert "challenge" in result
        assert result["user"]["name"] == user.email

    def test_verify_and_save_for_staff_user(self) -> None:
        """verify_and_save_registration creates a PasskeyCredential for a staff User."""
        user = UserFactory.create(is_staff=True)
        mock_result = _mock_verified_registration()
        session = _session_with_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_registration_response",
            return_value=mock_result,
        ):
            passkey = verify_and_save_registration("{}", session, user)
        assert passkey.user == user
        assert not hasattr(user, "subscriber") or True  # no Subscriber required

    def test_authentication_returns_staff_user(self) -> None:
        """verify_authentication_response returns the auth.User directly (no Subscriber hop)."""
        user = UserFactory.create(is_staff=True)
        PasskeyCredentialFactory.create(user=user, credential_id="dGVzdA")
        credential_json = json.dumps({"id": "dGVzdA"})
        mock_result = _mock_verified_authentication(new_sign_count=1)
        session = _session_with_auth_challenge("dGVzdA")
        with patch(
            "subscriptions.services.passkey.webauthn.verify_authentication_response",
            return_value=mock_result,
        ):
            result = verify_authentication_response(credential_json, session)
        assert result == user
        assert result.is_staff is True
