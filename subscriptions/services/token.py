# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the subscriptions/services/ package so it does not shadow
# the stdlib token module at runtime.
"""
subscriptions/services/token.py — Magic-link JWT generation and validation.

Provides pure functions for creating and verifying signed JWTs used in the
magic-link authentication flow. Tokens are short-lived and signed with
settings.MAGIC_LINK_SECRET_KEY using HS256.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import jwt
from django.conf import settings

logger = logging.getLogger(__name__)

# Algorithm used to sign magic-link JWTs.
_JWT_ALGORITHM = "HS256"


def generate_magic_link_token(email: str, purpose: str = "login") -> str:
    """
    Create a signed JWT containing the subscriber's email and an expiry.

    Args:
        email: The subscriber's email address to embed in the token.
        purpose: Describes the intended use of the token (e.g. "login").

    Returns:
        A signed JWT string.

    """
    now = datetime.now(tz=UTC)
    expiry = now + timedelta(seconds=settings.MAGIC_LINK_EXPIRY_SECONDS)
    payload = {
        "email": email,
        "purpose": purpose,
        "iat": now,
        "exp": expiry,
    }
    token = jwt.encode(
        payload, settings.MAGIC_LINK_SECRET_KEY, algorithm=_JWT_ALGORITHM
    )
    logger.debug("Generated magic-link token for %s (purpose=%s)", email, purpose)
    return token


def validate_magic_link_token(token: str) -> dict[str, str] | None:
    """
    Decode and validate a magic-link JWT.

    Verifies the signature and expiry. Returns the decoded payload dict on
    success, or None if the token is expired or otherwise invalid.

    Args:
        token: The JWT string to validate.

    Returns:
        The decoded payload dict, or None on failure.

    """
    try:
        payload: dict[str, str] = jwt.decode(
            token,
            settings.MAGIC_LINK_SECRET_KEY,
            algorithms=[_JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Magic-link token has expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.debug("Magic-link token is invalid: %s", exc)
        return None
