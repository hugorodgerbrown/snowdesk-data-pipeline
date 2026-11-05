"""
apps/design_system/views.py — The staff-only component library pages.

``/_components/`` renders every design-system entry from
``apps/design_system/design_tokens.py``: a sidebar grouped into Foundations
(design tokens) and Components (rendered HTML partials), with the main
column HTMX-swapped from the sidebar. ``/_icon-sets/`` compares the weather
icon sets (SNOW-791), and ``/_errors/<kind>/`` previews each error page
(SNOW-1151).

Moved out of ``apps/public/debug_views.py`` by SNOW-1427
(docs/decisions/public-splits-by-surface.md). The URL names stay
``public:…``: ``apps/public/urls.py`` routes these views, and it is the only
module outside this app that imports it.

Auth: ``staff_member_required`` only — no DEBUG gate, as for every staff
debug page.
"""

import logging

from django.contrib.admin.views.decorators import staff_member_required
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
)
from django.shortcuts import render

from apps.core.decorators import require_htmx
from apps.design_system.design_tokens import LIBRARY_GROUPS, get_category
from apps.ingest.public import WMO_CODE_TO_ICON_BUCKET
from apps.public.errors import ERROR_PAGES, error_response
from apps.weather.icon_sets import (
    ICON_SETS,
    LOCAL_SET_SOURCES,
    available_icon_sets,
)
from apps.weather.services.weather_display import (
    _ICON_BUCKET_LABEL,
    WEATHER_ICON_BUCKETS,
    weather_icon_filename,
)

logger = logging.getLogger(__name__)

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
def error_preview(request: HttpRequest, kind: str) -> HttpResponse:
    """Render one error page exactly as its handler would (SNOW-1151).

    The handlers only run with ``DEBUG`` off, so a development build never
    shows them; this route does, in every environment. The response carries
    the error's real status and goes through the same negotiation, so an
    ``Accept: application/json`` or ``HX-Request`` preview shows those
    shapes too.

    Args:
        request: The staff request.
        kind: A key of ``apps.public.errors.ERROR_PAGES`` — ``400``,
            ``403``, ``csrf``, ``404`` or ``500``.

    Returns:
        The error response for ``kind``.

    Raises:
        Http404: ``kind`` is not a known error page.

    """
    if kind not in ERROR_PAGES:
        raise Http404(kind)
    return error_response(request, kind)


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
    the readable form of ``WMO_CODE_TO_ICON_BUCKET``. A bucket with a
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
    for code, bucket in sorted(WMO_CODE_TO_ICON_BUCKET.items()):
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
