"""
apps/public/debug_views.py — Staff-only design-system / debug pages.

Renders every design-system entry from ``apps/public/design_tokens.py`` under
``/_components/``. Sidebar grouped into Foundations (design tokens) and
Components (rendered HTML partials); main column HTMX-swaps via the
sidebar. Also hosts the Web Push demo (``/_push-demo/``) and the SW
shell-version page (``/_sw-version/``, SNOW-517).

Auth: ``staff_member_required`` only — no DEBUG gate. Every page here is
reachable in production by any staff user, by design (everyone with
admin access already has equivalent capability via Django admin).

The earlier ``header_combinations`` view at ``/debug/header/`` (SNOW-101,
shipped as part of SNOW-100) was retired by SNOW-110 — its visual matrix
now lives inside the component library as the **Weather header** entry
under the Components group.
"""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.shortcuts import render

from apps.accounts.models import PushSubscription
from apps.accounts.push_config import VAPID_PUBLIC_KEY
from apps.core.decorators import require_htmx
from apps.core.sw_shell import read_cache_version
from apps.public.design_tokens import LIBRARY_GROUPS, get_category

DEFAULT_SLUG = "typography"


@staff_member_required
def component_library(request: HttpRequest) -> HttpResponse:
    """Render the full component-library page with the active panel SSR.

    The active panel is the one whose slug appears in ``?slug=`` (so
    ``/_components/?slug=weather-header`` deep-links straight to that
    panel), falling back to ``DEFAULT_SLUG`` when the query string is
    absent or names an unknown slug. Silent fallback is preferred over
    a 404 so old or misspelled bookmarks land on a usable page rather
    than a hard error.

    The active panel is rendered server-side so the URL is meaningful
    with JS off and so screen-reader users don't land on an empty main
    column.
    """
    requested_slug = request.GET.get("slug", DEFAULT_SLUG)
    active = get_category(requested_slug) or get_category(DEFAULT_SLUG)
    return render(
        request,
        "_components/index.html",
        {
            "groups": LIBRARY_GROUPS,
            "active": active,
        },
    )


@staff_member_required
@require_htmx
def component_library_panel(
    request: HttpRequest,
    slug: str,
) -> HttpResponse:
    """Return the inner-HTML for one library panel (HTMX-only).

    Unknown ``slug`` returns 404 — the URL is meant to be reached from
    the sidebar, where every entry corresponds to a real category in
    ``LIBRARY_GROUPS``.
    """
    category = get_category(slug)
    if category is None:
        return HttpResponseNotFound()
    return render(
        request,
        "_components/partials/_panel.html",
        {"active": category},
    )


@staff_member_required
def push_demo(request: HttpRequest) -> HttpResponse:
    """Spike demo page for Web Push notifications."""
    return render(
        request,
        "_debug/push_demo.html",
        {
            "vapid_public_key": VAPID_PUBLIC_KEY,
            "subscriptions": PushSubscription.objects.all(),
        },
    )


@staff_member_required
def sw_version(request: HttpRequest) -> HttpResponse:
    """Staff page surfacing the service-worker shell version (SNOW-517).

    Server-renders the deployed ``CACHE_VERSION`` (read live from
    ``static/js/sw.js`` via ``apps.core.sw_shell.read_cache_version()``) and
    ``settings.APP_VERSION`` so the baseline works with JS disabled. The
    live SW version — what the browser actually has under control right
    now — is filled in by ``static/js/pwa_sw_version_probe.js`` as a
    progressive enhancement: it posts ``'version'`` to
    ``navigator.serviceWorker.controller`` and writes the reply into the
    page (see ``sw.js``'s ``message`` handler).
    """
    return render(
        request,
        "_debug/sw_version.html",
        {
            "cache_version": read_cache_version(),
            "app_version": settings.APP_VERSION,
        },
    )
