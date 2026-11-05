"""
apps/public/debug_views.py — Staff-only debug pages.

Hosts the Web Push demo (``/_push-demo/``), the SW shell-version page
(``/_sw-version/``, SNOW-517), one route's terrain per segment
(``/_route-terrain/<uuid>/``, SNOW-1020), the bulletin alert season replay
(``/_alerts-replay/``, SNOW-949) and the ingest report (``/_ingest/``,
SNOW-1423). The component library, the icon-set comparison and the error
previews moved to ``apps/design_system/views.py`` (SNOW-1427).

Auth: ``staff_member_required`` only — no DEBUG gate. Every page here is
reachable in production by any staff user, by design (everyone with
admin access already has equivalent capability via Django admin).
"""

import csv
import datetime
import uuid
from typing import Any

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from apps.accounts.push_config import VAPID_PUBLIC_KEY
from apps.bulletins.services.alerts import ALERT_TZ
from apps.bulletins.services.golden_week import SOURCES
from apps.bulletins.services.replay import DEFAULT_REGION, replay_archive
from apps.core.decorators import require_htmx
from apps.core.sw_shell import cache_version
from apps.public.ingest_report import WINDOWS, build_report, parse_window
from apps.routes.models import Route
from track.terrain_detail import COLUMNS, terrain_detail


@staff_member_required
def push_demo(request: HttpRequest) -> HttpResponse:
    """Spike demo page for Web Push notifications.

    SNOW-874: the stored-subscription list is no longer passed in here.
    Rendered server-side it was a snapshot of page-load time, which went
    stale the moment the tester clicked Enable push — leaving the page
    showing a live endpoint above and an hour-old one below. The client
    reads it from ``accounts:push_subscriptions`` instead.
    """
    return render(
        request,
        "_debug/push_demo.html",
        {"vapid_public_key": VAPID_PUBLIC_KEY},
    )


@staff_member_required
def alerts_replay(request: HttpRequest) -> HttpResponse:
    """Render a season of bulletin alerts for one region (SNOW-949).

    The same engine as ``replay_bulletin_alerts``: the committed archive,
    replayed through the live decision and wording, read-only. Query
    parameters pick the region (``region``, default CH-4115), provider
    (``source``), mode (``mode``) and whether the decisions that sent
    nothing are shown (``all=1``). Messages are grouped by week so a
    season reads as it would have arrived.
    """
    source = request.GET.get("source", "SLF").upper()
    if source not in SOURCES:
        source = "SLF"
    mode = request.GET.get("mode", "NEW_BULLETIN")
    if mode not in ("NEW_BULLETIN", "CHANGES"):
        mode = "NEW_BULLETIN"
    region_id = request.GET.get("region", DEFAULT_REGION).strip() or DEFAULT_REGION
    show_all = request.GET.get("all") == "1"
    alerts = replay_archive(source=source, region_id=region_id, mode=mode)
    weeks: dict[str, list[dict[str, Any]]] = {}
    for alert in alerts:
        if not alert.sent and not show_all:
            continue
        local = alert.delivered_at.astimezone(ALERT_TZ)
        monday = local.date() - datetime.timedelta(days=local.weekday())
        weeks.setdefault(monday.isoformat(), []).append(
            {"alert": alert, "when": local.strftime("%a %d %b %H:%M")}
        )
    return render(
        request,
        "_debug/alerts_replay.html",
        {
            "weeks": weeks,
            "region_id": region_id,
            "source": source,
            "mode": mode,
            "show_all": show_all,
            "sources": SOURCES,
            "sent": sum(1 for a in alerts if a.sent),
            "skipped": sum(1 for a in alerts if not a.sent),
        },
    )


def _ingest_report_context(request: HttpRequest) -> dict[str, Any] | None:
    """Build the ingest report for ``?window=``, or None for an unknown window."""
    try:
        window = parse_window(request.GET.get("window"))
    except ValueError:
        return None
    return {"report": build_report(window, now=timezone.now()), "windows": WINDOWS}


