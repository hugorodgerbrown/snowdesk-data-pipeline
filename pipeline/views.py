"""
pipeline/views.py — HTTP views for the pipeline application.

Full-page views return a complete HTML response. HTMX partial views return
only a fragment (no layout wrapper) and are restricted to HTMX requests via
the require_htmx decorator.

View responsibilities are kept minimal: read query params, fetch data via
the ORM or service layer, and render a template. No business logic here.
"""

import logging
from datetime import date

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import Bulletin, PipelineRun

logger = logging.getLogger(__name__)


def require_htmx(view_func):
    """
    Decorator that returns 400 Bad Request for non-HTMX requests.

    Partial/fragment views should only be called by HTMX. This decorator
    enforces that, preventing full-page responses from being accidentally
    bypassed.

    Args:
        view_func: The view function to wrap.

    Returns:
        Wrapped view that returns 400 for non-HTMX requests.
    """

    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        """Check the request is an HTMX request before delegating."""
        if not request.htmx:  # type: ignore[attr-defined]
            logger.warning("Non-HTMX request to partial view %s", request.path)
            return HttpResponse("HTMX requests only.", status=400)
        return view_func(request, *args, **kwargs)  # type: ignore[no-any-return]

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    """
    Render the main dashboard page.

    Displays recent pipeline runs and the latest bulletins. The page
    uses HTMX to auto-refresh the tables without a full reload.

    Args:
        request: The incoming HTTP request.

    Returns:
        Rendered dashboard HTML response.
    """
    recent_runs = PipelineRun.objects.order_by("-started_at")[:10]
    latest_bulletins = Bulletin.objects.order_by("-issued_at")[:50]

    context = {
        "recent_runs": recent_runs,
        "latest_bulletins": latest_bulletins,
        "today": date.today(),
    }
    logger.debug("Rendering dashboard for %s", request.user)
    return render(request, "pipeline/dashboard.html", context)


@require_GET
@require_htmx
def bulletins_partial(request: HttpRequest) -> HttpResponse:
    """
    Return an HTMX fragment containing the bulletins table.

    Accepts an optional `date` query parameter (YYYY-MM-DD) to filter
    bulletins by issued_at date. Called by HTMX to refresh the table
    without a full page load.

    Args:
        request: The incoming HTTP GET request (must be HTMX).

    Returns:
        Rendered HTML fragment for the bulletins table.
    """
    date_filter = request.GET.get("date")
    bulletins_qs = Bulletin.objects.order_by("-issued_at")

    if date_filter:
        try:
            filter_date = date.fromisoformat(date_filter)
            bulletins_qs = bulletins_qs.filter(issued_at__date=filter_date)
        except ValueError:
            logger.warning("Invalid date filter: %s", date_filter)

    context = {"bulletins": bulletins_qs[:100]}
    return render(request, "pipeline/partials/bulletins_table.html", context)


@require_GET
@require_htmx
def runs_partial(request: HttpRequest) -> HttpResponse:
    """
    Return an HTMX fragment containing the recent pipeline runs table.

    Called by HTMX to poll for run-status updates without a full reload.

    Args:
        request: The incoming HTTP GET request (must be HTMX).

    Returns:
        Rendered HTML fragment for the pipeline runs table.
    """
    recent_runs = PipelineRun.objects.order_by("-started_at")[:10]
    context = {"recent_runs": recent_runs}
    return render(request, "pipeline/partials/runs_table.html", context)
