"""
apps/public/errors.py — Site-wide error handlers (SNOW-1151).

Wired in ``config/urls.py`` as ``handler400`` / ``handler403`` /
``handler404`` / ``handler500``, and as ``CSRF_FAILURE_VIEW`` in
``config/settings/base.py``. Each handler negotiates one of three shapes:

* **JSON** — for ``/api/``, ``/oauth/`` and ``/.well-known/`` paths, and for
  any request that accepts JSON but not HTML. Body is
  ``{"error": <slug>, "status": <code>}``; a JSON client should never have
  to parse an HTML page to learn it asked for something that isn't there.
* **Plain text** — for HTMX requests. htmx does not swap a 4xx/5xx response
  by default, so a full page here would be bytes nobody renders; the
  one-line body is what ``htmx:responseError`` listeners can show.
* **HTML** — everyone else.

The HTML pages come in two kinds, and the split is the point of this module
(``docs/decisions/the-500-page-renders-without-context.md``):

* **400 and 500 are context-free.** They render ``errors/_standalone.html``
  with a plain dict — no ``RequestContext``, so no context processors, no
  ``request.user``, no session and no database. A 500 is often *caused* by
  one of those, and a 400 can be raised by ``DisallowedHost``, where any
  context processor that builds an absolute URL raises again.
* **403 and 404 get the full site.** They render ``errors/page.html``,
  which extends ``_status_page.html``, with the request, so the reader
  keeps the nav and their account menu. If that render itself fails, the
  handler drops to the standalone shell rather than letting the error page
  become a 500.

Every path has a last resort: if even the standalone template fails (a
missing static manifest is the realistic case), ``_FALLBACK_HTML`` is
returned as a literal string.

Copy lives here, in ``ERROR_PAGES``, so the full-site and standalone
renderings of one status cannot drift apart. ``preview_error_page`` (behind
the staff-only ``/_errors/<kind>/`` route in ``debug_views.py``) renders the
same responses so the pages can be reviewed in a DEBUG build, where Django
shows its technical pages instead.
"""

from __future__ import annotations

import dataclasses
import logging

from django.http import (
    HttpRequest,
    HttpResponse,
    JsonResponse,
)
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers
from django.utils.functional import Promise
from django.utils.translation import get_language, gettext_lazy as _
from django.views.decorators.csrf import requires_csrf_token

logger = logging.getLogger(__name__)

# Path prefixes whose clients speak JSON, whatever their Accept header says.
JSON_PATH_PREFIXES = ("/api/", "/oauth/", "/.well-known/")

SITE_TEMPLATE = "errors/page.html"
STANDALONE_TEMPLATE = "errors/_standalone.html"

# The last resort, returned when the standalone template cannot render.
# Literal English with no assets: by the time this is reached, neither the
# template engine nor the static manifest can be trusted.
_FALLBACK_HTML = (
    "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<title>Error — Snowdesk</title></head><body>"
    "<h1>Something went wrong</h1>"
    "<p>Snowdesk couldn't load this page. Try again in a minute.</p>"
    "<p><a href='/'>Go to the homepage</a></p></body></html>"
)


@dataclasses.dataclass(frozen=True)
class ErrorPage:
    """Everything one error response needs, in all three shapes.

    Attributes:
        status: The HTTP status code returned.
        slug: Machine-readable error name — the JSON ``error`` value.
        heading: The page's ``<h1>``; also the HTMX plain-text body.
        body: One or two sentences under the heading.
        full_site: True when the HTML page renders ``errors/page.html``
            with the request (nav, account menu); False when it is only
            ever rendered context-free.

    """

    status: int
    slug: str
    heading: str | Promise
    body: str | Promise
    full_site: bool


ERROR_PAGES: dict[str, ErrorPage] = {
    "400": ErrorPage(
        status=400,
        slug="bad_request",
        heading=_("This request couldn't be read"),
        body=_(
            "Something in the address or the form didn't make sense to "
            "the server. Go back and try again."
        ),
        full_site=False,
    ),
    "403": ErrorPage(
        status=403,
        slug="forbidden",
        heading=_("You don't have access to this page"),
        body=_(
            "It may belong to another account. If you have more than one, "
            "check which one you're signed in with."
        ),
        full_site=True,
    ),
    "csrf": ErrorPage(
        status=403,
        slug="csrf_failed",
        heading=_("This form has expired"),
        body=_(
            "Reload the page and submit it again. This happens when a page "
            "has been open for a long time, or when cookies are blocked."
        ),
        full_site=True,
    ),
    "404": ErrorPage(
        status=404,
        slug="not_found",
        heading=_("Page not found"),
        body=_(
            "The link may be mistyped or out of date. The map and today's "
            "bulletins are where you left them."
        ),
        full_site=True,
    ),
    "500": ErrorPage(
        status=500,
        slug="server_error",
        heading=_("Something went wrong on our side"),
        body=_("Snowdesk couldn't load this page. Try again in a minute."),
        full_site=False,
    ),
}


def wants_json(request: HttpRequest) -> bool:
    """Return True when the client should get a JSON error body.

    True for the JSON path prefixes, and for a request that accepts
    ``application/json`` but not ``text/html``. A browser's ``*/*`` accepts
    both, so it falls through to HTML.

    Args:
        request: The request that produced the error.

    Returns:
        Whether to answer with JSON.

    """
    if request.path.startswith(JSON_PATH_PREFIXES):
        return True
    return request.accepts("application/json") and not request.accepts("text/html")


