"""
apps/public/debug_views.py — Staff-only design-system / debug pages.

Renders every design-system entry from ``apps/public/design_tokens.py`` under
``/_components/``. Sidebar grouped into Foundations (design tokens) and
Components (rendered HTML partials); main column HTMX-swaps via the
sidebar. Also hosts the Web Push demo (``/_push-demo/``), the SW
shell-version page (``/_sw-version/``, SNOW-517), and the icon-set
comparison grid (``/_icon-sets/``, SNOW-791).

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
from apps.core.sw_shell import cache_version
from apps.public.design_tokens import LIBRARY_GROUPS, get_category
from apps.weather.icon_sets import (
    ICON_SETS,
    LOCAL_SET_SOURCES,
    available_icon_sets,
)
from apps.weather.services.weather_display import (
    _ICON_BUCKET_LABEL,
    _WMO_CODE_TO_ICON_BUCKET,
    WEATHER_ICON_BUCKETS,
    weather_icon_filename,
)

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

    Server-renders the deployed ``CACHE_VERSION`` (derived live from the
    shell content hash via ``apps.core.sw_shell.cache_version()`` — the
    same value ``serve_sw`` injects into ``/sw.js``, recomputed here rather
    than read from the process cache so the page never shows a stale name)
    and ``settings.APP_VERSION`` so the baseline works with JS disabled. The
    live SW version — what the browser actually has under control right
    now — is filled in by ``static/js/pwa_sw_version_probe.js`` as a
    progressive enhancement: it posts ``'version'`` to
    ``navigator.serviceWorker.controller`` and writes the reply into the
    page (see ``sw.js``'s ``message`` handler).

    SNOW-585: also passes ``settings.SW_DEV_SHELL_BYPASS`` so the template
    can render the dev-only "restore shell cache" opt-in toggle only when
    the bypass is active — the toggle is meaningless in production, where
    the bypass is never on.
    """
    return render(
        request,
        "_debug/sw_version.html",
        {
            "cache_version": cache_version(),
            "app_version": settings.APP_VERSION,
            "sw_dev_shell_bypass": settings.SW_DEV_SHELL_BYPASS,
        },
    )


@staff_member_required
def icon_set_comparison(request: HttpRequest) -> HttpResponse:
    """Every icon bucket against every candidate set, side by side.

    SNOW-791 is choosing between several drawings of the same twelve
    buckets. Judging them needs all of it at once — every bucket, every
    set, at the sizes the app actually renders — and a screenshot of that
    goes stale the moment one of the sets is edited. This reads what is on
    disk, so it is never out of date.

    Rows are the twelve buckets in ``WEATHER_ICON_BUCKETS`` order, each
    labelled with the WMO codes that resolve to it — so the grid doubles as
    the readable form of ``_WMO_CODE_TO_ICON_BUCKET``. A bucket with a
    day/night pair contributes two rows; the ten that ship a single drawing
    contribute one.

    Columns are the sets ``available_icon_sets`` finds on disk. ``meteoswiss``
    and ``bbc`` are gitignored, so they appear only where someone has
    populated them — and the page carries the recipe for doing that
    (``LOCAL_SET_SOURCES``), marking which are currently absent.

    Args:
        request: The incoming request (unused — the grid is derived from
            the registry and the filesystem, not from the request).

    Returns:
        The rendered comparison page.

    """
    codes: dict[str, list[int]] = {}
    for code, bucket in sorted(_WMO_CODE_TO_ICON_BUCKET.items()):
        codes.setdefault(bucket, []).append(code)

    sets = available_icon_sets()
    rows: list[dict[str, object]] = []
    for bucket in WEATHER_ICON_BUCKETS:
        day = weather_icon_filename(bucket, "day")
        night = weather_icon_filename(bucket, "night")
        # Ten of the twelve resolve to one drawing at either hour; naming a
        # variant there would invent a distinction the set does not draw.
        variants = [("day", day), ("night", night)] if day != night else [("", day)]
        for variant, filename in variants:
            rows.append(
                {
                    "bucket": bucket,
                    "label": _ICON_BUCKET_LABEL[bucket],
                    "codes": codes.get(bucket, []),
                    "variant": variant,
                    "filename": filename,
                    "paths": [ICON_SETS[name] + filename for name in sets],
                }
            )

    return render(
        request,
        "_components/icon_sets.html",
        {
            "rows": rows,
            "sets": sets,
            "sizes": (27, 40, 72),
            # Only the sets that are missing here need rebuilding; showing the
            # recipe for one already on disk is noise.
            "sources": [
                {"name": name, "missing": name not in sets, **LOCAL_SET_SOURCES[name]}
                for name in LOCAL_SET_SOURCES
            ],
            "icon_root": "static/icons/weather",
            "missing_any": any(n not in sets for n in LOCAL_SET_SOURCES),
        },
    )
