"""
public/urls.py — URL routing for the public bulletin site.

Three routes:
  /                  Home — redirects to a random region's latest bulletin.
  /<zone>/           Naked zone — redirects to /<zone>/<name>/ using a cached
                     lookup so repeat visits skip the database.
  /<zone>/<name>/    Bulletin viewer for a specific region slug.
"""

from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("<slug:zone>/<slug:name>/", views.bulletin_detail, name="bulletin"),
    path("<slug:zone>/", views.zone_redirect, name="zone_redirect"),
]
