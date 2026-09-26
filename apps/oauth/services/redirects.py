"""
apps/oauth/services/redirects.py — Redirect URI rules.

Two rules, both from OAuth 2.1 and Claude's connector requirements:

* **Registration.** A redirect URI must be ``https`` with a host, or an
  ``http`` loopback URI (``localhost`` / ``127.0.0.1``). No fragment. Claude's
  hosted apps use ``https://claude.ai/api/mcp/auth_callback``; Claude Code
  uses ``http://localhost:<port>/callback``.
* **Matching.** Exact string match against the client's registered list,
  except that a loopback URI matches a registered loopback URI on any port
  (RFC 8252 §7.3) — Claude Code binds a fresh port every time.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from apps.oauth.models import OAuthClient

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1"})


def is_loopback(uri: str) -> bool:
    """Return True for an ``http`` URI on ``localhost`` or ``127.0.0.1``.

    Args:
        uri: The redirect URI.

    Returns:
        True for a loopback redirect.

    """
    try:
        parts = urlsplit(uri)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme == "http" and host in LOOPBACK_HOSTS


def is_registrable(uri: object) -> bool:
    """Return True when ``uri`` may be registered as a redirect URI.

    Args:
        uri: A candidate from a registration payload or a metadata document.

    Returns:
        True for an https URI with a host, or an http loopback URI; never
        for one with a fragment or embedded credentials.

    """
    if not isinstance(uri, str) or not uri or len(uri) > 500:
        return False
    try:
        parts = urlsplit(uri)
        host = parts.hostname
        # Touch the port so a malformed one raises here rather than later.
        _ = parts.port
    except ValueError:
        return False
    if parts.fragment or parts.username or parts.password or not host:
        return False
    if parts.scheme == "https":
        return True
    return is_loopback(uri)


def _without_port(uri: str) -> tuple[str, str, str, str]:
    """Return a loopback URI's (scheme, host, path, query), dropping the port."""
    parts = urlsplit(uri)
    return (parts.scheme, parts.hostname or "", parts.path, parts.query)


def redirect_uri_allowed(client: "OAuthClient", uri: str) -> bool:
    """Return True when ``uri`` is one of ``client``'s redirect URIs.

    Exact match, except that a loopback ``uri`` matches a registered
    loopback URI whatever either's port.

    Args:
        client: The client the authorize request names.
        uri: The ``redirect_uri`` from the request.

    Returns:
        True when the authorize endpoint may send a code to ``uri``.

    """
    if not uri:
        return False
    registered = [str(u) for u in client.redirect_uris or []]
    if uri in registered:
        return True
    if not is_loopback(uri):
        return False
    try:
        wanted = _without_port(uri)
    except ValueError:
        return False
    return any(is_loopback(r) and _without_port(r) == wanted for r in registered)


def only_loopback(client: "OAuthClient") -> bool:
    """Return True when every one of ``client``'s redirect URIs is loopback.

    The consent page warns in that case: the code goes to a program on
    this machine, not to a named web service.

    Args:
        client: The client.

    Returns:
        True for a non-empty, loopback-only list.

    """
    uris = [str(u) for u in client.redirect_uris or []]
    return bool(uris) and all(is_loopback(u) for u in uris)
