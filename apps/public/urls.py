"""
apps/public/urls.py — URL routing for the public bulletin site.

URL structure:
  /                                            Interactive map homepage (canonical).
  /map/                                        Permanent 301 redirect to /.
  /terms/                                      SLF data-licence acknowledgement
                                               + Snowdesk liability disclaimer.
  /help/                                        Plain-language "how it works"
                                               help page (SNOW-456).
  /observations/                                Signed-in stream of recent
                                               field observations (SNOW-476).
  /resorts/<id>/<slug>/                         Resort detail page — danger
                                               chip, bulletin link, favourite
                                               toggle (SNOW-504).
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

All three forms are served by ``bulletin_detail``. The ``lowercase_region_id``
decorator on ``bulletin_detail`` (and the other region-accepting views)
301-redirects any mixed-case ``region_id`` to its canonical lowercase form
before any view logic runs. Form 3 (with an explicit date) additionally 302s
to the canonical form ``/<canonical_region_id>/<name_slug>/<date>/`` when
the inbound path doesn't already match. Every render emits a
``<link rel="canonical">`` pointing at the form-3 canonical URL so SEO
collapses all three forms into one indexed destination.

``/map/`` is a permanent (301) redirect to ``/`` so existing bookmarks and
inbound links are preserved; query strings (e.g. ``?d=``) are forwarded.
The name ``map`` is kept so ``{% url 'public:map' %}`` still resolves
(to the redirect URL) and existing reverse calls continue to work.

The ``/terms/`` and ``/examples/`` routes are registered before
the generic ``<region_id:region_id>/<slug:slug>/`` pattern so Django's URL
resolver matches the literal suffixes first.
"""

from django.urls import path
from django.views.generic import RedirectView

from . import debug_views, views
from .feeds import CountryBulletinFeed

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    # SNOW-344: /map/ is now a permanent redirect to /; the name is kept so
    # existing reverse() calls and {% url 'public:map' %} still resolve.
    path(
        "map/",
        RedirectView.as_view(url="/", permanent=True, query_string=True),
        name="map",
    ),
    # SLF data-licence acknowledgement page — registered before generic
    # <str:region_id>/ patterns so "terms" never resolves as a region_id.
    path("terms/", views.terms, name="terms"),
    # Technology credits and attribution page (SNOW-122).
    path("colophon/", views.colophon, name="colophon"),
    # Legal pages — registered before generic <str:region_id>/ patterns
    # so these slugs never resolve as region IDs (SNOW-153).
    path("privacy/", views.privacy, name="privacy"),
    path("terms-of-service/", views.terms_of_service, name="terms_of_service"),
    # Static reference guide: how to read the SLF avalanche bulletin.
    path(
        "how-to-read-a-bulletin/",
        views.how_to_read_bulletin,
        name="how_to_read_bulletin",
    ),
    # Plain-language "how it works" help page (SNOW-456) — registered before
    # the generic <region_id:region_id>/ patterns so "help" never resolves
    # as a region id.
    path("help/", views.help_page, name="help"),
    # Recent field-observations stream (SNOW-476) — registered before the
    # generic <region_id:region_id>/ patterns so "observations" never
    # resolves as a region id.
    path("observations/", views.observations_list, name="observations"),
    # Resort detail page (SNOW-504) — registered before the generic
    # <region_id:region_id>/ patterns so "resorts" never resolves as a
    # region id (though the RegionIdConverter regex already rejects it,
    # since it requires a two-letter-country-code + digit prefix).
    path(
        "resorts/<int:resort_id>/<slug:slug>/",
        views.resort_detail,
        name="resort",
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
    # Web Push spike — staff-only demo page.
    path("_push-demo/", debug_views.push_demo, name="push_demo"),
    # SW shell cache-version page — staff-only, surfaces live vs deployed
    # CACHE_VERSION (SNOW-517).
    path("_sw-version/", debug_views.sw_version, name="sw_version"),
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

urlpatterns += [
    # Per-country RSS/Atom feed (SNOW-396). Registered before the generic
    # <region_id:region_id>/ catch-all so `<country>/feed.rss` is never
    # mistaken for a region_id + slug pair. The region_id converter regex
    # requires a dash after the two-letter country code, so a bare
    # `ch`/`at`/`it`/`fr` first segment can't match it, but explicit
    # ordering keeps the intent visible.
    path(
        "<str:country>/feed.rss",
        CountryBulletinFeed(),
        name="country_bulletin_feed",
    ),
    # Share-redirect (SNOW-217) — registered before the generic
    # <str:region_id>/ catch-alls so "/s/<token>/" is never swallowed.
    path(
        "s/<str:token>/",
        views.share_redirect,
        name="share_redirect",
    ),
    # Season calendar partial — HTMX-deferred heatmap grid (SNOW-170).
    # Registered before the generic <region_id:region_id>/ pattern for the same reason.
    path(
        "partials/season/<region_id:region_id>/",
        views.season_calendar_partial,
        name="season_partial",
    ),
    # Bulletin pages — three forms, all served by ``bulletin_detail``.
    # Forms 1 + 2 default to today and render in place; form 3 redirects
    # to canonical when the URL components don't match.
    path(
        "<region_id:region_id>/",
        views.bulletin_detail,
        name="region_root",
    ),
    path(
        "<region_id:region_id>/<slug:slug>/",
        views.bulletin_detail,
        name="bulletin",
    ),
    path(
        "<region_id:region_id>/<slug:slug>/<str:date_str>/",
        views.bulletin_detail,
        name="bulletin_date",
    ),
]