def _is_htmx(request: HttpRequest) -> bool:
    """Return True for an htmx-issued request.

    Reads the header directly rather than ``request.htmx``: a handler can
    run for a request that never reached ``HtmxMiddleware``.

    Args:
        request: The request that produced the error.

    Returns:
        Whether the ``HX-Request`` header is set.

    """
    return request.headers.get("HX-Request") == "true"


def _standalone_response(request: HttpRequest, page: ErrorPage) -> HttpResponse:
    """Render ``page`` in the context-free shell, or the literal fallback.

    The context is a plain dict, and ``render_to_string`` is called without
    its ``request`` argument, so no context processor runs. The request is
    passed as an ordinary variable for one reason: ``theme_head.html`` reads
    ``request.csp_nonce``, an attribute the CSP middleware already set. The
    template must read nothing else off it — ``request.user`` would be a
    session and database lookup, which is what this shell exists to avoid.

    Args:
        request: The request that produced the error.
        page: The error to render.

    Returns:
        An HTML response with ``page.status``.

    """
    try:
        # SNOW-1180: the fivebar domain and token-free path, from the
        # context processor's function called directly — this shell must
        # not run the context processors themselves.
        from apps.public.context_processors import fivebar

        html: str = render_to_string(
            STANDALONE_TEMPLATE,
            {
                "page": page,
                "LANGUAGE_CODE": get_language() or "en",
                "request": request,
                **fivebar(request),
            },
        )
    except Exception:
        logger.exception("Standalone error template failed; using fallback")
        html = _FALLBACK_HTML
    return HttpResponse(html, status=page.status)


def error_response(request: HttpRequest, kind: str) -> HttpResponse:
    """Build the negotiated response for one error ``kind``.

    Every shape carries ``Vary: Accept, HX-Request``: one URL can now answer
    with JSON, plain text or HTML, so a cache that kept the JSON 404 for an
    API client must not hand it to a browser asking for the same URL.

    Args:
        request: The request that produced the error.
        kind: A key of ``ERROR_PAGES``.

    Returns:
        A JSON, plain-text or HTML response carrying the error's status.

    """
    response = _negotiate(request, kind)
    patch_vary_headers(response, ("Accept", "HX-Request"))
    return response


def _negotiate(request: HttpRequest, kind: str) -> HttpResponse:
    """Pick JSON, plain text or HTML for ``kind`` — see ``error_response``.

    Args:
        request: The request that produced the error.
        kind: A key of ``ERROR_PAGES``.

    Returns:
        The response, before any ``Vary`` header is added.

    """
    page = ERROR_PAGES[kind]
    if wants_json(request):
        return JsonResponse(
            {"error": page.slug, "status": page.status}, status=page.status
        )
    if _is_htmx(request):
        return HttpResponse(
            str(page.heading),
            status=page.status,
            content_type="text/plain; charset=utf-8",
        )
    if not page.full_site:
        return _standalone_response(request, page)
    try:
        # SNOW-1180: fivebar_status becomes the fivebar tag's data-status.
        return render(
            request,
            SITE_TEMPLATE,
            {"page": page, "fivebar_status": page.status},
            status=page.status,
        )
    except Exception:
        logger.exception("Error page for %s failed; using standalone", kind)
        return _standalone_response(request, page)


@requires_csrf_token
def bad_request(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler400`` — malformed request, ``SuspiciousOperation``, bad host.

    Args:
        request: The offending request.
        exception: The exception Django caught (not shown to the reader).

    Returns:
        The negotiated 400 response.

    """
    return error_response(request, "400")


@requires_csrf_token
def permission_denied(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler403`` — a raised ``PermissionDenied``.

    Views that return ``HttpResponse(status=403)`` directly do not reach
    this handler, and keep their own bodies.

    Args:
        request: The refused request.
        exception: The ``PermissionDenied`` (its message is not shown).

    Returns:
        The negotiated 403 response.

    """
    return error_response(request, "403")


@requires_csrf_token
def page_not_found(request: HttpRequest, exception: Exception) -> HttpResponse:
    """``handler404`` — no URL matched, or a view raised ``Http404``.

    Args:
        request: The request for the missing resource.
        exception: The ``Http404`` / ``Resolver404`` (not shown).

    Returns:
        The negotiated 404 response.

    """
    return error_response(request, "404")


@requires_csrf_token
def server_error(request: HttpRequest) -> HttpResponse:
    """``handler500`` — an unhandled exception.

    Django has already logged the traceback via ``django.request`` before
    calling this, so nothing is logged here.

    Args:
        request: The request whose view raised.

    Returns:
        The negotiated 500 response; HTML is always the standalone shell.

    """
    return error_response(request, "500")


def csrf_failure(request: HttpRequest, reason: str = "") -> HttpResponse:
    """``CSRF_FAILURE_VIEW`` — a POST whose CSRF check failed.

    Replaces Django's default, which renders its template without the
    request and so could not use the site's base template.

    Args:
        request: The rejected request.
        reason: Django's reason string; logged, never shown.

    Returns:
        The negotiated 403 response with the ``csrf_failed`` copy.

    """
    logger.warning("CSRF failure on %s: %s", request.path, reason)
    return error_response(request, "csrf")
