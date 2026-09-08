"""
tests/accounts/test_token.py — Tests for the TimestampSigner-based token service.

Covers:
  - Round-trip per salt (account-access, email-verification).
  - Cross-salt replay fails in both directions.
  - Expiry on account-access tokens.
  - The no-expiry branch (``max_age=None``) accepts an ancient token.
  - Tampered and garbage tokens return None.
  - Generated tokens contain no '/' characters (safe in URL path segments).
"""

from datetime import UTC, datetime, timedelta

from django.conf import settings
from freezegun import freeze_time

from apps.accounts.services.token import (
    SALT_ACCOUNT_ACCESS,
    SALT_EMAIL_VERIFICATION,
    generate_token,
    verify_token,
)


class TestGenerateToken:
    """Tests for generate_token."""

    def test_returns_non_empty_string(self) -> None:
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_no_forward_slash_in_token(self) -> None:
        """Tokens must be safe as URL path segments — no '/' characters."""
        for salt in (SALT_ACCOUNT_ACCESS, SALT_EMAIL_VERIFICATION):
            token = generate_token("alice@example.com", salt=salt)
            assert "/" not in token, f"Token for salt={salt!r} contains '/': {token!r}"

    def test_different_salts_produce_different_tokens(self) -> None:
        token_a = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        token_b = generate_token("alice@example.com", salt=SALT_EMAIL_VERIFICATION)
        assert token_a != token_b


class TestVerifyToken:
    """Tests for verify_token."""

    def test_round_trip_account_access(self) -> None:
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result == "alice@example.com"

    def test_round_trip_email_verification(self) -> None:
        token = generate_token("alice@example.com", salt=SALT_EMAIL_VERIFICATION)
        result = verify_token(token, salt=SALT_EMAIL_VERIFICATION, max_age=None)
        assert result == "alice@example.com"

    def test_cross_salt_replay_account_to_verification_fails(self) -> None:
        """A SALT_ACCOUNT_ACCESS token cannot be verified as SALT_EMAIL_VERIFICATION."""
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        result = verify_token(token, salt=SALT_EMAIL_VERIFICATION, max_age=None)
        assert result is None

    def test_cross_salt_replay_verification_to_account_fails(self) -> None:
        """A SALT_EMAIL_VERIFICATION token cannot be verified as SALT_ACCOUNT_ACCESS."""
        token = generate_token("alice@example.com", salt=SALT_EMAIL_VERIFICATION)
        result = verify_token(
            token, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None

    def test_expired_account_access_token_returns_none(self) -> None:
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

    def test_max_age_none_accepts_an_ancient_token(self) -> None:
        """``max_age=None`` disables the expiry check entirely."""
        with freeze_time("2020-01-01T00:00:00Z"):
            token = generate_token("alice@example.com", salt=SALT_EMAIL_VERIFICATION)
        # Verify five years later — should still be valid.
        with freeze_time("2025-01-01T00:00:00Z"):
            result = verify_token(token, salt=SALT_EMAIL_VERIFICATION, max_age=None)
        assert result == "alice@example.com"

    def test_tampered_token_returns_none(self) -> None:
        token = generate_token("alice@example.com", salt=SALT_ACCOUNT_ACCESS)
        tampered = token[:-4] + "ZZZZ"
        result = verify_token(
            tampered, salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None

    def test_garbage_string_returns_none(self) -> None:
        result = verify_token(
            "not-a-token",
            salt=SALT_ACCOUNT_ACCESS,
            max_age=settings.ACCOUNT_TOKEN_MAX_AGE,
        )
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = verify_token(
            "", salt=SALT_ACCOUNT_ACCESS, max_age=settings.ACCOUNT_TOKEN_MAX_AGE
        )
        assert result is None
