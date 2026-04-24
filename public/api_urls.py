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
    path("resorts-by-region/", api.resorts_by_region, name="resorts_by_region"),
    path("regions.geojson", api.regions_geojson, name="regions_geojson"),
    path(
        "region/<str:region_id>/summary/",
        api.region_summary,
        name="region_summary",
    ),
    path(
        "offline-manifest/map/",
        api.offline_manifest_map,
        name="offline_manifest_map",
    ),
]
