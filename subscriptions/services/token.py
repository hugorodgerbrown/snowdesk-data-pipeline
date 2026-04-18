# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib token module at runtime.
"""
subscriptions/services/token.py — Account-access token generation and validation.

Provides pure functions for creating and verifying signed tokens used in the
account-access and unsubscribe flows.  Tokens are produced by Django's
built-in ``TimestampSigner`` so they do not require a separate secret
— they are derived from ``settings.SECRET_KEY`` and an additional salt.

Three salts are defined:
  - ``SALT_ACCOUNT_ACCESS`` — short-lived tokens for account-access email links.
  - ``SALT_UNSUBSCRIBE`` — permanent tokens embedded in bulletin emails; these
    never expire so a subscriber can always opt out even months later.

Public API
----------
``generate_token(value, *, salt)``
    Sign ``value`` and return a URL-safe token string.

``verify_token(token, *, salt, max_age)``
    Verify ``token`` against ``salt``.  Returns the original ``value`` string
    on success, or ``None`` on failure (bad signature, tampered, or expired).

``generate_unsubscribe_token(email, region_id)``
    Convenience wrapper that encodes ``{email}|{region_id}`` and signs with
    ``SALT_UNSUBSCRIBE``.

``verify_unsubscribe_token(token)``
    Convenience wrapper that verifies and splits an unsubscribe token.
    Returns ``(email, region_id)`` on success or ``None`` on failure.
"""

from __future__ import annotations

import logging

from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

logger = logging.getLogger(__name__)

# Salt values — changing a salt invalidates all tokens produced with the old
# salt, which is intentional: bump the salt to rotate all outstanding tokens.
SALT_ACCOUNT_ACCESS = "account-access"
SALT_UNSUBSCRIBE = "unsubscribe"

# Separator used inside unsubscribe token values.
_UNSUB_SEP = "|"


def generate_token(value: str, *, salt: str) -> str:
    """
    Sign ``value`` and return a URL-safe token string.

    The token encodes ``value``, a timestamp, and an HMAC derived from
    ``settings.SECRET_KEY`` + ``salt``.  Safe as a URL path segment after
    standard percent-decoding.  Not guaranteed safe as a query-string value
    without encoding (``TimestampSigner`` uses ``:`` separators).

    Args:
        value: The plain-text string to sign (e.g. an email address).
        salt: A non-empty string that scopes the signature; tokens signed
            with one salt cannot be verified with another.

    Returns:
        A signed, URL-safe token string.

    """
    signer = TimestampSigner(salt=salt)
    token = signer.sign(value)
    logger.debug("Generated token (salt=%s)", salt)
    return token


def verify_token(token: str, *, salt: str, max_age: int | None) -> str | None:
    """
    Verify a token and return the embedded value, or ``None`` on failure.

    Swallows ``BadSignature`` and ``SignatureExpired`` so callers can treat
    all failure modes identically (render the link-expired page).

    Args:
        token: The token string to verify.
        salt: Must match the salt used to generate the token.
        max_age: Maximum age of the token in seconds.  Pass ``None`` to
            accept tokens regardless of age (unsubscribe flow).

    Returns:
        The original plain-text value embedded in the token, or ``None``.

    """
    signer = TimestampSigner(salt=salt)
    try:
        # TimestampSigner.unsign accepts max_age=None to mean "no expiry"
        value: str = signer.unsign(token, max_age=max_age)
        return value
    except SignatureExpired:
        logger.debug("Token has expired (salt=%s)", salt)
        return None
    except BadSignature:
        logger.debug("Token has a bad signature (salt=%s)", salt)
        return None


# ---------------------------------------------------------------------------
# Unsubscribe convenience wrappers
# ---------------------------------------------------------------------------


def generate_unsubscribe_token(email: str, region_id: str) -> str:
    """
    Create a permanent unsubscribe token encoding both email and region_id.

    The two values are joined with ``|`` before signing, which is safe
    because neither email addresses nor SLF region IDs contain that character.

    Args:
        email: The subscriber's email address.
        region_id: The SLF region identifier (e.g. ``"CH-4115"``).

    Returns:
        A signed, URL-safe token string.

    """
    if _UNSUB_SEP in email or _UNSUB_SEP in region_id:
        raise ValueError(
            f"email and region_id must not contain '{_UNSUB_SEP}'; "
            f"got email={email!r}, region_id={region_id!r}"
        )
    value = f"{email}{_UNSUB_SEP}{region_id}"
    return generate_token(value, salt=SALT_UNSUBSCRIBE)


def verify_unsubscribe_token(token: str) -> tuple[str, str] | None:
    """
    Verify an unsubscribe token and return ``(email, region_id)``, or ``None``.

    Unsubscribe tokens never expire (``max_age=None``) so a subscriber can
    always opt out of a region using a link embedded in a historical email.

    Args:
        token: The unsubscribe token to verify.

    Returns:
        A ``(email, region_id)`` tuple on success, or ``None`` on failure.

    """
    raw = verify_token(token, salt=SALT_UNSUBSCRIBE, max_age=None)
    if raw is None:
        return None
    parts = raw.split(_UNSUB_SEP, 1)
    if len(parts) != 2:
        logger.warning("Unsubscribe token value has unexpected format: %r", raw)
        return None
    return parts[0], parts[1]
