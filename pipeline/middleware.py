"""
pipeline/middleware.py — Lightweight observability middleware.

``QueryCountMiddleware`` attaches an ``X-DB-Query-Count`` response header
recording the number of SQL statements executed while servicing the
request. It is a no-op unless ``settings.QUERY_COUNT_HEADER_ENABLED`` is
truthy, which keeps the debug-cursor cost out of production while still
giving the header to dev / perf environments and to the
``monitor_query_counts`` management command (which reads the header to
track per-page query counts in ``perf/query_counts.txt``).
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
