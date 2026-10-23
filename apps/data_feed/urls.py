"""
apps/data_feed/urls.py — URL routing for the data feed, under ``/api/data/v1/``.

Included from ``apps/public/api_urls.py`` at the ``/api/`` root, so this
module owns the ``data/v1/`` prefix. Each feed is its own path rather than
a ``<str:name>`` catch-all, so an unknown feed is the URLconf's 404 and
``api:data_feed:<name>`` reverses.

The version is in the path because a service client's token is bound to a
URL prefix (RFC 8707): a client's token for ``/api/data/v1/`` will not open
a ``/api/data/v2/`` that changes the shapes under it.
"""

from django.urls import path

from apps.data_feed import views
from apps.data_feed.contract import FEEDS

app_name = "data_feed"

urlpatterns = [
    path(f"data/v1/{name}/", views.feed, {"name": name}, name=name) for name in FEEDS
]
