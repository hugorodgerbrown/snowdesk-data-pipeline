"""
tests/subscriptions/test_token.py — Tests for the TimestampSigner-based token service.

Covers:
  - Round-trip per salt (account-access, unsubscribe).
  - Cross-salt replay fails in both directions.
  - Expiry on account-access tokens.
  - No expiry on unsubscribe tokens.
  - Tampered and garbage tokens return None.
  - Generated tokens contain no '/' characters (safe in URL path segments).
  - Unsubscribe convenience wrappers encode/decode (email, region_id).
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from freezegun import freeze_time

from subscriptions.services.token import (
    SALT_ACCOUNT_ACCESS,
    SALT_UNSUBSCRIBE,
    generate_token,
    generate_unsubscribe_token,
    verify_token,
    verify_unsubscribe_token,
)


class TestGenerateToken:
    """Tests for generate_token."""

    def test_returns_non_empty_string(self):
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_no_forward_slash_in_token(self):
        """Tokens must be safe as URL path segments — no '/' characters."""
        for salt in (SALT_ACCOUNT_ACCESS, SALT_UNSUBSCRIBE):
            token = generate_token("alice@example.com", salt=salt)
            assert "/" not in token, f"Token for salt={salt!r} contains '/': {token!r}"

    def test_different_salts_produce_different_tokens(self):
        token_a = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        token_b = generate_token("alice@example.com", salt=SALT_UNSUBSCRIBE)
        assert token_a != token_b


class TestVerifyToken:
    """Tests for verify_token."""

    def test_round_trip_account_access(self):
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_round_trip_unsubscribe(self):
        token = generate_token("alice@example.com", salt=SALT_UNSUBSCRIBE)
        result = verify_token(token, salt=SALT_UNSUBSCRIBE, max_age=None)
        assert result == "alice@example.com"

    def test_cross_salt_replay_account_to_unsubscribe_fails(self):
        """A token signed with SALT_ACCOUNT_ACCESS cannot be verified as SALT_UNSUBSCRIBE."""
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        result = verify_token(token, salt=SALT_UNSUBSCRIBE, max_age=None)
        assert result is None

    def test_cross_salt_replay_unsubscribe_to_account_fails(self):
        """A token signed with SALT_UNSUBSCRIBE cannot be verified as SALT_ACCOUNT_ACCESS."""
        token = generate_token("alice@example.com", salt=SALT_UNSUBSCRIBE)
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None

    def test_expired_account_access_token_returns_none(self):
        with freeze_time("2026-01-01T12:00:00Z"):
            token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        future = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC) + timedelta(
            seconds=settings.ACCOUNT_TOKEN_MAX_AGE + 1
        )
        with freeze_time(future):
            result = verify_token(
                token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
            )
        assert result is None

    def test_unsubscribe_token_does_not_expire(self):
        """Unsubscribe tokens have no expiry — max_age=None."""
        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_token("alice@example.com", salt=SALT_UNSUBSCRIBE)
        # Verify five years later — should still be valid.
        with freeze_time("2025-01-01T00:00:00Z"):
            result = verify_token(token, salt=SALT_UNSUBSCRIBE, max_age=None)
        assert result == "alice@example.com"

    def test_tampered_token_returns_none(self):
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        tampered = token[:-4] + "ZZZZ"
        result = verify_token(
            tampered, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None

    def test_garbage_string_returns_none(self):
        result = verify_token(
            "not-a-token",
            salt=SALT_ACCOUNT_ACCESS,
            max_age=settings.ACCOUNT_TOKEN_MAX_AGE,
        )
        assert result is None

    def test_empty_string_returns_none(self):
        result = verify_token(
            "", salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None


class TestUnsubscribeConvenienceWrappers:
    """Tests for generate_unsubscribe_token / verify_unsubscribe_token."""

    def test_round_trip(self):
        token = generate_unsubscribe_token("alice@example.com", "CH-4115")
        result = verify_unsubscribe_token(token)
        assert result == ("alice@example.com", "CH-4115")

    def test_no_forward_slash(self):
        token = generate_unsubscribe_token("alice@example.com", "CH-4115")
        assert "/" not in token

    def test_tampered_unsubscribe_token_returns_none(self):
        token = generate_unsubscribe_token("alice@example.com", "CH-4115")
        tampered = token[:-4] + "ZZZZ"
        result = verify_unsubscribe_token(tampered)
        assert result is None

    def test_garbage_token_returns_none(self):
        result = verify_unsubscribe_token("garbage")
        assert result is None

    def test_region_id_with_dash_handled_correctly(self):
        """Region IDs contain '-' but not '|' so splitting is unambiguous."""
        token = generate_unsubscribe_token("user@example.com", "CH-4115-SUB")
        result = verify_unsubscribe_token(token)
        assert result == ("user@example.com", "CH-4115-SUB")

    def test_generate_raises_on_separator_in_email(self):
        with pytest.raises(ValueError, match="must not contain"):
            generate_unsubscribe_token("bad|email@example.com", "CH-4115")

    def test_generate_raises_on_separator_in_region_id(self):
        with pytest.raises(ValueError, match="must not contain"):
            generate_unsubscribe_token("alice@example.com", "CH|4115")

    def test_does_not_expire(self):
        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_unsubscribe_token("alice@example.com", "CH-4115")
        with freeze_time("2025-06-01T00:00:00Z"):
            result = verify_unsubscribe_token(token)
        assert result == ("alice@example.com", "CH-4115")
