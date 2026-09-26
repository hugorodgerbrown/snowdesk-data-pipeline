"""
apps/oauth/services/tokens.py — Codes, tokens, refresh rotation and bearer auth.

Lifetimes: an authorization code lives five minutes and is single-use; an
access token lives one hour; a refresh token lives thirty days and is
rotated on every use. A refresh token is issued only when the approved
scope includes ``offline_access`` — Claude requests it because the AS
metadata lists it.

Every secret is ``<prefix>`` + ``secrets.token_urlsafe(32)``. The prefix
(``sd_ac_`` / ``sd_at_`` / ``sd_rt_``) makes a leaked value recognisable in
a log or a secret scanner; only the SHA-256 hex digest is stored.

**Refresh reuse.** A rotated refresh token keeps ``replaced_by``. Presenting
it again means two parties hold the family, so the whole grant is revoked
(OAuth 2.1 §4.3.1) and both must start over.

Errors are raised as ``OAuthError`` carrying the RFC 6749 error code, which
the token endpoint serialises as ``{error, error_description}``.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from apps.oauth.models import AuthorizationCode, OAuthClient, OAuthGrant, OAuthToken
from apps.oauth.services.pkce import verify_s256
from apps.oauth.services.resource import canonical_resource, mcp_resources

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
LAST_USED_RESOLUTION = timedelta(minutes=1)

CODE_PREFIX = "sd_ac_"
ACCESS_PREFIX = "sd_at_"
REFRESH_PREFIX = "sd_rt_"

SUPPORTED_SCOPES: frozenset[str] = frozenset({"mcp", "offline_access"})
DEFAULT_SCOPE = "mcp"


class OAuthError(Exception):
    """An RFC 6749 error the token endpoint reports to the client."""

    def __init__(self, error: str, description: str, status: int = 400) -> None:
        """Store the error code, description and HTTP status.

        Args:
            error: The RFC 6749 ``error`` value.
            description: The ``error_description``.
            status: 400, or 401 for ``invalid_client``.

        """
        super().__init__(description)
        self.error = error
        self.description = description
        self.status = status


@dataclass(frozen=True)
class TokenPair:
    """What the token endpoint returns: the plaintext, once."""

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str

    def as_response(self) -> dict[str, object]:
        """Return the RFC 6749 §5.1 JSON body."""
        body: dict[str, object] = {
            "access_token": self.access_token,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "scope": self.scope,
        }
        if self.refresh_token:
            body["refresh_token"] = self.refresh_token
        return body


def hash_secret(raw: str) -> str:
    """Return the SHA-256 hex digest stored in place of a code or token.

    Args:
        raw: The plaintext.

    Returns:
        64 hex characters.

    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mint(prefix: str) -> str:
    """Return a new random secret with a readable prefix."""
    return prefix + secrets.token_urlsafe(32)


def normalise_scope(raw: str | None) -> str | None:
    """Return the requested scope in canonical order, or None if unsupported.

    An absent or empty scope is ``mcp``. ``mcp`` is always included: it is
    the only thing a token can be used for.

    Args:
        raw: The space-separated ``scope`` parameter.

    Returns:
        The normalised scope string, or None when it names an unknown scope.

    """
    requested = set((raw or "").split())
    if not requested <= SUPPORTED_SCOPES:
        return None
    requested.add(DEFAULT_SCOPE)
    return " ".join(sorted(requested))


def issue_code(
    grant: OAuthGrant,
    *,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
    scope: str,
) -> str:
    """Create an authorization code under ``grant`` and return its plaintext.

    Args:
        grant: The approval the code belongs to.
        redirect_uri: The redirect URI the authorize request named.
        code_challenge: The S256 PKCE challenge.
        resource: The MCP resource URL.
        scope: The approved scope.

    Returns:
        The code, to put on the redirect.

    """
    raw = _mint(CODE_PREFIX)
    AuthorizationCode.objects.create(
        grant=grant,
        code_hash=hash_secret(raw),
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        resource=resource,
        scope=scope,
        expires_at=timezone.now() + CODE_TTL,
    )
    return raw


