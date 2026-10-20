"""
tests/public/_error_urls.py — Test URLconf for the error handlers (SNOW-1151).

The real ``config.urls`` patterns and handlers, plus four views that fail on
purpose, so ``tests/public/test_errors.py`` can drive each handler through
the full middleware stack rather than calling it directly.
"""

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import HttpRequest, HttpResponse
from django.urls import path

from config.urls import (  # noqa: F401 — Django reads the handlers off this module
    handler400,
    handler403,
    handler404,
    handler500,
    urlpatterns as _site_patterns,
)


def boom(request: HttpRequest) -> HttpResponse:
    """Raise an unhandled exception (→ handler500)."""
    raise RuntimeError("boom")


def denied(request: HttpRequest) -> HttpResponse:
    """Raise PermissionDenied (→ handler403)."""
    raise PermissionDenied("not yours")


def suspicious(request: HttpRequest) -> HttpResponse:
    """Raise SuspiciousOperation (→ handler400)."""
    raise SuspiciousOperation("odd")


def form(request: HttpRequest) -> HttpResponse:
    """Accept a POST, so a CSRF check has a view to guard."""
    return HttpResponse("ok")


urlpatterns = [
    path("_test/boom/", boom),
    path("api/_test/boom/", boom),
    path("_test/denied/", denied),
    path("_test/suspicious/", suspicious),
    path("_test/form/", form),
    *_site_patterns,
]
