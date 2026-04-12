"""
public/urls.py — URL routing for the public bulletin site.

URL structure:
  /                                   Marketing homepage.
  /random/                            Redirects to a random region's today page.
  /<region_id>/season/                Full-season test page (up to 100 panels).
  /<region_id>/                       Redirects to /<region_id>/<slug>/.
  /<region_id>/<slug>/                Today's bulletin for a region.
  /<region_id>/<slug>/<date>/         Bulletin for a region on a specific date.

The ``/random/`` and ``/<region_id>/season/`` routes are registered before
the generic ``<str:region_id>/<slug:slug>/`` pattern so Django's URL
resolver matches the literal suffixes first.
"""

from django.urls import path

from . import views

app_name = "public"

urlpatterns = [
    path("", views.home, name="home"),
    path("random/", views.random_redirect, name="random"),
    path(
        "<str:region_id>/season/",
        views.season_bulletins,
        name="season_bulletins",
    ),
    path(
        "<str:region_id>/recent/",
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
