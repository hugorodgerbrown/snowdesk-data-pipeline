"""
apps/observations/urls.py — URL routing for the observations application.

Mounts the two HTMX partial endpoints under ``partials/report/`` so they
sit in the ``observations`` namespace and avoid collision with the generic
``<str:region_id>/`` catch-all in ``apps/public/urls.py``.

URL structure:
  partials/report/form/           GET  — observation-type form (after GPS fix)
  partials/report/                POST — submit a field report
  partials/report/list/           GET  — the user's own reports (SNOW-658)
  partials/report/<uuid>/delete/  POST — delete one of the user's own reports
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
    # SNOW-658: declared before the bare ``partials/report/`` POST below for
    # readability only — the two cannot collide, since this one carries a
    # trailing path segment the POST route's pattern does not match.
    path(
        "partials/report/list/",
        views.observation_list,
        name="list",
    ),
    path(
        "partials/report/<uuid:uuid>/delete/",
        views.observation_delete,
        name="delete",
    ),
    path(
        "partials/report/",
        views.report_submit,
        name="report_submit",
    ),
]
