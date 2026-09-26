"""
apps/oauth/services/clients.py — Client registration and lookup.

Two ways a client becomes known:

* **Dynamic Client Registration** (RFC 7591) — ``register_client`` takes the
  JSON a client POSTs to ``/oauth/register/`` and creates a ``DCR`` row with
  a random ``client_id``. Public clients only: whatever
  ``token_endpoint_auth_method`` was asked for, the answer is ``none``
  (RFC 7591 §3.2.1 lets the server replace requested metadata).
* **Client ID Metadata Document** — a ``client_id`` that is an ``https://``
  URL is fetched (``apps.oauth.services.cimd``) and cached on a ``CIMD`` row,
  refetched once it is 24 hours old.

``resolve_client`` is the single lookup the authorize endpoint uses.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from apps.oauth.models import OAuthClient
from apps.oauth.services.cimd import (
    CimdError,
    fetch_client_metadata,
    trimmed_client_name,
)
from apps.oauth.services.redirects import is_registrable
from apps.oauth.services.tokens import hash_secret

logger = logging.getLogger(__name__)

CIMD_CACHE_TTL = timedelta(hours=24)
MAX_REDIRECT_URIS: int = 10


class RegistrationError(Exception):
    """A DCR payload was refused, with its RFC 7591 error code."""

    def __init__(self, error: str, description: str) -> None:
        """Store the RFC 7591 ``error`` and a human-readable description.

        Args:
            error: ``invalid_redirect_uri`` or ``invalid_client_metadata``.
            description: What was wrong.

        """
        super().__init__(description)
        self.error = error
        self.description = description


def register_client(payload: object) -> OAuthClient:
    """Create a DCR client from a registration request body.

    Args:
        payload: The decoded JSON body.

    Returns:
        The new ``OAuthClient``.

    Raises:
        RegistrationError: For a non-object body, missing or unusable
            ``redirect_uris``, or a grant type other than the two supported.

    """
    if not isinstance(payload, dict):
        raise RegistrationError(
            "invalid_client_metadata", "The request body must be a JSON object."
        )

    redirect_uris = payload.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise RegistrationError(
            "invalid_redirect_uri", "redirect_uris must be a non-empty list."
        )
    if len(redirect_uris) > MAX_REDIRECT_URIS:
        raise RegistrationError(
            "invalid_redirect_uri",
            f"At most {MAX_REDIRECT_URIS} redirect_uris may be registered.",
        )
    if not all(is_registrable(u) for u in redirect_uris):
        raise RegistrationError(
            "invalid_redirect_uri",
            "Every redirect URI must be https, or http on localhost / 127.0.0.1.",
        )

    grant_types = payload.get("grant_types", ["authorization_code"])
    if not isinstance(grant_types, list) or not set(grant_types) <= {
        "authorization_code",
        "refresh_token",
    }:
        raise RegistrationError(
            "invalid_client_metadata",
            "Only the authorization_code and refresh_token grants are supported.",
        )

    client_name = trimmed_client_name(payload.get("client_name"))

    client = OAuthClient.objects.create(
        client_id="sd_client_" + secrets.token_urlsafe(24),
        kind=OAuthClient.KIND.DCR,
        client_name=client_name,
        redirect_uris=list(redirect_uris),
    )
    logger.info("oauth: registered DCR client pk=%s", client.pk)
    return client


def registration_response(client: OAuthClient) -> dict[str, Any]:
    """Return the RFC 7591 client information response for ``client``.

    Args:
        client: A newly registered client.

    Returns:
        The JSON body for the 201 response.

    """
    return {
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


#: How long a failed CIMD fetch is remembered, so repeating one authorize
#: request cannot make the server fetch the same URL again and again.
CIMD_FAILURE_TTL_SECONDS = 300


def _cimd_failure_key(client_id: str) -> str:
    """Return the cache key recording a failed fetch of ``client_id``."""
    return f"oauth:cimd-failed:{hash_secret(client_id)}"


def _resolve_cimd(client_id: str) -> OAuthClient | None:
    """Return the cached CIMD client, refetching once it is 24 hours old.

    Args:
        client_id: The https URL.

    Returns:
        The client row, or None when the document cannot be used.

    """
    existing = OAuthClient.objects.filter(client_id=client_id).first()
    if existing is not None and existing.kind != OAuthClient.KIND.CIMD:
        return None
    now = timezone.now()
    if (
        existing is not None
        and existing.metadata_fetched_at is not None
        and now - existing.metadata_fetched_at < CIMD_CACHE_TTL
    ):
        return existing

    if cache.get(_cimd_failure_key(client_id)):
        return None
    try:
        metadata = fetch_client_metadata(client_id)
    except CimdError as exc:
        logger.info("oauth: CIMD fetch refused for %s: %s", client_id, exc)
        cache.set(_cimd_failure_key(client_id), True, CIMD_FAILURE_TTL_SECONDS)
        return None

    client, _ = OAuthClient.objects.update_or_create(
        client_id=client_id,
        defaults={
            "kind": OAuthClient.KIND.CIMD,
            "client_name": metadata.client_name,
            "redirect_uris": metadata.redirect_uris,
            "metadata_fetched_at": now,
        },
    )
    return client


def resolve_client(client_id: str) -> OAuthClient | None:
    """Return the client an authorize request names, or None.

    A DCR or LOCAL ``client_id`` is a row lookup. An ``https://`` one is a
    CIMD client: its cached row, or a fresh fetch of its document.

    Args:
        client_id: The ``client_id`` query parameter.

    Returns:
        The client, or None when it is unknown or its document is unusable.

    """
    if not client_id or len(client_id) > 500:
        return None
    if client_id.startswith("https://"):
        return _resolve_cimd(client_id)
    return (
        OAuthClient.objects.filter(client_id=client_id)
        .exclude(kind=OAuthClient.KIND.CIMD)
        .first()
    )
