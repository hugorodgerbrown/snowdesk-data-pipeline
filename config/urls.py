"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the subscriptions flow under /subscribe/, the JSON
API under /api/, and the public-facing bulletin site at the root.

The ``/sw.js`` route is registered before ``public.urls`` so the generic
``<str:region_id>/`` pattern in public.urls does not swallow it.
"""

from django.contrib import admin
from django.urls import include, path

from public.views import serve_sw

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("subscriptions.urls")),
    path("api/", include("public.api_urls")),
    path("sw.js", serve_sw, name="service_worker"),
    path("", include("public.urls")),
]
