"""
apps/core/http.py — HTTP request utility helpers.

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


# The one ``Sec-Fetch-Dest`` value that means "the browser is about to put
# this in the address bar". Every other value — image, iframe, script,
# empty, style, … — is a subresource some other document asked for.
_TOP_LEVEL_DESTINATION = "document"


def is_top_level_navigation(request: HttpRequest) -> bool:
    """Return True when the request looks like a person following a link.

    Stricter than ``not is_speculative(request)``, and deliberately its own
    helper rather than a widening of that one: ``is_speculative`` answers
    "should this count as a visit" for analytics, where a passive
    subresource load is a harmless over-count. This answers "did a person
    choose to come here", which is the question an endpoint has to ask
    before it writes to the session on a bare GET.

    THE HOLE THIS CLOSES. ``Sec-Purpose`` marks a prefetch, but nothing
    marks an ``<img src="…">`` or ``<iframe src="…">`` pointed at the same
    URL — any page anywhere on the web can embed one and make every
    visitor's browser issue the GET with no interaction at all.
    ``Sec-Fetch-Dest`` is the header that tells the two apart: a browser
    sets it on every request it makes, and only a top-level navigation
    carries ``document``.

    ABSENT MEANS YES, and that is a deliberate trade-off. The header is
    forbidden to scripts, so it cannot be forged — but an older browser
    (or any client that sends no fetch metadata at all) omits it entirely,
    and treating that as a refusal would break the feature for those
    visitors rather than merely narrow it. Fetch metadata is a hardening
    signal, not an authentication one, so the fallback is permissive.

    Speculative requests are excluded too, so one call answers both
    questions for a caller that must not write on either.

    Args:
        request: The current HTTP request.

    Returns:
        True when the request may be treated as a deliberate navigation.

    """
    if is_speculative(request):
        return False
    destination = request.headers.get("Sec-Fetch-Dest", "").strip().lower()
    return destination in ("", _TOP_LEVEL_DESTINATION)
