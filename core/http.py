"""
core/http.py — HTTP request utility helpers.

Shared low-level helpers for extracting information from Django HttpRequest
objects. Kept in ``core`` so any app can import without creating circular
dependencies.
"""

from __future__ import annotations

from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str:
    """Return the best-guess client IP address from the request.

    Prefers the leftmost entry in the ``X-Forwarded-For`` header (the
    original client IP when behind Render's reverse proxy) and falls back
    to ``REMOTE_ADDR``. Returns an empty string when neither is present.

    Note: ``X-Forwarded-For`` can be spoofed by the client. Use only for
    analytics and rate-limiting; never for access control.

    Args:
        request: The current HTTP request.

    Returns:
        An IP address string, or ``""`` when no address can be determined.

    """
    forwarded_for: str = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR", "") or "")
