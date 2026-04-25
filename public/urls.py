"""
public/urls.py — URL routing for the public bulletin site.

URL structure:
  /                                            Marketing homepage.
  /map/                                        Interactive region-choropleth map.
  /terms/                                      SLF data-licence acknowledgement
                                               + Snowdesk liability disclaimer.
  /examples/random/                            Redirects to a random bulletin.
  /examples/category/<danger_level>/           Redirects to a random bulletin
                                               matching the given danger level.
  /random/                                     Deprecated → /examples/random/.
  /<region_id>/season/                         Full-season page (up to 100 panels).
  /<region_id>/history/                        Recent bulletin history for a region.
  /<region_id>/                                Redirects to /<region_id>/<slug>/.
  /<region_id>/<slug>/                         Today's bulletin for a region.
  /<region_id>/<slug>/<date>/                  Bulletin for a specific date.

The ``/map/``, ``/terms/``, ``/examples/`` and ``/<region_id>/season/``
routes are registered before the generic ``<str:region_id>/<slug:slug>/``
pattern so Django's URL resolver matches the literal suffixes first.
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
]

# SNOW-45 scrubber perf spike — DEBUG-only harness page. Registered BEFORE
# the generic ``<str:region_id>/<slug>/`` patterns below, otherwise the
# URL resolver would match ``/debug/scrubber-perf/`` as a bulletin lookup
# (region_id="debug", slug="scrubber-perf") and 404 it. View also
# inline-gates on ``settings.DEBUG`` as a belt-and-braces fallback.
if settings.DEBUG:
    urlpatterns += [
        path(
            "debug/scrubber-perf/",
            debug_views.scrubber_perf,
            name="debug_scrubber_perf",
        ),
    ]

urlpatterns += [
    # Examples — sample bulletin links
    path("examples/random/", views.examples_random, name="examples_random"),
    path(
        "examples/category/<str:danger_level>/",
        views.examples_category,
        name="examples_category",
    ),
    # Deprecated — redirect to /examples/random/
    path("random/", views.random_redirect, name="random"),
    path(
        "<str:region_id>/season/",
        views.season_bulletins,
        name="season_bulletins",
    ),
    path(
        "<str:region_id>/history/",
        views.random_bulletins,
        name="random_bulletins",
    ),
    path(
        "<str:region_id>/",
        views.region_redirect,
        name="region_redirect",
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