@staff_member_required
def ingest_report(request: HttpRequest) -> HttpResponse:
    """Staff page: what each scheduled feed pulled in, live (SNOW-1423).

    ``?window=`` is ``1h``, ``24h`` (the default) or ``7d``; anything else
    is a 400. The panel re-fetches itself every minute from
    ``ingest_report_panel``.
    """
    context = _ingest_report_context(request)
    if context is None:
        return HttpResponseBadRequest("Unknown window.")
    return render(request, "_debug/ingest_report.html", context)


@staff_member_required
@require_htmx
def ingest_report_panel(request: HttpRequest) -> HttpResponse:
    """HTMX fragment: the ingest report's panel, for its one-minute refresh."""
    context = _ingest_report_context(request)
    if context is None:
        return HttpResponseBadRequest("Unknown window.")
    return render(request, "_debug/_ingest_report_panel.html", context)


@staff_member_required
def sw_version(request: HttpRequest) -> HttpResponse:
    """Staff page surfacing the service-worker shell version (SNOW-517).

    Server-renders the deployed ``CACHE_VERSION`` (derived live from the
    shell content hash via ``apps.core.sw_shell.cache_version()`` — the
    same value ``serve_sw`` injects into ``/sw.js``, recomputed here rather
    than read from the process cache so the page never shows a stale name)
    and ``settings.APP_VERSION`` so the baseline works with JS disabled. The
    live SW version — what the browser actually has under control right
    now — is filled in by ``static/js/pwa_sw_version_probe.js`` as a
    progressive enhancement: it posts ``'version'`` to
    ``navigator.serviceWorker.controller`` and writes the reply into the
    page (see ``sw.js``'s ``message`` handler).

    SNOW-585: also passes ``settings.SW_DEV_SHELL_BYPASS`` so the template
    can render the dev-only "restore shell cache" opt-in toggle only when
    the bypass is active — the toggle is meaningless in production, where
    the bypass is never on.
    """
    return render(
        request,
        "_debug/sw_version.html",
        {
            "cache_version": cache_version(),
            "app_version": settings.APP_VERSION,
            "sw_dev_shell_bypass": settings.SW_DEV_SHELL_BYPASS,
        },
    )


@staff_member_required
def route_terrain(request: HttpRequest, route_uuid: uuid.UUID) -> HttpResponse:
    """One route's slope angle, aspect, bearing and track gradient per segment.

    SNOW-1020. The rows come from
    ``track.terrain_detail.terrain_detail`` and nothing here
    derives a figure of its own. ``?format=csv`` returns the same rows as
    a download, so the table and the file cannot disagree, and
    ``?format=json`` returns them as JSON, for scripts. The map's route
    panel reads the same rows from the owner-scoped ``routes:terrain``
    (SNOW-1146), so this page stays staff only.

    Any route, not only the viewer's own: this is staff-only, and staff
    already read ``slope_samples`` in the Route admin, which links here.
    No row carries a coordinate — distances and bearings only.

    Args:
        request: The incoming request.
        route_uuid: The route's public ``uuid``.

    Returns:
        The rendered table, or the CSV. 404 for an unknown route. A route
        that has never been sampled renders an empty table and says so.

    """
    route = get_object_or_404(Route, uuid=route_uuid)
    rows = terrain_detail(route.slope_samples, route.points) or []

    if request.GET.get("format") == "json":
        # One row per segment by index, for scripts. The route panel's
        # terrain data reads routes:terrain instead (SNOW-1146).
        return JsonResponse({"rows": rows})

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="route-{route.uuid}-terrain.csv"'
        )
        writer = csv.DictWriter(response, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        return response

    return render(
        request,
        "_debug/route_terrain.html",
        {
            "route": route,
            "columns": COLUMNS,
            "rows": [[row[column] for column in COLUMNS] for row in rows],
            "sampled": route.slope_samples is not None,
        },
    )
