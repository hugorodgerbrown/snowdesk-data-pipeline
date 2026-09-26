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
from urllib.parse import urlsplit, urlunsplit

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


def registered_redirect_uri(client: "OAuthClient", uri: str) -> str | None:
    """Return the redirect URI to send a browser to, taken from ``client``'s record.

    The authorize endpoint never redirects to the string it was given: it
    redirects to the matching entry in the client's registered list, so the
    target's scheme, host, path and query always come from registration.
    For a loopback match (RFC 8252 §7.3, any port) the only part taken from
    the request is the port, parsed as an integer.

    Args:
        client: The client the authorize request names.
        uri: The ``redirect_uri`` from the request.

    Returns:
        The URI to redirect to, which equals ``uri`` for every URI a client
        can legitimately send, or ``None`` when ``uri`` is not registered.

    """
    if not uri:
        return None
    registered = [str(u) for u in client.redirect_uris or []]
    for candidate in registered:
        if candidate == uri:
            return candidate
    return _registered_loopback_uri(registered, uri)


def _registered_loopback_uri(registered: list[str], uri: str) -> str | None:
    """Return the loopback URI registered for ``uri``, with the request's port.

    Args:
        registered: The client's registered redirect URIs.
        uri: The ``redirect_uri`` from the request.

    Returns:
        The registered URI with the request's port (an integer) put in, or
        ``None`` when ``uri`` is not loopback or matches no registered one.

    """
    if not is_loopback(uri):
        return None
    try:
        wanted = _without_port(uri)
        port = urlsplit(uri).port
    except ValueError:
        return None
    for candidate in registered:
        if is_loopback(candidate) and _without_port(candidate) == wanted:
            parts = urlsplit(candidate)
            netloc = parts.hostname or ""
            if port is not None:
                netloc = f"{netloc}:{int(port)}"
            return urlunsplit(parts._replace(netloc=netloc))
    return None


def redirect_uri_allowed(client: "OAuthClient", uri: str) -> bool:
    """Return True when ``uri`` is one of ``client``'s redirect URIs.

    Exact match, except that a loopback ``uri`` matches a registered
    loopback URI whatever either's port. See :func:`registered_redirect_uri`
    for the value to actually redirect to.

    Args:
        client: The client the authorize request names.
        uri: The ``redirect_uri`` from the request.

    Returns:
        True when the authorize endpoint may send a code to ``uri``.

    """
    return registered_redirect_uri(client, uri) is not None


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
