"""
tests/core/test_task_queue.py — task_queue_health (SNOW-1423).

Moved from tests/mcp_server/test_ops_tools.py when the function moved to
apps.core.task_queue, so the ingest report can share it.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.core.task_queue import task_queue_health


@pytest.mark.django_db
class TestTaskQueueHealth:
    """task_queue_health: the inline backends have no queue."""

    def test_inline_backend(self) -> None:
        """Under the test settings tasks run inline."""
        result = task_queue_health()
        assert "no queue" in result["summary"]
        assert "by_status" not in result

    def test_database_backend(self) -> None:
        """The database backend reports backlog and recent failures."""
        from django_tasks_db.models import DBTaskResult

        now = timezone.now()
        common = {
            "args_kwargs": {"args": [], "kwargs": {}},
            "backend_name": "default",
            "run_after": now,
            "exception_class_path": "",
            "traceback": "",
        }
        DBTaskResult.objects.create(status="READY", task_path="apps.x.send", **common)
        DBTaskResult.objects.create(
            status="FAILED",
            task_path="apps.x.send",
            finished_at=now,
            **{**common, "exception_class_path": "smtplib.SMTPException"},
        )
        tasks = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}
        with override_settings(TASKS=tasks):
            result = task_queue_health(now=now + dt.timedelta(seconds=30))

        assert result["by_status"] == {"READY": 1, "FAILED": 1}
        assert result["oldest_ready_age_seconds"] == 30
        assert result["recent_failures"][0]["exception_class_path"] == (
            "smtplib.SMTPException"
        )
        assert "1 failure(s)" in result["summary"]
