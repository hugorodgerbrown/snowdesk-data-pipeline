"""
apps/analytics/urls.py — URL routing for the analytics app.

Mounted at ``/api/telemetry`` from ``config/urls.py`` (the include
strips that prefix, so the empty-string path matches the endpoint
exactly). Kept in its own namespace ``analytics:`` so future analytics
endpoints don't collide with the public site's ``api:`` namespace.
"""

from django.urls import path

from . import views

app_name = "analytics"

urlpatterns = [
    path("", views.telemetry_receive, name="telemetry"),
]
