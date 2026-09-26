"""
apps/oauth/models.py — Database models for the oauth application.

Snowdesk is its own OAuth 2.1 authorization server for the MCP endpoint
(SNOW-1035). Four tables carry that:

* ``OAuthClient`` — a client that may ask for access: one registered through
  Dynamic Client Registration (``DCR``), one identified by the URL of its
  Client ID Metadata Document (``CIMD``), or the ``LOCAL`` client
  ``mint_mcp_token`` issues tokens under. Public clients only — none of them
  holds a secret, and PKCE is what binds a code to the client that asked.
* ``OAuthGrant`` — one user's approval of one client. It is the "connected
  app" row ``/account/settings/`` lists, and the token family: revoking it
  kills every code and token under it, and a refresh token presented twice
  revokes it.
* ``AuthorizationCode`` — the single-use, five-minute code the consent page
  hands back to the client.
* ``OAuthToken`` — access and refresh tokens.

**No secret is stored in the clear.** A code or token is minted as
``<prefix>`` + ``secrets.token_urlsafe(32)``, returned to the client once,
and only its SHA-256 hex digest is written here. A database read gives an
attacker nothing to present. See ``apps/oauth/services/tokens.py``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OAuthClient
# ---------------------------------------------------------------------------


class OAuthClientQuerySet(models.QuerySet["OAuthClient"]):
    """Custom queryset for OAuthClient."""

    def of_kind(self, kind: str) -> "OAuthClientQuerySet":
        """Return the clients of one kind.

        Args:
            kind: An ``OAuthClient.KIND`` value.

        Returns:
            Filtered queryset.

        """
        return self.filter(kind=kind)


class OAuthClient(BaseModel):
    """A public OAuth client that may ask a user for access to the MCP server.

    ``client_id`` is the identifier the client presents. For a DCR client it
    is an opaque random string minted at registration; for a CIMD client it
    is the HTTPS URL of the client's metadata document, and the row is a
    cache of that document refreshed after 24 hours
    (``apps.oauth.services.clients.resolve_client``).

    ``redirect_uris`` is the allowlist the authorize endpoint matches
    against. A loopback entry (``http://localhost`` / ``http://127.0.0.1``)
    matches on any port — see ``apps.oauth.services.redirects``.
    """

    class KIND(models.TextChoices):
        """How the client came to be known to Snowdesk."""

        DCR = "DCR", "Dynamic registration"
        CIMD = "CIMD", "Client ID metadata document"
        LOCAL = "LOCAL", "Local (mint_mcp_token)"

    client_id = models.CharField(
        max_length=500,
        unique=True,
        help_text=(
            "The identifier the client presents: a random string for a "
            "DCR client, the metadata document URL for a CIMD client."
        ),
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND.choices,
        help_text="How the client registered.",
    )
    client_name = models.CharField(
        max_length=100,
        blank=True,
        help_text="The name the client gave itself, shown on the consent page.",
    )
    redirect_uris = models.JSONField(
        default=list,
        blank=True,
        help_text="The redirect URIs the authorize endpoint accepts.",
    )
    metadata_fetched_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a CIMD client's metadata document was last fetched.",
    )

    objects = OAuthClientQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        verbose_name = "OAuth client"

    @property
    def redirect_host(self) -> str:
        """Return the hostname of the first redirect URI, or ``""``.

        The consent page and the settings page both name where a code goes,
        because that — not the name a client gives itself — is the fact a
        user can check.
        """
        if not self.redirect_uris:
            return ""
        return urlsplit(str(self.redirect_uris[0])).hostname or ""

    @property
    def display_name(self) -> str:
        """Return the client's name, falling back to its redirect host."""
        return self.client_name or self.redirect_host or self.client_id

    def to_string(self) -> str:
        """Return ``"{name} ({kind})"``."""
        return f"{self.display_name} ({self.get_kind_display()})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# OAuthGrant
# ---------------------------------------------------------------------------


class OAuthGrantQuerySet(models.QuerySet["OAuthGrant"]):
    """Custom queryset for OAuthGrant."""

    def for_user(self, user: "User") -> "OAuthGrantQuerySet":
        """Return the grants the given user has made.

        Args:
            user: The user to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(user=user)

    def active(self) -> "OAuthGrantQuerySet":
        """Return the grants that have not been revoked.

        Returns:
            Filtered queryset.

        """
        return self.filter(revoked_at__isnull=True)


class OAuthGrant(BaseModel):
    """One user's approval of one client — a "connected app".

    Unique per (user, client): approving the same client again reuses the
    row, clears ``revoked_at`` and updates ``scope`` / ``resource``. Tokens
    issued before a revocation stay revoked.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_grants",
        help_text="The user who approved the client.",
    )
    client = models.ForeignKey(
        OAuthClient,
        on_delete=models.CASCADE,
        related_name="grants",
        help_text="The client the user approved.",
    )
    scope = models.CharField(
        max_length=200,
        blank=True,
        help_text="Space-separated scopes the user approved.",
    )
    resource = models.CharField(
        max_length=500,
        blank=True,
        help_text="The MCP resource URL the approval was for.",
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When a token under this grant last reached the MCP server.",
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user disconnected the client; null while active.",
    )

    objects = OAuthGrantQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        verbose_name = "OAuth grant"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "client"],
                name="unique_oauth_grant_per_user_client",
            ),
        ]

    @property
    def is_active(self) -> bool:
        """Return True while the grant has not been revoked."""
        return self.revoked_at is None

    def to_string(self) -> str:
        """Return ``"{user} → {client name}"``, marked when revoked."""
        suffix = " (revoked)" if self.revoked_at else ""
        return f"{self.user} → {self.client.display_name}{suffix}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# AuthorizationCode
