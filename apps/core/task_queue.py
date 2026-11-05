"""
apps/core/task_queue.py — the background task queue's health (SNOW-1423).

``task_queue_health`` reports the database task backend's backlog and the
past day's failures. It lives here, in no app's domain, because two readers
need it: the ops MCP server's ``task_queue_health`` tool and the staff
ingest report (``apps.public.ingest_report``). Keeping it out of
``apps.mcp_server`` lets the report import it without a cycle, since the ops
server imports the report.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

#: The window counted as "recent" for failures.
RECENT: dt.timedelta = dt.timedelta(hours=24)


def task_queue_health(*, now: dt.datetime | None = None) -> dict[str, Any]:
    """Return the background task queue's backlog and recent failures.

    Only the database backend (production) keeps rows. Elsewhere tasks run
    inline in the request, so there is no queue to report.

    Args:
        now: The reference time; defaults to now. A test seam.

    Returns:
        ``backend``, and for the database backend ``by_status``,
        ``oldest_ready_age_seconds`` and ``recent_failures``; plus
        ``summary``.

    """
    backend = settings.TASKS["default"]["BACKEND"]
    if backend != "django_tasks_db.DatabaseBackend":
        return {
            "backend": backend,
            "summary": f"Tasks run inline ({backend.rsplit('.', 1)[-1]}); no queue.",
        }
    # Imported here: the app is installed everywhere, but only this branch
    # reads its table.
    from django_tasks_db.models import DBTaskResult

    now = now or timezone.now()
    by_status = dict(DBTaskResult.objects.values_list("status").annotate(n=Count("id")))
    oldest = (
        DBTaskResult.objects.ready()
        .order_by("enqueued_at")
        .values_list("enqueued_at", flat=True)
    ).first()
    failures = (
        DBTaskResult.objects.failed()
        .filter(finished_at__gte=now - RECENT)
        .values("task_path", "exception_class_path")
        .annotate(n=Count("id"))
        .order_by("-n")[:10]
    )
    oldest_age = round((now - oldest).total_seconds()) if oldest else None
    failed_24h = sum(row["n"] for row in failures)
    return {
        "backend": backend,
        "by_status": by_status,
        "oldest_ready_age_seconds": oldest_age,
        "recent_failures": list(failures),
        "summary": (
            f"{by_status.get('READY', 0)} ready, oldest "
            f"{oldest_age if oldest_age is not None else 0}s; "
            f"{failed_24h} failure(s) in 24h."
        ),
    }
