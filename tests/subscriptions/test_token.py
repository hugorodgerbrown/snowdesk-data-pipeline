"""
tests/subscriptions/test_token.py — Tests for magic-link token service.

Covers token generation, validation, expiry handling, and tamper detection.
Uses freezegun to control the clock.
"""

from datetime import UTC, datetime, timedelta

import jwt
from django.conf import settings
from freezegun import freeze_time

from subscriptions.services.token import (
    generate_magic_link_token,
    validate_magic_link_token,
)


class TestGenerateMagicLinkToken:
    """Tests for generate_magic_link_token."""

    def test_returns_string(self):
        token = generate_magic_link_token("alice@example.com")
        assert isinstance(token, str)
        assert len(token) > 0

    def test_payload_contains_email(self):
        token = generate_magic_link_token("alice@example.com")
        payload = jwt.decode(
            token, settings.MAGIC_LINK_SECRET_KEY, algorithms=["HS256"]
        )
        assert payload["email"] == "alice@example.com"

    def test_payload_contains_purpose(self):
        token = generate_magic_link_token("alice@example.com", purpose="subscribe")
        payload = jwt.decode(
            token, settings.MAGIC_LINK_SECRET_KEY, algorithms=["HS256"]
        )
        assert payload["purpose"] == "subscribe"

    def test_default_purpose_is_login(self):
        token = generate_magic_link_token("alice@example.com")
        payload = jwt.decode(
            token, settings.MAGIC_LINK_SECRET_KEY, algorithms=["HS256"]
        )
        assert payload["purpose"] == "login"

    def test_payload_has_iat_and_exp(self):
        with freeze_time("2026-01-01T12:00:00Z"):
            token = generate_magic_link_token("alice@example.com")
            payload = jwt.decode(
                token, settings.MAGIC_LINK_SECRET_KEY, algorithms=["HS256"]
            )
        assert "iat" in payload
        assert "exp" in payload

    def test_exp_is_iat_plus_expiry_seconds(self):
        with freeze_time("2026-01-01T12:00:00Z"):
            token = generate_magic_link_token("alice@example.com")
            payload = jwt.decode(
                token, settings.MAGIC_LINK_SECRET_KEY, algorithms=["HS256"]
            )
        expected_delta = settings.MAGIC_LINK_EXPIRY_SECONDS
        actual_delta = payload["exp"] - payload["iat"]
        assert actual_delta == expected_delta


class TestValidateMagicLinkToken:
    """Tests for validate_magic_link_token."""

    def test_valid_token_returns_payload(self):
        token = generate_magic_link_token("alice@example.com")
        payload = validate_magic_link_token(token)
        assert payload is not None
        assert payload["email"] == "alice@example.com"

    def test_expired_token_returns_none(self):
        with freeze_time("2026-01-01T12:00:00Z"):
            token = generate_magic_link_token("alice@example.com")
        # Advance time beyond expiry
        future = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC) + timedelta(
            seconds=settings.MAGIC_LINK_EXPIRY_SECONDS + 1
        )
        with freeze_time(future):
            result = validate_magic_link_token(token)
        assert result is None

    def test_tampered_token_returns_none(self):
        token = generate_magic_link_token("alice@example.com")
        # Flip the FIRST char of the signature, not the last.  A 32-byte
        # HS256 signature encodes to 43 base64url chars where the final
        # char carries only 4 signature bits + 2 unused padding bits, so
        # a one-char flip there can decode to the same signature byte
        # (e.g. "A"→"B": both have top-4-bits 0000).  The first char's 6
        # bits all map into signature byte 0, so any flip there is
        # guaranteed to change the decoded signature.
        head, payload, sig = token.split(".")
        flipped_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
        tampered = ".".join((head, payload, flipped_sig))
        result = validate_magic_link_token(tampered)
        assert result is None

    def test_garbage_string_returns_none(self):
        result = validate_magic_link_token("not.a.token")
        assert result is None

    def test_empty_string_returns_none(self):
        result = validate_magic_link_token("")
        assert result is None

    def test_valid_token_purpose_preserved(self):
        token = generate_magic_link_token("alice@example.com", purpose="subscribe")
        payload = validate_magic_link_token(token)
        assert payload is not None
        assert payload["purpose"] == "subscribe"