# ---------------------------------------------------------------------------


class AuthorizationCodeQuerySet(models.QuerySet["AuthorizationCode"]):
    """Custom queryset for AuthorizationCode."""

    def expired_before(self, cutoff: datetime) -> "AuthorizationCodeQuerySet":
        """Return the codes that expired before ``cutoff``.

        Args:
            cutoff: A tz-aware datetime.

        Returns:
            Filtered queryset.

        """
        return self.filter(expires_at__lt=cutoff)


class AuthorizationCode(BaseModel):
    """A single-use authorization code, bound to one PKCE challenge.

    Every value the token endpoint must check against — the redirect URI the
    authorize request named, the PKCE challenge, the resource and the scope
    — is stored with the code, so the exchange compares against what the
    user approved rather than against anything the client re-sends.
    """

    grant = models.ForeignKey(
        OAuthGrant,
        on_delete=models.CASCADE,
        related_name="codes",
        help_text="The grant the code was issued under.",
    )
    code_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 hex digest of the code. The code itself is not stored.",
    )
    redirect_uri = models.CharField(
        max_length=500,
        help_text="The redirect URI the authorize request named.",
    )
    code_challenge = models.CharField(
        max_length=128,
        help_text="The S256 PKCE challenge the authorize request carried.",
    )
    resource = models.CharField(
        max_length=500,
        help_text="The MCP resource URL the code is for.",
    )
    scope = models.CharField(
        max_length=200,
        blank=True,
        help_text="Space-separated scopes the code carries.",
    )
    expires_at = models.DateTimeField(help_text="When the code stops being accepted.")
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the code was exchanged; a used code is never accepted again.",
    )

    objects = AuthorizationCodeQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def is_expired(self) -> bool:
        """Return True once the code's lifetime has passed."""
        return self.expires_at <= timezone.now()

    def to_string(self) -> str:
        """Return ``"code for {grant}"``, marked when used."""
        suffix = " (used)" if self.used_at else ""
        return f"code for {self.grant}{suffix}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# OAuthToken
# ---------------------------------------------------------------------------


class OAuthTokenQuerySet(models.QuerySet["OAuthToken"]):
    """Custom queryset for OAuthToken."""

    def access(self) -> "OAuthTokenQuerySet":
        """Return the access tokens.

        Returns:
            Filtered queryset.

        """
        return self.filter(kind=OAuthToken.KIND.ACCESS)

    def refresh(self) -> "OAuthTokenQuerySet":
        """Return the refresh tokens.

        Returns:
            Filtered queryset.

        """
        return self.filter(kind=OAuthToken.KIND.REFRESH)

    def unrevoked(self) -> "OAuthTokenQuerySet":
        """Return the tokens that have not been revoked.

        Returns:
            Filtered queryset.

        """
        return self.filter(revoked_at__isnull=True)

    def spent_before(self, cutoff: datetime) -> "OAuthTokenQuerySet":
        """Return tokens that expired, or were revoked, before ``cutoff``.

        Args:
            cutoff: A tz-aware datetime.

        Returns:
            Filtered queryset.

        """
        return self.filter(
            models.Q(expires_at__lt=cutoff) | models.Q(revoked_at__lt=cutoff)
        )


class OAuthToken(BaseModel):
    """An access token (one hour) or a refresh token (thirty days).

    A refresh token is rotated on every use: the old row gains
    ``consumed_at`` (and ``replaced_by``, when a successor is issued) and is
    never accepted again.
    Presenting a rotated token is reuse, which revokes the whole grant
    (``apps.oauth.services.tokens.refresh``).
    """

    class KIND(models.TextChoices):
        """Access or refresh."""

        ACCESS = "ACCESS", "Access token"
        REFRESH = "REFRESH", "Refresh token"

    grant = models.ForeignKey(
        OAuthGrant,
        on_delete=models.CASCADE,
        related_name="tokens",
        help_text="The grant the token was issued under.",
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND.choices,
        help_text="Access or refresh.",
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 hex digest of the token. The token itself is not stored.",
    )
    resource = models.CharField(
        max_length=500,
        help_text="The MCP resource URL the token is for (its audience).",
    )
    scope = models.CharField(
        max_length=200,
        blank=True,
        help_text="Space-separated scopes the token carries.",
    )
    expires_at = models.DateTimeField(help_text="When the token stops being accepted.")
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the token was revoked; null while live.",
    )
    replaced_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="For a rotated refresh token, the token that replaced it.",
    )
    consumed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "For a refresh token, when it was spent on a refresh. Presenting it "
            "again is reuse, whether or not a successor was issued."
        ),
    )

    objects = OAuthTokenQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        verbose_name = "OAuth token"

    @property
    def is_expired(self) -> bool:
        """Return True once the token's lifetime has passed."""
        return self.expires_at <= timezone.now()

    def to_string(self) -> str:
        """Return ``"{kind} for {grant}"``, marked when revoked."""
        suffix = " (revoked)" if self.revoked_at else ""
        return f"{self.get_kind_display()} for {self.grant}{suffix}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
