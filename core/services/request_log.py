"""
core/services/request_log.py — Thin service wrapper for RequestLog capture.

Re-exports the ``capture`` function so views import from
``core.services.request_log`` rather than reaching into the manager directly.
Keeping view imports at the service boundary makes the dependency graph easier
to follow and the call site easier to mock in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest

    from core.models import RequestLog


def capture(request: "HttpRequest") -> "RequestLog":
    """Capture request context and persist a new RequestLog row.

    Delegates to ``RequestLog.objects.from_request(request)``.  Saves once;
    returns the new instance immediately.

    Args:
        request: The current Django HTTP request.

    Returns:
        The newly saved RequestLog instance.

    """
    from core.models import RequestLog as _RequestLog  # noqa: PLC0415

    return _RequestLog.objects.from_request(request)
