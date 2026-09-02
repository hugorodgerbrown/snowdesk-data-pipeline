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

``AppVersionHeaderMiddleware`` stamps ``X-App-Version`` on every response
(SNOW-369). The PWA client reads it on every response — not just at startup
— so a deploy is noticed the moment it rolls, without waiting for the next
scheduled visit to ``/api/version``.

SNOW-609 removed the companion ``X-App-Min-Version`` header. The client no
longer compares versions at all: whether a build is acceptable is a server
decision, returned as ``update_required`` in the ``/api/version`` body.
Dropping the header also disarms already-deployed shells, which read a
missing floor as "no floor enforced".

``WeatherIconSetMiddleware`` (SNOW-791) honours ``?icons=<name>`` in DEBUG,
pinning one of the candidate weather icon sets for the session so they can
be compared against real data. Inert when DEBUG is off.
"""

from __future__ import annotations

from collections.abc import Callable

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse

from apps.weather.icon_sets import set_active_icon_set_resolver


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
    """Stamp ``X-App-Version`` on every response.

    The PWA client compares its own build to this header on every response
    and, on a mismatch, schedules one authoritative ``/api/version`` round
    trip — the header is a hint that something may have moved, never a
    verdict (``static/js/pwa_version_check.js``).

    SNOW-609: the companion ``X-App-Min-Version`` header is gone. It carried
    ``settings.APP_MIN_VERSION``, a floor the client compared against by
    string inequality — meaningless when ``APP_VERSION`` resolves to a git
    SHA. Whether a client must force-update is now decided server-side and
    reported only in the ``/api/version`` body.
    """

    _CURRENT_HEADER = "X-App-Version"

    def __init__(
        self,
        get_response: Callable[[HttpRequest], HttpResponse],
    ) -> None:
        """Bind the next middleware callable."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Run the view, stamp the header.

        The value is read from settings at request time (not cached at init)
        so ``override_settings`` in tests takes effect immediately, and so a
        settings hot-reload picks up new values without a process restart.
        The lookup is a bare attribute access on the settings holder — cheap.
        """
        response = self.get_response(request)
        response[self._CURRENT_HEADER] = str(getattr(settings, "APP_VERSION", ""))
        return response


class WeatherIconSetMiddleware:
    """Let ``?icons=<name>`` pin a weather icon set for the session (DEBUG only).

    SNOW-791 is choosing between four candidate icon sets, and the only way
    to judge them is against real data on the real surfaces. An environment
    variable would mean a restart per comparison; this makes it a click.

    The parameter is honoured **only when ``DEBUG`` is on**. On a deployed
    site the middleware is inert, so a crafted query string cannot change
    what any visitor is served — ``settings.WEATHER_ICON_SET`` is then the
    only input. An unknown name is stored as-is and resolved by
    ``apps.weather.icon_sets.icon_set_dir``, which falls back rather than
    raising.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler in the chain.

        Args:
            get_response: The next middleware or view.

        """
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pin the requested set, then defer to the rest of the chain.

        Args:
            request: The incoming request.

        Returns:
            The downstream response, unmodified.

        """
        set_active_icon_set_resolver(lambda: self._pinned_set(request))
        try:
            return self.get_response(request)
        finally:
            set_active_icon_set_resolver(None)

    @staticmethod
    def _pinned_set(request: HttpRequest) -> str | None:
        """Return the set pinned for this request, touching the session only if needed.

        **Reading the session at all adds ``Vary: Cookie`` to the response**,
        which defeats ``Cache-Control: public`` on the CDN-cacheable
        endpoints (`/robots.txt`, `/llms.txt` — SNOW-338). A request with no
        session cookie cannot have a pinned set, so there is nothing to read
        and the session is left alone.

        Args:
            request: The incoming request.

        Returns:
            The pinned set name, or ``None``.

        """
        if not settings.DEBUG or not hasattr(request, "session"):
            return None
        if choice := request.GET.get("icons"):
            request.session["weather_icon_set"] = choice
            return choice
        if settings.SESSION_COOKIE_NAME not in request.COOKIES:
            return None
        return request.session.get("weather_icon_set")
