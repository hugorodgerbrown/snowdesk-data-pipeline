"""
public/debug_views.py — Staff-only design-system / component-library page.

Renders every design token from ``src/css/main.css`` under ``/_components/``.
Sidebar nav + HTMX-swapped main panel. Foundations only at this stage;
HTML components (weather header, masthead, etc.) plug in later via SNOW-104.

Auth: ``staff_member_required`` only — no DEBUG gate. The page is reachable
in production by any staff user, by design (everyone with admin access
already has equivalent capability via Django admin).
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.shortcuts import render

from pipeline.decorators import require_htmx
from public.design_tokens import FOUNDATION_CATEGORIES, get_category

DEFAULT_SLUG = "typography"


@staff_member_required
def component_library(request: HttpRequest) -> HttpResponse:
    """Render the full component-library page with the default panel SSR.

    The default panel (typography) is rendered server-side so the URL is
    meaningful with JS off and so screen-reader users don't land on an
    empty main column.
    """
    return render(
        request,
        "_components/index.html",
        {
            "categories": FOUNDATION_CATEGORIES,
            "active": get_category(DEFAULT_SLUG),
        },
    )


@staff_member_required
@require_htmx
def component_library_panel(
    request: HttpRequest,
    slug: str,
) -> HttpResponse:
    """Return the inner-HTML for one foundation panel (HTMX-only).

    Unknown ``slug`` returns 404 — the URL is meant to be reached from
    the sidebar, where every entry corresponds to a real category.
    """
    category = get_category(slug)
    if category is None:
        return HttpResponseNotFound()
    return render(
        request,
        "_components/partials/_panel.html",
        {"active": category},
    )
