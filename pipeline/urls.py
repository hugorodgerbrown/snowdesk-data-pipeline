"""
pipeline/urls.py — URL routing for the pipeline application.

Full-page routes are at the root. HTMX partial/fragment routes are grouped
under the `partials/` prefix and should only be called by HTMX (enforced
in views.py via the require_htmx decorator).
"""

from django.urls import path

from . import views

app_name = "pipeline"

urlpatterns = [
    # Full-page views
    path("", views.dashboard, name="dashboard"),
    # HTMX partial views
    path("partials/bulletins/", views.bulletins_partial, name="bulletins-partial"),
    path("partials/runs/", views.runs_partial, name="runs-partial"),
]
