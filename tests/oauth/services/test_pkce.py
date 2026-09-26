"""
tests/oauth/services/test_pkce.py — Tests for apps.oauth.services.pkce.

Uses the RFC 7636 Appendix B test vector, then covers each way a verifier
or challenge is refused.
"""

from __future__ import annotations

from apps.oauth.services.pkce import is_valid_challenge, s256_challenge, verify_s256

# RFC 7636 Appendix B.
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_s256_challenge_matches_rfc_vector() -> None:
    """The challenge for the RFC verifier is the RFC challenge."""
    assert s256_challenge(RFC_VERIFIER) == RFC_CHALLENGE


def test_verify_accepts_matching_verifier() -> None:
    """A verifier that hashes to the challenge is accepted."""
    assert verify_s256(RFC_VERIFIER, RFC_CHALLENGE)


def test_verify_rejects_wrong_verifier() -> None:
    """A different well-formed verifier is refused."""
    assert not verify_s256("a" * 43, RFC_CHALLENGE)


def test_verify_rejects_malformed_verifier() -> None:
    """Too short, too long, bad characters and empty are all refused."""
    assert not verify_s256("short", RFC_CHALLENGE)
    assert not verify_s256("a" * 129, RFC_CHALLENGE)
    assert not verify_s256("a" * 42 + "!", RFC_CHALLENGE)
    assert not verify_s256("", RFC_CHALLENGE)


def test_plain_method_is_not_accepted() -> None:
    """A 'plain' challenge (the verifier itself) does not verify."""
    assert not verify_s256(RFC_VERIFIER, RFC_VERIFIER)


def test_challenge_shape() -> None:
    """Only a 43-character base64url string is a valid challenge."""
    assert is_valid_challenge(RFC_CHALLENGE)
    assert not is_valid_challenge(RFC_CHALLENGE + "=")
    assert not is_valid_challenge("")
