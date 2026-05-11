"""
subscriptions/services/passkey.py — WebAuthn / passkey service functions.

Wraps the py_webauthn library to provide four high-level operations:

  generate_registration_options   Build creation options for credentials.create().
  verify_and_save_registration    Verify the response and persist PasskeyCredential.
  generate_authentication_options Build request options for credentials.get().
  verify_authentication_response  Verify the response and return the Subscriber.

Challenges are stored in the Django session between the "generate" and
"verify" steps; each session key is cleared after use to prevent replay.

The RP_ID, RP_NAME, and ORIGIN are read from Django settings (populated via
python-decouple).  See WEBAUTHN_RP_ID / WEBAUTHN_RP_NAME / WEBAUTHN_ORIGIN.
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid
from typing import Any, cast

import webauthn
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.utils import timezone
from webauthn.authentication.verify_authentication_response import (
    VerifiedAuthentication,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from subscriptions.models import PasskeyCredential, Subscriber

logger = logging.getLogger(__name__)

# Session keys used to persist challenges between the request/response round-trip.
_SESSION_REG_CHALLENGE = "webauthn_reg_challenge"
_SESSION_AUTH_CHALLENGE = "webauthn_auth_challenge"


class PasskeyError(Exception):
    """Raised when a passkey registration or authentication operation fails."""


class PasskeyUnknownCredentialError(PasskeyError):
    """Raised when the presented credential ID is not found in the database."""

    def __init__(self, credential_id: str) -> None:
        """Initialise with the unknown credential ID for Signal API use."""
        self.credential_id = credential_id
        super().__init__("Unknown credential.")


def _rp_id() -> str:
    """Return the configured WebAuthn RP ID."""
    return str(settings.WEBAUTHN_RP_ID)


def _rp_name() -> str:
    """Return the configured WebAuthn RP name."""
    return str(settings.WEBAUTHN_RP_NAME)


def _origin() -> str:
    """Return the configured WebAuthn expected origin."""
    return str(settings.WEBAUTHN_ORIGIN)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def generate_registration_options(
    subscriber: Subscriber, session: SessionBase
) -> dict[str, Any]:
    """
    Generate WebAuthn credential creation options for the given subscriber.

    Populates ``exclude_credentials`` from the subscriber's existing passkeys
    so the browser can prevent duplicate registrations on the same device.
    Stores the challenge in the session for later verification.

    Args:
        subscriber: The authenticated subscriber requesting registration.
        session: The Django session store to persist the challenge.

    Returns:
        JSON-serialisable dict of PublicKeyCredentialCreationOptions.

    """
    existing = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(pk.credential_id))
        for pk in subscriber.passkeys.all()
    ]

    options = webauthn.generate_registration_options(
        rp_id=_rp_id(),
        rp_name=_rp_name(),
        user_id=str(subscriber.uuid).encode(),
        user_name=subscriber.email,
        user_display_name=subscriber.email,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=existing,
    )

    # Persist the challenge so verify_and_save_registration can retrieve it.
    session[_SESSION_REG_CHALLENGE] = bytes_to_base64url(options.challenge)

    return cast(dict[str, Any], json.loads(options_to_json(options)))


def verify_and_save_registration(
    credential_json: str,
    session: SessionBase,
    subscriber: Subscriber,
) -> PasskeyCredential:
    """
    Verify a WebAuthn registration response and persist the new PasskeyCredential.

    Retrieves the challenge from the session, verifies the browser response via
    the py_webauthn library, then creates and saves a PasskeyCredential row.

    Args:
        credential_json: The JSON string returned by navigator.credentials.create().
        session: The Django session store holding the challenge.
        subscriber: The authenticated subscriber completing registration.

    Returns:
        The newly created PasskeyCredential.

    Raises:
        PasskeyError: If the challenge is missing, verification fails, or the
            credential ID is already registered to another subscriber.

    """
    encoded_challenge = session.get(_SESSION_REG_CHALLENGE)
    if not encoded_challenge:
        raise PasskeyError("Registration challenge missing from session.")

    try:
        result: VerifiedRegistration = webauthn.verify_registration_response(
            credential=credential_json,
            expected_challenge=base64url_to_bytes(encoded_challenge),
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
        )
    except Exception as exc:
        logger.warning("Passkey registration verification failed: %s", exc)
        raise PasskeyError("Registration verification failed.") from exc
    finally:
        # Always clear the challenge — prevent replay regardless of outcome.
        session.pop(_SESSION_REG_CHALLENGE, None)

    credential_id = bytes_to_base64url(result.credential_id)

    # Reject if this credential is already registered (safety net — the browser
    # should have caught it via excludeCredentials, but check server-side too).
    if PasskeyCredential.objects.filter(credential_id=credential_id).exists():
        raise PasskeyError("Credential is already registered.")

    aaguid = _parse_aaguid(result.aaguid)
    device_type = (
        result.credential_device_type.value
    )  # "single_device" | "multi_device"
    name = _auto_name(result.credential_backed_up)

    passkey = PasskeyCredential.objects.create(
        subscriber=subscriber,
        credential_id=credential_id,
        public_key=result.credential_public_key,
        sign_count=result.sign_count,
        aaguid=aaguid,
        name=name,
        device_type=device_type,
        backed_up=result.credential_backed_up,
    )
    logger.info(
        "Passkey registered for subscriber %s (device_type=%s)",
        subscriber.email,
        device_type,
    )
    return passkey


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def generate_authentication_options(
    session: SessionBase,
    *,
    subscriber: Subscriber | None = None,
) -> dict[str, Any]:
    """
    Generate WebAuthn credential request options.

    When ``subscriber`` is provided the options include their specific
    credential IDs (targeted authentication).  When omitted the options use
    an empty ``allow_credentials`` list, enabling conditional UI / passkey
    autofill — the browser presents all applicable passkeys to the user.

    Args:
        session: The Django session store to persist the challenge.
        subscriber: Optional subscriber for targeted authentication.

    Returns:
        JSON-serialisable dict of PublicKeyCredentialRequestOptions.

    """
    allow_credentials: list[PublicKeyCredentialDescriptor] | None = None
    if subscriber is not None:
        allow_credentials = [
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(pk.credential_id))
            for pk in subscriber.passkeys.all()
        ]

    options = webauthn.generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.PREFERRED,
    )

    session[_SESSION_AUTH_CHALLENGE] = bytes_to_base64url(options.challenge)

    return cast(dict[str, Any], json.loads(options_to_json(options)))


def verify_authentication_response(
    credential_json: str,
    session: SessionBase,
) -> Subscriber:
    """
    Verify a WebAuthn authentication response and return the authenticated Subscriber.

    Looks up the PasskeyCredential by credential ID from the response JSON,
    calls the py_webauthn verifier, updates ``sign_count`` and ``last_used_at``,
    and returns the associated Subscriber.

    Args:
        credential_json: The JSON string returned by navigator.credentials.get().
        session: The Django session store holding the challenge.

    Returns:
        The authenticated Subscriber.

    Raises:
        PasskeyError: If the challenge is missing, credential is unknown,
            or verification fails.

    """
    encoded_challenge = session.get(_SESSION_AUTH_CHALLENGE)
    if not encoded_challenge:
        raise PasskeyError("Authentication challenge missing from session.")

    # Extract the credential ID from the JSON to look up the stored key.
    try:
        raw_id = json.loads(credential_json).get("id", "")
    except (json.JSONDecodeError, TypeError) as exc:
        session.pop(_SESSION_AUTH_CHALLENGE, None)
        raise PasskeyError("Malformed credential JSON.") from exc

    try:
        passkey = PasskeyCredential.objects.select_related("subscriber").get(
            credential_id=raw_id
        )
    except PasskeyCredential.DoesNotExist:
        session.pop(_SESSION_AUTH_CHALLENGE, None)
        raise PasskeyUnknownCredentialError(raw_id)

    try:
        result: VerifiedAuthentication = webauthn.verify_authentication_response(
            credential=credential_json,
            expected_challenge=base64url_to_bytes(encoded_challenge),
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
        )
    except Exception as exc:
        logger.warning(
            "Passkey authentication verification failed for credential %s: %s",
            raw_id,
            exc,
        )
        raise PasskeyError("Authentication verification failed.") from exc
    finally:
        session.pop(_SESSION_AUTH_CHALLENGE, None)

    passkey.sign_count = result.new_sign_count
    passkey.backed_up = result.credential_backed_up
    passkey.last_used_at = timezone.now()
    passkey.save(
        update_fields=["sign_count", "backed_up", "last_used_at", "updated_at"]
    )

    logger.info(
        "Passkey authentication successful for subscriber %s",
        passkey.subscriber.email,
    )
    return passkey.subscriber


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_aaguid(aaguid_str: str) -> _uuid.UUID | None:
    """Parse an AAGUID string into a UUID, or None for the all-zeros placeholder.

    Returns None when the AAGUID is the all-zeros value that indicates no real
    AAGUID was provided by the authenticator.

    Args:
        aaguid_str: AAGUID string (e.g. "adce0002-35bc-c60a-648b-0b25f1f05503").

    Returns:
        UUID instance, or None if the AAGUID is all zeros.

    """
    try:
        parsed = _uuid.UUID(aaguid_str)
    except (ValueError, AttributeError):
        return None
    return None if parsed.int == 0 else parsed


def _auto_name(backed_up: bool) -> str:
    """
    Generate a human-readable default name for a newly registered passkey.

    Args:
        backed_up: True when the passkey is synced to the cloud.

    Returns:
        A short display name string.

    """
    from django.utils.timezone import now

    date_str = now().strftime("%-d %b %Y")
    kind = "Synced passkey" if backed_up else "Device passkey"
    return f"{kind} — {date_str}"
