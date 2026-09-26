"""
apps/oauth/services/pkce.py — PKCE (RFC 7636) verification, S256 only.

Every client Snowdesk serves is public, so PKCE is what binds an
authorization code to the client that started the flow: the authorize
request carries ``code_challenge = BASE64URL(SHA256(code_verifier))`` and
the token request carries the verifier. ``plain`` is not accepted — OAuth
2.1 and the MCP authorization spec both require S256, and the AS metadata
advertises only that.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re

logger = logging.getLogger(__name__)

# RFC 7636 §4.1: 43–128 characters from the unreserved set.
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")

# A challenge is a base64url SHA-256 digest without padding: always 43 chars.
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9\-_]{43}$")


def s256_challenge(verifier: str) -> str:
    """Return the S256 code challenge for ``verifier``.

    Args:
        verifier: The PKCE code verifier.

    Returns:
        ``BASE64URL(SHA256(verifier))`` without padding.

    """
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_valid_challenge(challenge: str) -> bool:
    """Return True when ``challenge`` has the shape of an S256 challenge.

    Args:
        challenge: The ``code_challenge`` from an authorize request.

    Returns:
        True for a 43-character base64url string.

    """
    return bool(_CHALLENGE_RE.match(challenge or ""))


def verify_s256(verifier: str, challenge: str) -> bool:
    """Return True when ``verifier`` hashes to ``challenge``.

    The comparison is constant-time (``hmac.compare_digest``).

    Args:
        verifier: The ``code_verifier`` from the token request.
        challenge: The ``code_challenge`` stored with the code.

    Returns:
        True only for a well-formed verifier whose S256 digest matches.

    """
    if not verifier or not _VERIFIER_RE.match(verifier):
        return False
    return hmac.compare_digest(s256_challenge(verifier), challenge or "")
