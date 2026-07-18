# ruff: noqa: A005 — filename is mandated by the architect's design spec; the
# module lives inside the accounts/services/ package so it does not shadow
# the stdlib token module at runtime.
"""
accounts/services/token.py — Account-access token generation and validation.

Provides pure functions for creating and verifying signed tokens used in the
account-access and unsubscribe flows.  Tokens are produced by Django's
built-in ``TimestampSigner`` so they do not require a separate secret
— they are derived from ``settings.SECRET_KEY`` and an additional salt.

The salts are:
  - ``SALT_ACCOUNT_ACCESS`` — short-lived tokens for account-access email links.
  - ``SALT_UNSUBSCRIBE`` — permanent tokens embedded in bulletin emails; these
    never expire so a subscriber can always opt out even months later.
  - ``SALT_EMAIL_VERIFICATION`` — short-lived tokens for the registration
    email-verification links (SNOW-430).
  - ``SALT_PASSWORD_RESET`` — short-lived, single-use password-reset tokens
    that embed a fingerprint of the current password hash so they
    auto-invalidate once the password changes (SNOW-432).
  - ``SALT_EMAIL_CHANGE`` — short-lived tokens binding a user to a specific
    pending new email address (SNOW-433).

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
from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils.crypto import constant_time_compare, salted_hmac

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# Salt values — changing a salt invalidates all tokens produced with the old
# salt, which is intentional: bump the salt to rotate all outstanding tokens.
SALT_ACCOUNT_ACCESS = "account-access"
SALT_UNSUBSCRIBE = "unsubscribe"
SALT_EMAIL_VERIFICATION = "email-verification"
SALT_PASSWORD_RESET = "password-reset"  # noqa: S105 — salt label, not a password
SALT_EMAIL_CHANGE = "email-change"

# Separator used inside composite token values (email|payload).
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
    value = f"{email.lower()}{_UNSUB_SEP}{region_id}"
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
        logger.warning(
            "Unsubscribe token value has unexpected format: parts=%d len=%d",
            len(parts),
            len(raw),
        )
        return None
    email = parts[0].lower()
    region_id = parts[1]
    return email, region_id


# ---------------------------------------------------------------------------
# Password-reset tokens (SNOW-432)
# ---------------------------------------------------------------------------


def _reset_fingerprint(user: User) -> str:
    """Return a short HMAC binding the token to the user's current credentials.

    Includes the current password hash and last-login timestamp so the token
    **auto-invalidates the moment the password changes** (single-use, option
    (a)) — no persisted used-token record is needed.  Mirrors Django's own
    ``PasswordResetTokenGenerator`` hash value.

    Args:
        user: The account the reset targets.

    Returns:
        A truncated hex HMAC digest.

    """
    login_ts = "" if user.last_login is None else user.last_login.isoformat()
    value = f"{user.password}{login_ts}"
    return salted_hmac(f"{SALT_PASSWORD_RESET}.fingerprint", value).hexdigest()[:20]


def generate_password_reset_token(user: User) -> str:
    """Create a single-use password-reset token for ``user``.

    The signed value is ``{email}|{fingerprint}``; the fingerprint binds the
    token to the user's current password hash so it stops verifying once the
    password is changed.

    Signs inline with ``TimestampSigner`` rather than delegating to
    ``generate_token`` so the password-derived payload never transits the
    shared, debug-logging token helpers.

    Args:
        user: The account requesting a reset.

    Returns:
        A signed, URL-safe token string.

    """
    payload = f"{user.get_username().lower()}{_UNSUB_SEP}{_reset_fingerprint(user)}"
    return TimestampSigner(salt=SALT_PASSWORD_RESET).sign(payload)


def verify_password_reset_token(token: str, *, max_age: int | None) -> User | None:
    """Verify a password-reset token and return the target user, or ``None``.

    Returns ``None`` on a bad/expired signature, an unknown email, or a
    fingerprint mismatch (the password has changed since the token was minted
    — i.e. the link has already been used).  Unsigns inline (no shared logging
    helper) so the password-derived payload is never passed to a logger.

    Args:
        token: The token from the reset-confirm URL.
        max_age: Maximum token age in seconds.

    Returns:
        The target ``auth.User`` on success, or ``None``.

    """
    signer = TimestampSigner(salt=SALT_PASSWORD_RESET)
    try:
        raw = signer.unsign(token, max_age=max_age)
    except BadSignature, SignatureExpired:
        return None
    parts = raw.split(_UNSUB_SEP, 1)
    if len(parts) != 2:
        return None
    email, fingerprint = parts[0].lower(), parts[1]
    user_model = get_user_model()
    try:
        user = user_model.objects.get(username=email)
    except user_model.DoesNotExist:
        return None
    if not constant_time_compare(fingerprint, _reset_fingerprint(user)):
        return None
    return user


# ---------------------------------------------------------------------------
# Email-change tokens (SNOW-433)
# ---------------------------------------------------------------------------


def generate_email_change_token(user: User, new_email: str) -> str:
    """Create a token binding ``user`` to a specific pending ``new_email``.

    The signed value is ``{user_pk}|{new_email}``; binding both means the token
    cannot be replayed against a different user or a different address.  Signs
    inline with ``TimestampSigner`` (no shared logging helper).

    Args:
        user: The account requesting the change.
        new_email: The requested new address.

    Returns:
        A signed, URL-safe token string.

    """
    payload = f"{user.pk}{_UNSUB_SEP}{new_email.strip().lower()}"
    return TimestampSigner(salt=SALT_EMAIL_CHANGE).sign(payload)


def verify_email_change_token(
    token: str, *, max_age: int | None
) -> tuple[User, str] | None:
    """Verify an email-change token and return ``(user, new_email)``, or ``None``.

    Returns ``None`` on a bad/expired signature or an unknown user pk.  The
    caller must still confirm the account's ``pending_email`` matches the
    returned address (latest-request-wins / single-use) and that the address
    is still free.

    Args:
        token: The token from the confirm URL.
        max_age: Maximum token age in seconds.

    Returns:
        ``(user, new_email)`` on success, or ``None``.

    """
    signer = TimestampSigner(salt=SALT_EMAIL_CHANGE)
    try:
        raw = signer.unsign(token, max_age=max_age)
    except BadSignature, SignatureExpired:
        return None
    parts = raw.split(_UNSUB_SEP, 1)
    if len(parts) != 2:
        return None
    user_pk, new_email = parts[0], parts[1].lower()
    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_pk)
    except user_model.DoesNotExist, ValueError:
        return None
    return user, new_email
