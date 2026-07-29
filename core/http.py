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


# Sec-Purpose values that mark a request the browser made speculatively, on
# the user's behalf but without their intent. The header is structured
# ("prefetch;anonymous-client-ip"), so match on substring rather than
# equality.
_SPECULATIVE_PURPOSES: tuple[str, ...] = ("prefetch", "prerender")


def is_speculative(request: HttpRequest) -> bool:
    """Return True when the request is a prefetch, prerender, or HEAD.

    None of these represent a person choosing to visit: a browser, crawler,
    or link scanner made them ahead of (or instead of) a real navigation.
    Endpoints that record analytics on arrival use this to skip the write
    while still answering normally — counting a prefetch as a click inflates
    the metric and lets a scanner grow the table without bound.

    ``RequestLog.objects.capture()`` stores the raw ``Sec-Purpose`` header
    for the requests it does log; this reads the same header to decide
    whether to log at all.

    Args:
        request: The current HTTP request.

    Returns:
        True when the request should not be recorded as a real visit.

    """
    if (request.method or "").upper() == "HEAD":
        return True
    purpose = request.headers.get("Sec-Purpose", "").lower()
    return any(token in purpose for token in _SPECULATIVE_PURPOSES)
