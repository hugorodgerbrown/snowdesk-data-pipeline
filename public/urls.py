"""
public/urls.py — URL routing for the public bulletin site.

URL structure:
  /                                            Marketing homepage.
  /map/                                        Interactive region-choropleth map.
  /terms/                                      SLF data-licence acknowledgement
                                               + Snowdesk liability disclaimer.
  /examples/random/                            Renders a random bulletin inline
                                               using the canonical view.
  /examples/category/<danger_level>/           Renders a random bulletin matching
                                               the given danger level inline.
  /random/                                     Deprecated → /examples/random/.
  /<region_id>/                                Renders today's bulletin in place.
  /<region_id>/<slug>/                         Renders today's bulletin in place.
  /<region_id>/<slug>/<date>/                  Renders that day's bulletin; 302s
                                               to the canonical form when the
                                               URL components don't match.

All three forms are served by ``bulletin_detail``. Forms 1 and 2 default
to today's date and render in place — they never redirect even when the
URL casing or slug is non-canonical. Form 3 (with an explicit date) 302s
to the canonical form ``/<canonical_region_id>/<name_slug>/<date>/`` when
the inbound path doesn't already match. Every render emits a
``<link rel="canonical">`` pointing at the form-3 canonical URL so SEO
collapses all three forms into one indexed destination.

The ``/map/``, ``/terms/`` and ``/examples/`` routes are registered before
the generic ``<str:region_id>/<slug:slug>/`` pattern so Django's URL
resolver matches the literal suffixes first.
"""

from django.conf import settings
from django.urls import path

from . import debug_views, views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("map/", views.map_view, name="map"),
    # SLF data-licence acknowledgement page — registered before generic
    # <str:region_id>/ patterns so "terms" never resolves as a region_id.
    path("terms/", views.terms, name="terms"),
    # Calendar partial — registered before generic <str:region_id>/ patterns.
    path(
        "partials/calendar/<str:region_id>/<int:year>/<int:month>/",
        views.calendar_partial,
        name="calendar_partial",
    ),
    # Component library — staff-only design-system page (SNOW-103).
    # Underscore prefix follows the project convention for staff-only routes.
    path(
        "_components/",
        debug_views.component_library,
        name="components_index",
    ),
    path(
        "partials/_components/<slug:slug>/",
        debug_views.component_library_panel,
        name="components_panel",
    ),
    # Examples — sample bulletin links rendered inline using the canonical view.
    path("examples/random/", views.examples_random, name="examples_random"),
    path(
        "examples/category/<str:danger_level>/",
        views.examples_category,
        name="examples_category",
    ),
    # Deprecated — redirect to /examples/random/
    path("random/", views.random_redirect, name="random"),
]

# Staff-only design-debug pages, only mounted in DEBUG builds. The view
# is itself decorated with @_require_debug + @staff_member_required, so
# even a stray production import would render 404 / login-redirect rather
# than the markup. See public/debug_views.py.
if settings.DEBUG:
    urlpatterns.append(
        path(
            "debug/header/",
            debug_views.header_combinations,
            name="debug_header",
        ),
    )

urlpatterns += [
    # Bulletin pages — three forms, all served by ``bulletin_detail``.
    # Forms 1 + 2 default to today and render in place; form 3 redirects
    # to canonical when the URL components don't match.
    path(
        "<str:region_id>/",
        views.bulletin_detail,
        name="region_root",
    ),
    path(
        "<str:region_id>/<slug:slug>/",
        views.bulletin_detail,
        name="bulletin",
    ),
    path(
        "<str:region_id>/<slug:slug>/<str:date_str>/",
        views.bulletin_detail,
        name="bulletin_date",
    ),
]
