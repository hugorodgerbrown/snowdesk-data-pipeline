"""
public/urls.py — URL routing for the public bulletin site.

Five routes:
  /                          Home — redirects to a random region's latest
                             bulletin.
  /<region_id>/random/       Compact card list showing the most recent
                             bulletins for a single region (one per day, in
                             reverse chronological order). Accepts an
                             optional ``?b=N`` query parameter to override
                             the number of cards shown (default 10).
  /<region_id>/season/       Full-season test page — up to 100 bulletin
                             panels in a responsive grid (three columns on
                             desktop, single column on mobile).
  /<zone>/                   Naked zone — redirects to /<zone>/<name>/ using
                             a cached lookup so repeat visits skip the DB.
  /<zone>/<name>/            Bulletin viewer for a specific region slug.

The ``<region_id>/random/`` and ``<region_id>/season/`` routes are
registered before the generic ``<slug:zone>/<slug:name>/`` pattern so
Django's URL resolver matches the literal suffix rather than treating
``random`` or ``season`` as a name slug.
"""

from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path(
        "<str:region_id>/random/",
        views.random_bulletins,
        name="random_bulletins",
    ),
    path(
        "<str:region_id>/season/",
        views.season_bulletins,
        name="season_bulletins",
    ),
    path("<slug:zone>/<slug:name>/", views.bulletin_detail, name="bulletin"),
    path("<slug:zone>/", views.zone_redirect, name="zone_redirect"),
]
