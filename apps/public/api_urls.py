"""
apps/public/api_urls.py — URL routing for the public JSON API.

Mounted at ``/api/`` from ``config/urls.py``. Kept separate from
``apps/public/urls.py`` so the page-serving routes and the JSON endpoints
don't share a namespace — ``{% url "api:ratings" %}`` vs
``{% url "public:bulletin_date" %}``.
"""

from django.urls import include, path

from . import api

app_name = "api"

urlpatterns = [
    # PWA shell contract — server-authoritative version + kill-switch state
    # consumed by the client on cold start / every 15 min while active
    # (spec §5.2 / §5.10 / SNOW-369 / SNOW-372).
    path("version", api.version, name="version"),
    path("sw-config", api.sw_config, name="sw_config"),
    # Share-create endpoint (SNOW-217). POST only; returns a tokenised short URL.
    path("bulletins/share/", api.share_create, name="share_create"),
    # Per-bulletin JSON endpoints (SNOW-394). Two representations of the
    # same stored bulletin — the render model (canonical presentation
    # shape) and the raw CAAML in its GeoJSON Feature envelope. Both are
    # advertised on the bulletin HTML page via ``<link rel="alternate"
    # type="application/json">`` so LLM crawlers and generic fetchers
    # can pivot from HTML to structured data in one hop. The
    # ``.caaml.json`` suffix distinguishes the raw endpoint from the
    # render-model endpoint at the URL level (rather than via
    # ``Accept``) so both are independently discoverable and cacheable.
    path(
        "bulletins/<str:bulletin_id>/",
        api.bulletin_render_model,
        name="bulletin_render_model",
    ),
    path(
        "bulletins/<str:bulletin_id>.caaml.json",
        api.bulletin_caaml,
        name="bulletin_caaml",
    ),
    # SNOW-239: unified ratings endpoint. Replaces today-summaries + season-ratings.
    # Accepts optional ?d=YYYY-MM-DD and ?country=ch|fr|at|it filters.
    path("ratings/", api.ratings, name="ratings"),
    path("resorts-by-region/", api.resorts_by_region, name="resorts_by_region"),
    path("resorts.geojson", api.resorts_geojson, name="resorts_geojson"),
    path("regions.geojson", api.regions_geojson, name="regions_geojson"),
    # SNOW-761: the map's Weather overlay. ONE feed, anchored on Location —
    # it replaces the resort-anchored /api/forecast-weather.geojson and the
    # region-anchored /api/region-weather.geojson that SNOW-762 removed.
    # Filtered by Location.objects.public(), never active(): the latter also
    # reaches favourites, and a public feed built from it leaks private pins.
    path("weather.geojson", api.weather_geojson, name="weather_geojson"),
    # SNOW-761: the sheet body behind a tap on a weather symbol. Keyed on
    # Location like the feed it is tapped from, and 404s for anything
    # outside Location.objects.public() — see the view.
    path(
        "weather/<int:location_id>/detail/",
        api.weather_detail,
        name="weather_detail",
    ),
    path(
        "major-regions.geojson",
        api.major_regions_geojson,
        name="major_regions_geojson",
    ),
    path(
        "sub-regions.geojson",
        api.sub_regions_geojson,
        name="sub_regions_geojson",
    ),
    # SNOW-521: full precomputed basemap_download blob (incl. z tile
    # ranges) for one MicroRegion, fetched on demand when the
    # #region-readout download icon is clicked.
    path(
        "region-basemap-tiles/",
        api.region_basemap_tiles,
        name="region_basemap_tiles",
    ),
    # SNOW-323: dissolved bulletin grouping boundaries keyed by date.
    # The whole-season payload is cached client-side by map.js.
    path(
        "bulletin-groupings.geojson",
        api.bulletin_groupings_geojson,
        name="bulletin_groupings_geojson",
    ),
    path(
        "region/<region_id:region_id>/summary/",
        api.region_summary,
        name="region_summary",
    ),
    # SNOW-499: minimal resort-pin popup — public (unlike favourites.geojson),
    # per-user favourite star state resolved inline; never cached.
    path(
        "resorts/<int:resort_id>/popup/",
        api.resort_popup,
        name="resort_popup",
    ),
    # SNOW-419: community-reports overlay — anonymised, clustered
    # FieldObservation pins from the last 48 hours. The view inline-gates
    # on the ``community_reports`` waffle flag and 404s when inactive.
    path(
        "community-reports.geojson",
        api.community_reports_geojson,
        name="community_reports_geojson",
    ),
    # SNOW-74 — edit-resorts mode endpoints. Always registered; the
    # views inline-gate on ``request.user.is_superuser`` (SNOW-724) and
    # 404 for everyone else, so non-superusers see the same response
    # shape they did when this was DEBUG-only.
    path(
        "edit/resorts/queue/",
        api.edit_resorts_queue,
        name="edit_resorts_queue",
    ),
    # Literal segment, declared before the ``<int:resort_id>`` pattern so
    # the two can never be confused by a future non-numeric converter.
    path(
        "edit/resorts/create/",
        api.edit_resort_create,
        name="edit_resort_create",
    ),
    path(
        "edit/resorts/<int:resort_id>/save/",
        api.edit_resort_save,
        name="edit_resort_save",
    ),
    # SNOW-755 — edit-locations mode endpoints, gated the same way. The
    # literal segments ("create", "links") are declared before the
    # ``<int:...>`` patterns for the same reason the resort routes above
    # are, and ``links/`` is its own prefix because unlink names the LINK,
    # not the location: a location reached by four resorts has four links
    # to choose between, and the pair of ids that would otherwise be
    # needed is exactly what the link row's own id already is.
    path(
        "edit/locations/queue/",
        api.edit_locations_queue,
        name="edit_locations_queue",
    ),
    path(
        "edit/locations/create/",
        api.edit_location_create,
        name="edit_location_create",
    ),
    path(
        "edit/locations/links/<int:link_id>/unlink/",
        api.edit_location_unlink,
        name="edit_location_unlink",
    ),
    path(
        "edit/locations/<int:location_id>/save/",
        api.edit_location_save,
        name="edit_location_save",
    ),
    path(
        "edit/locations/<int:location_id>/link/",
        api.edit_location_link,
        name="edit_location_link",
    ),
    # SNOW-391: hosted MCP (Model Context Protocol) JSON-RPC endpoint.
    # Final URLs: POST /api/mcp/ (canonical, reverses as api:mcp:endpoint)
    # and POST /api/mcp (slash-less alias). Mounted at "" so apps.mcp_server.urls
    # owns the "mcp" path segment and can register both spellings — see the
    # module docstring there for why the slash-less alias exists.
    path("", include(("apps.mcp_server.urls", "mcp"), namespace="mcp")),
]
