"""
config/urls.py — Root URL configuration.

Mounts the Django admin, the internal pipeline dashboard under /dashboard/,
and the public-facing bulletin site at the root.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("dashboard/", include("pipeline.urls")),
    path("subscribe/", include("subscriptions.urls")),
    path("", include("public.urls")),
]
