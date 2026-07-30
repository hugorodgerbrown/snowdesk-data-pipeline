"""
apps/core/middleware.py — Lightweight observability and security middleware.

``QueryCountMiddleware`` attaches an ``X-DB-Query-Count`` response header
recording the number of SQL statements executed while servicing the
request. It is a no-op unless ``settings.QUERY_COUNT_HEADER_ENABLED`` is
truthy, which keeps the debug-cursor cost out of production while still
giving the header to dev / perf environments and to the
``monitor_query_counts`` management command (which reads the header to
track per-page query counts in ``perf/query_counts.txt``).

``SecurityHeadersMiddleware`` adds ``Referrer-Policy`` and
``Permissions-Policy`` headers to every response.  Per-view overrides on
token-bearing views take precedence because the middleware sets its value
only when the header is absent — ``no-referrer`` on terminal responses
(error pages, redirects) and ``same-origin`` on GET pages that render a
same-origin POST form (``same-origin`` is required there so the POST is not
sent with ``Origin: null``, which Django's CSRF check rejects on HTTPS — see
SNOW-438).

``AppVersionHeaderMiddleware`` stamps ``X-App-Version`` and
``X-App-Min-Version`` on every response (SNOW-369). The PWA client reads
these on every response — not just at startup — so mid-session transitions
into a forced-update state fire the moment the deploy rolls, without
waiting for the next visit to ``/api/version``.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse


class QueryCountMiddleware:
    """Expose the per-request SQL query count via a response header."""

    header_name = "X-DB-Query-Count"

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        """Bind the next middleware callable and cache the enable flag."""
        self.get_response = get_response
        self.enabled: bool = bool(
            getattr(settings, "QUERY_COUNT_HEADER_ENABLED", False)
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Run the view, count the queries, stamp the header."""
        if not self.enabled:
            return self.get_response(request)

        # Forcing the debug cursor captures queries regardless of DEBUG.
        # We restore the previous value so a middleware higher up that
        # already enabled it (e.g. a test harness) keeps its state.
        previous = connection.force_debug_cursor
        connection.force_debug_cursor = True
        start = len(connection.queries_log)
        try:
            response = self.get_response(request)
        finally:
            connection.force_debug_cursor = previous

        response[self.header_name] = str(len(connection.queries_log) - start)
        return response


class SecurityHeadersMiddleware:
    """Set Referrer-Policy and Permissions-Policy on every response.

    Uses ``strict-origin-when-cross-origin`` as the global Referrer-Policy
    default.  Views that carry tokens in their URL paths (account_view,
    unsubscribe_view, verify_view, …) set their own value — ``no-referrer``
    on terminal responses, ``same-origin`` on GET pages with a POST form
    (SNOW-438) — and this middleware honours that by only writing the header
    when it is absent.
    """

    _REFERRER_POLICY_HEADER = "Referrer-Policy"
    _REFERRER_POLICY_DEFAULT = "strict-origin-when-cross-origin"
    _PERMISSIONS_POLICY_HEADER = "Permissions-Policy"
    _PERMISSIONS_POLICY_VALUE = (
        "camera=(), microphone=(), geolocation=(self), payment=(), usb=()"
    )

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        """Bind the next middleware callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Stamp security headers on the outgoing response."""
        response = self.get_response(request)
        if self._REFERRER_POLICY_HEADER not in response:
            response[self._REFERRER_POLICY_HEADER] = self._REFERRER_POLICY_DEFAULT
        if self._PERMISSIONS_POLICY_HEADER not in response:
            response[self._PERMISSIONS_POLICY_HEADER] = self._PERMISSIONS_POLICY_VALUE
        return response


class AppVersionHeaderMiddleware:
    """Stamp ``X-App-Version`` and ``X-App-Min-Version`` on every response.

    The PWA client compares its own build to ``X-App-Min-Version`` on every
    response and enters an Update Required state when it falls behind,
    without needing to poll ``/api/version``. Empty ``APP_MIN_VERSION``
    (the default) still sends the header — the client treats an empty
    string as "no minimum enforced", which is safer than an omitted header
    that could be confused with an older server that never set it.
    """

    _CURRENT_HEADER = "X-App-Version"
    _MIN_HEADER = "X-App-Min-Version"

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        """Bind the next middleware callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Run the view, stamp the two headers.

        Values are read from settings at request time (not cached at init)
        so ``override_settings`` in tests takes effect immediately, and so a
        settings hot-reload picks up new values without a process restart.
        The lookup is a bare attribute access on the settings holder — cheap.
        """
        response = self.get_response(request)
        response[self._CURRENT_HEADER] = str(getattr(settings, "APP_VERSION", ""))
        response[self._MIN_HEADER] = str(getattr(settings, "APP_MIN_VERSION", ""))
        return response
