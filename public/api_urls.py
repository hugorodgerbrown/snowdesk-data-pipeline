"""
public/api_urls.py — URL routing for the public JSON API.

Mounted at ``/api/`` from ``config/urls.py``. Kept separate from
``public/urls.py`` so the page-serving routes and the JSON endpoints
don't share a namespace — ``{% url "api:today_summaries" %}`` vs
``{% url "public:bulletin" %}``.
"""

from django.urls import path

from . import api

app_name = "api"

urlpatterns = [
    path("today-summaries/", api.today_summaries, name="today_summaries"),
    path("season-ratings/", api.season_ratings, name="season_ratings"),
    path("resorts-by-region/", api.resorts_by_region, name="resorts_by_region"),
    path("resorts.geojson", api.resorts_geojson, name="resorts_geojson"),
    path("regions.geojson", api.regions_geojson, name="regions_geojson"),
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
    path(
        "region/<str:region_id>/summary/",
        api.region_summary,
        name="region_summary",
    ),
    # SNOW-74 — edit-resorts mode endpoints. Always registered; the
    # views inline-gate on the ``edit_map`` waffle flag (SNOW-86) and
    # 404 when it is inactive for the request user, so non-superusers
    # see the same response shape they did when this was DEBUG-only.
    path(
        "edit/resorts/queue/",
        api.edit_resorts_queue,
        name="edit_resorts_queue",
    ),
    path(
        "edit/resorts/<int:resort_id>/coords/",
        api.edit_resort_save_coords,
        name="edit_resort_save_coords",
    ),
]
