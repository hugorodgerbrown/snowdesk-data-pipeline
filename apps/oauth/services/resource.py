"""
apps/oauth/services/resource.py — The MCP resource URL and the request origin.

A token is issued for one resource (RFC 8707): the URL of the MCP endpoint.
Two spellings of it are mounted — ``/api/mcp/`` and ``/api/mcp`` — and a
client sends whichever the user typed, so audience checks compare the
*canonical* form, which strips the trailing slash and lowercases the scheme
and host.

The origin comes from the request (``request.build_absolute_uri("/")``),
not from ``SITE_BASE_URL``. That lets a tunnel host (ngrok) or a staging
host issue and accept its own tokens. It is safe because ``ALLOWED_HOSTS``
bounds the host Django will accept, and behind Render
``SECURE_PROXY_SSL_HEADER`` makes the scheme https.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from django.http import HttpRequest
from django.urls import reverse

logger = logging.getLogger(__name__)


def canonical_resource(url: str) -> str:
    """Return the comparison form of a resource URL.

    Lowercases the scheme and host, drops any query and fragment, and
    strips a trailing ``/`` from the path.

    Args:
        url: A resource URL as a client or a stored row gives it.

    Returns:
        The canonical form, or ``""`` for a value with no scheme or host.

    """
    parts = urlsplit((url or "").strip())
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def request_origin(request: HttpRequest) -> str:
    """Return this request's origin, e.g. ``https://snowdesk.info``.

    Args:
        request: The current request.

    Returns:
        Scheme and host, with no trailing slash.

    """
    return request.build_absolute_uri("/").rstrip("/")


def mcp_resource_url(request: HttpRequest) -> str:
    """Return the documented MCP URL for this origin (trailing slash).

    Args:
        request: The current request.

    Returns:
        ``<origin>/api/mcp/``.

    """
    return request_origin(request) + reverse("api:mcp:endpoint")


def mcp_resources(request: HttpRequest) -> frozenset[str]:
    """Return the canonical MCP resource URLs this origin serves.

    Both mounted spellings canonicalise to the same value, so the set has
    one member today; it is a set so a check reads as membership.

    Args:
        request: The current request.

    Returns:
        The canonical forms of ``/api/mcp/`` and ``/api/mcp``.

    """
    origin = request_origin(request)
    return frozenset(
        canonical_resource(origin + reverse(name))
        for name in ("api:mcp:endpoint", "api:mcp:endpoint_noslash")
    )


def is_mcp_resource(request: HttpRequest, url: str) -> bool:
    """Return True when ``url`` names this origin's MCP endpoint.

    Args:
        request: The current request.
        url: The resource URL to check.

    Returns:
        True when its canonical form is one of ``mcp_resources(request)``.

    """
    return canonical_resource(url) in mcp_resources(request)
