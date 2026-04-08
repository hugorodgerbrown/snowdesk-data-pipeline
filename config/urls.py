"""
config/urls.py — Root URL configuration.

Mounts the Django admin and delegates all application routing to the
pipeline app's own urls.py.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("pipeline.urls")),
]