def issue_token_pair(
    grant: OAuthGrant, *, resource: str, scope: str
) -> tuple[TokenPair, OAuthToken | None]:
    """Create an access token, and a refresh token when ``offline_access``.

    Args:
        grant: The approval the tokens belong to.
        resource: The audience.
        scope: The scope the tokens carry.

    Returns:
        The plaintext pair, and the refresh-token row (None without
        ``offline_access``) so a rotation can link to it.

    """
    now = timezone.now()
    access_raw = _mint(ACCESS_PREFIX)
    OAuthToken.objects.create(
        grant=grant,
        kind=OAuthToken.KIND.ACCESS,
        token_hash=hash_secret(access_raw),
        resource=resource,
        scope=scope,
        expires_at=now + ACCESS_TTL,
    )

    refresh_raw: str | None = None
    refresh_row: OAuthToken | None = None
    if "offline_access" in scope.split():
        refresh_raw = _mint(REFRESH_PREFIX)
        refresh_row = OAuthToken.objects.create(
            grant=grant,
            kind=OAuthToken.KIND.REFRESH,
            token_hash=hash_secret(refresh_raw),
            resource=resource,
            scope=scope,
            expires_at=now + REFRESH_TTL,
        )

    pair = TokenPair(
        access_token=access_raw,
        refresh_token=refresh_raw,
        expires_in=int(ACCESS_TTL.total_seconds()),
        scope=scope,
    )
    return pair, refresh_row


def _check_resource(requested: str | None, stored: str) -> None:
    """Raise ``invalid_target`` when a re-sent resource differs from the stored one.

    Args:
        requested: The ``resource`` parameter on the token request, if any.
        stored: The resource the code or token carries.

    Raises:
        OAuthError: When ``requested`` names a different resource.

    """
    if requested and canonical_resource(requested) != canonical_resource(stored):
        raise OAuthError("invalid_target", "The resource does not match the grant.")


def exchange_code(
    *,
    raw_code: str,
    client: OAuthClient,
    redirect_uri: str,
    code_verifier: str,
    resource: str | None,
) -> TokenPair:
    """Exchange an authorization code for tokens.

    Checks, in order: the code exists, is unused, is unexpired, belongs to
    ``client``, its grant is active, the redirect URI matches the authorize
    request, the PKCE verifier matches, and any re-sent resource matches.
    The code is marked used before tokens are issued, inside one
    transaction, so two concurrent exchanges cannot both succeed.

    Args:
        raw_code: The ``code`` parameter.
        client: The client presenting it.
        redirect_uri: The ``redirect_uri`` parameter.
        code_verifier: The ``code_verifier`` parameter.
        resource: The ``resource`` parameter, if sent.

    Returns:
        The new token pair.

    Raises:
        OAuthError: ``invalid_grant`` for any code check, ``invalid_target``
            for a resource mismatch.

    """
    with transaction.atomic():
        code = (
            AuthorizationCode.objects.select_for_update()
            .select_related("grant")
            .filter(code_hash=hash_secret(raw_code or ""))
            .first()
        )
        if code is None:
            raise OAuthError("invalid_grant", "Unknown authorization code.")
        if code.used_at is not None:
            raise OAuthError("invalid_grant", "The code was already used.")
        if code.is_expired:
            raise OAuthError("invalid_grant", "The authorization code has expired.")
        if code.grant.client_id != client.pk:
            raise OAuthError("invalid_grant", "The code was issued to another client.")
        if code.grant.revoked_at is not None:
            raise OAuthError("invalid_grant", "The grant has been revoked.")
        if redirect_uri != code.redirect_uri:
            raise OAuthError("invalid_grant", "redirect_uri does not match.")
        if not verify_s256(code_verifier, code.code_challenge):
            raise OAuthError("invalid_grant", "PKCE verification failed.")
        _check_resource(resource, code.resource)

        code.used_at = timezone.now()
        code.save(update_fields=["used_at", "updated_at"])
        pair, _ = issue_token_pair(code.grant, resource=code.resource, scope=code.scope)
    return pair


def _rotate(token: OAuthToken, *, resource: str | None, scope: str | None) -> TokenPair:
    """Issue a new pair for a live refresh token and retire the old one.

    Called inside ``refresh``'s transaction with the row locked.

    Args:
        token: The live, unrotated refresh token.
        resource: The ``resource`` parameter, if sent.
        scope: The ``scope`` parameter, if sent; may only narrow.

    Returns:
        The new token pair.

    Raises:
        OAuthError: ``invalid_scope`` for a wider scope, ``invalid_target``
            for a resource mismatch.

    """
    _check_resource(resource, token.resource)
    new_scope = token.scope
    if scope:
        narrowed = normalise_scope(scope)
        if narrowed is None or not set(narrowed.split()) <= set(token.scope.split()):
            raise OAuthError("invalid_scope", "The scope exceeds the grant.")
        new_scope = narrowed

    pair, new_refresh = issue_token_pair(
        token.grant, resource=token.resource, scope=new_scope
    )
    if new_refresh is not None:
        token.replaced_by = new_refresh
        token.save(update_fields=["replaced_by", "updated_at"])
    else:
        # Narrowed to no offline_access: nothing replaces it, so retire it.
        token.revoked_at = timezone.now()
        token.save(update_fields=["revoked_at", "updated_at"])
    return pair


