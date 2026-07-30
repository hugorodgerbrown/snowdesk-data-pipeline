"""
apps/observations/urls.py — URL routing for the observations application.

Mounts the two HTMX partial endpoints under ``partials/report/`` so they
sit in the ``observations`` namespace and avoid collision with the generic
``<str:region_id>/`` catch-all in ``apps/public/urls.py``.

URL structure:
  partials/report/form/   GET  — observation-type form (after GPS fix)
  partials/report/        POST — submit a field report
"""

from django.urls import path

from . import views

app_name = "observations"

urlpatterns = [
    path(
        "partials/report/form/",
        views.report_form,
        name="report_form",
    ),
    path(
        "partials/report/",
        views.report_submit,
        name="report_submit",
    ),
]
