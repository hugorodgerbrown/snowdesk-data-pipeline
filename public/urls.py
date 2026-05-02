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
  /<region_id>/                                302 → /<region_id>/<slug>/<today>/.
  /<region_id>/<slug>/                         302 → /<region_id>/<slug>/<today>/.
  /<region_id>/<slug>/<date>/                  Canonical bulletin URL — renders
                                               the bulletin for that date.

Forms 1 (``/<region_id>/``) and 2 (``/<region_id>/<slug>/``) both 302 to
the canonical form-3 URL with today's date defaulted in. The form-3 URL
is the only pattern that lands on ``bulletin_detail``, and is the single
URL advertised via ``<link rel="canonical">`` on the page itself.

The ``/map/``, ``/terms/`` and ``/examples/`` routes are registered before
the generic ``<str:region_id>/<slug:slug>/`` pattern so Django's URL
resolver matches the literal suffixes first.
"""

from django.urls import path

from . import views

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
    # Examples — sample bulletin links rendered inline using the canonical view.
    path("examples/random/", views.examples_random, name="examples_random"),
    path(
        "examples/category/<str:danger_level>/",
        views.examples_category,
        name="examples_category",
    ),
    # Deprecated — redirect to /examples/random/
    path("random/", views.random_redirect, name="random"),
    # Bulletin pages — in increasing order of specificity.
    # Forms 1 + 2 redirect to the canonical form-3 URL (today's date).
    # The ``bulletin`` URL name resolves to the form-2 redirect entry — every
    # internal href that wants the canonical URL must use ``bulletin_date``.
    path(
        "<str:region_id>/",
        views.region_redirect,
        name="region_redirect",
    ),
    path(
        "<str:region_id>/<slug:slug>/",
        views.region_slug_redirect,
        name="bulletin",
    ),
    path(
        "<str:region_id>/<slug:slug>/<str:date_str>/",
        views.bulletin_detail,
        name="bulletin_date",
    ),
]
