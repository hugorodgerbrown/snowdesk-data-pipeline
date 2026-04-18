"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the subscriptions flow under /subscribe/, the JSON
API under /api/, and the public-facing bulletin site at the root.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("subscribe/", include("subscriptions.urls")),
    path("api/", include("public.api_urls")),
    path("", include("public.urls")),
]
