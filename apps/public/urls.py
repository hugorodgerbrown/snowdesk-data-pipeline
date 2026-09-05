"""
apps/public/urls.py — URL routing for the public bulletin site.

URL structure:
  /                                            Interactive map homepage (canonical).
  /map/                                        Permanent 301 redirect to /.
  /terms/                                      Permanent 301 redirect to
                                               /terms-of-service/ (SNOW-770).
  /help/                                        Plain-language "how it works"
                                               help page (SNOW-456).
  /compare/                                     Public comparison of the
                                               avalanche apps in this
                                               category (SNOW-836).
  /observations/                                Permanent 301 redirect to
                                               /?panel=reports — the map with
                                               the reports sheet open (SNOW-804).
  /resorts/<slug>/                             Resort detail page — danger
                                               chip, bulletin link, favourite
                                               toggle (SNOW-504; slug-keyed
                                               since SNOW-796).
  /resorts/<id>/<slug>/                         Permanent 301 redirect to the
                                               slug-keyed page (SNOW-796).
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
    # SNOW-770 merged the old /terms/ page into /terms-of-service/. The URL
    # stays registered — and stays HERE, ahead of the generic
    # <str:region_id>/ patterns, or "terms" starts resolving as a region_id
    # — so inbound links and anything indexed keep working. The name is kept
    # so existing reverse() calls still resolve.
    path(
        "terms/",
        RedirectView.as_view(
            pattern_name="public:terms_of_service",
            permanent=True,
            query_string=True,
        ),
        name="terms",
    ),
    # Technology credits and attribution page (SNOW-122).
    path("colophon/", views.colophon, name="colophon"),
    # Public comparison of the avalanche apps in this category (SNOW-836).
    # Registered here, ahead of the generic <region_id:region_id>/ patterns,
    # so "compare" never resolves as a region id.
    path("compare/", views.compare, name="compare"),
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
    # SNOW-804: the recent-observations stream (SNOW-476) was a filtered
    # view of map data — the map has the layer, the sheet and the submit
    # flow — so the page is gone and the URL 301s to the map with the
    # reports sheet open (``?panel=`` is consumed by static/js/map.js).
    # Registered here, ahead of the generic <region_id:region_id>/ patterns,
    # so "observations" never resolves as a region id; the name is kept so
    # an old reverse still resolves. Mirrors the /map/ redirect above.
    path(
        "observations/",
        RedirectView.as_view(url="/?panel=reports", permanent=True, query_string=True),
        name="observations",
    ),
    # Resort detail page (SNOW-504) — registered before the generic
    # <region_id:region_id>/ patterns so "resorts" never resolves as a
    # region id (though the RegionIdConverter regex already rejects it,
    # since it requires a two-letter-country-code + digit prefix).
    path(
        "resorts/<slug:slug>/",
        views.resort_detail,
        name="resort",
    ),
    # SNOW-796: the pre-slug shape. Indexed and bookmarked, so it stays as
    # a permanent redirect to the slug-keyed page — the ADR's "every changed
    # route keeps a permanent redirect from its integer form".
    path(
        "resorts/<int:resort_id>/<slug:slug>/",
        views.resort_legacy_redirect,
        name="resort_legacy",
    ),
    # SNOW-761: the full forecast for one Location — the page the map's
    # weather card hands off to via "View forecast". Keyed on Location
    # rather than on a resort or a region because most of the estate is
    # neither: 461 of 540 public locations are region centroids, which had
    # no page of their own before this one. SNOW-797: keyed on the opaque
    # short id, not the pk; the integer form keeps a permanent redirect.
    # The int route is listed first, but the two cannot compete anyway —
    # the short_id converter wants exactly eleven characters.
    path(
        "weather/<int:location_id>/",
        views.location_weather_legacy_redirect,
        name="location_weather_legacy",
    ),
    path(
        "weather/<short_id:short_id>/",
        views.location_weather,
        name="location_weather",
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
    # SNOW-791: every icon bucket against every candidate set, side by side.
    # A working surface for choosing a set; goes with the switcher.
    path(
        "_icon-sets/",
        debug_views.icon_set_comparison,
        name="icon_sets",
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
