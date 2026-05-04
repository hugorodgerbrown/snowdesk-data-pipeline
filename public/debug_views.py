"""
public/debug_views.py — Staff-only design-system / component-library page.

Renders every design-system entry from ``public/design_tokens.py`` under
``/_components/``. Sidebar grouped into Foundations (design tokens) and
Components (rendered HTML partials); main column HTMX-swaps via the
sidebar.

Auth: ``staff_member_required`` only — no DEBUG gate. The page is
reachable in production by any staff user, by design (everyone with
admin access already has equivalent capability via Django admin).

The earlier ``header_combinations`` view at ``/debug/header/`` (SNOW-101,
shipped as part of SNOW-100) was retired by SNOW-110 — its visual matrix
now lives inside the component library as the **Weather header** entry
under the Components group.
"""

from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
from django.shortcuts import render

from pipeline.decorators import require_htmx
from public.design_tokens import LIBRARY_GROUPS, get_category

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
            "groups": LIBRARY_GROUPS,
            "active": get_category(DEFAULT_SLUG),
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