def refresh(
    *,
    raw_refresh: str,
    client: OAuthClient,
    resource: str | None,
    scope: str | None,
) -> TokenPair:
    """Rotate a refresh token and issue a new pair.

    Reuse of a rotated token revokes the whole grant before failing, so the
    revocation is committed even though the request is refused.

    Args:
        raw_refresh: The ``refresh_token`` parameter.
        client: The client presenting it.
        resource: The ``resource`` parameter, if sent.
        scope: The ``scope`` parameter, if sent; may only narrow.

    Returns:
        The new token pair.

    Raises:
        OAuthError: ``invalid_grant`` for an unknown, reused, revoked or
            expired token or a revoked grant; ``invalid_scope`` for a wider
            scope; ``invalid_target`` for a resource mismatch.

    """
    reused_grant: OAuthGrant | None = None
    with transaction.atomic():
        token = (
            OAuthToken.objects.select_for_update()
            .refresh()
            .select_related("grant")
            .filter(token_hash=hash_secret(raw_refresh or ""))
            .first()
        )
        if token is None or token.grant.client_id != client.pk:
            raise OAuthError("invalid_grant", "Unknown refresh token.")
        if token.replaced_by_id is not None:
            reused_grant = token.grant
        elif token.revoked_at is not None or token.grant.revoked_at is not None:
            raise OAuthError("invalid_grant", "The refresh token has been revoked.")
        elif token.is_expired:
            raise OAuthError("invalid_grant", "The refresh token has expired.")
        else:
            return _rotate(token, resource=resource, scope=scope)

    # Reuse: revoke outside the transaction above so the revocation commits
    # even though this request is refused. Every other branch has returned
    # or raised by now, so ``reused_grant`` is set; the guard is for mypy.
    if reused_grant is None:
        raise OAuthError("invalid_grant", "Unknown refresh token.")
    logger.warning(
        "oauth: refresh token reuse on grant pk=%s — revoking the grant",
        reused_grant.pk,
    )
    revoke_grant(reused_grant)
    raise OAuthError("invalid_grant", "The refresh token was already used.")


def revoke_grant(grant: OAuthGrant) -> None:
    """Revoke a grant and every token under it.

    Args:
        grant: The grant to revoke.

    """
    now = timezone.now()
    with transaction.atomic():
        OAuthGrant.objects.filter(pk=grant.pk, revoked_at__isnull=True).update(
            revoked_at=now, updated_at=now
        )
        grant.tokens.filter(revoked_at__isnull=True).update(
            revoked_at=now, updated_at=now
        )
        grant.codes.filter(used_at__isnull=True).update(used_at=now, updated_at=now)
    grant.revoked_at = grant.revoked_at or now
    logger.info("oauth: revoked grant pk=%s", grant.pk)


def revoke_token(raw: str, client_id: str | None = None) -> None:
    """Revoke a token presented to the RFC 7009 revocation endpoint.

    A refresh token revokes its whole grant (RFC 7009 §2.1 — the access
    tokens from the same grant go with it). An access token is revoked on
    its own. An unknown token, or one ``client_id`` does not own, is
    ignored: the endpoint answers 200 either way.

    Args:
        raw: The ``token`` parameter.
        client_id: The ``client_id`` parameter, if sent.

    """
    token = (
        OAuthToken.objects.select_related("grant__client")
        .filter(token_hash=hash_secret(raw or ""))
        .first()
    )
    if token is None:
        return
    if client_id and token.grant.client.client_id != client_id:
        return
    if token.kind == OAuthToken.KIND.REFRESH:
        revoke_grant(token.grant)
        return
    OAuthToken.objects.filter(pk=token.pk, revoked_at__isnull=True).update(
        revoked_at=timezone.now()
    )


def authenticate_bearer(raw: str, request: "HttpRequest") -> "User | None":
    """Return the user an access token acts for, or None.

    Rejects an unknown, expired or revoked token, a token whose grant is
    revoked, a token whose audience is not this origin's MCP endpoint, and
    an inactive user. Touches ``grant.last_used_at`` at most once a minute,
    so a busy client does not write on every call.

    Args:
        raw: The token from the ``Authorization: Bearer`` header.
        request: The request, whose origin defines the expected audience.

    Returns:
        The authenticated user, or None.

    """
    if not raw:
        return None
    token = (
        OAuthToken.objects.access()
        .select_related("grant__user")
        .filter(token_hash=hash_secret(raw))
        .first()
    )
    if token is None or token.revoked_at is not None or token.is_expired:
        return None
    grant = token.grant
    if grant.revoked_at is not None or not grant.user.is_active:
        return None
    if canonical_resource(token.resource) not in mcp_resources(request):
        return None

    now = timezone.now()
    if grant.last_used_at is None or now - grant.last_used_at >= LAST_USED_RESOLUTION:
        OAuthGrant.objects.filter(pk=grant.pk).update(last_used_at=now)
    return grant.user
