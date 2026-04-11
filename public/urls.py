"""
public/urls.py — URL routing for the public bulletin site.

Four routes:
  /                          Home — redirects to a random region's latest
                             bulletin.
  /<region_id>/random/       Compact card list showing the most recent
                             bulletins for a single region (one per day, in
                             reverse chronological order). Accepts an
                             optional ``?b=N`` query parameter to override
                             the number of cards shown (default 10).
  /<zone>/                   Naked zone — redirects to /<zone>/<name>/ using
                             a cached lookup so repeat visits skip the DB.
  /<zone>/<name>/            Bulletin viewer for a specific region slug.

The ``<region_id>/random/`` route is registered before the generic
``<slug:zone>/<slug:name>/`` pattern so Django's URL resolver matches it as
a concrete path rather than treating ``random`` as a name slug.
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
    path("<slug:zone>/<slug:name>/", views.bulletin_detail, name="bulletin"),
    path("<slug:zone>/", views.zone_redirect, name="zone_redirect"),
]
