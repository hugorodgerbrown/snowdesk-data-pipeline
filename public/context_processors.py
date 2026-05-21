"""
public/context_processors.py — Template context processors for the public site.

Exposes site-level settings as template variables so templates can build
absolute URLs without view-layer boilerplate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest


def site_base_url(request: HttpRequest) -> dict[str, Any]:
    """
    Inject ``SITE_BASE_URL`` into every template context.

    Reads ``settings.SITE_BASE_URL`` (configured via ``.env`` / python-decouple,
    defaulting to ``"http://localhost:8000"`` in development) and exposes it as
    ``{{ SITE_BASE_URL }}`` so templates can build absolute URLs for OG/Twitter
    meta tags and other contexts that require a fully-qualified base URL.

    The trailing slash is stripped so templates can concatenate directly:
    ``{{ SITE_BASE_URL }}{% static 'social/og-default.png' %}`` produces a
    well-formed absolute URL without a double slash.

    Args:
        request: The incoming HTTP request (unused — value comes from settings).

    Returns:
        ``{"SITE_BASE_URL": str}`` with the base URL, trailing-slash stripped.

    """
    return {"SITE_BASE_URL": settings.SITE_BASE_URL.rstrip("/")}
