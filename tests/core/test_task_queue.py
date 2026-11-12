"""
tests/core/test_task_queue.py — task_queue_health (SNOW-1423).

Moved from tests/mcp_server/test_ops_tools.py when the function moved to
apps.core.task_queue, so the ingest report can share it. The summary counts
every failure in the window, not just the ten groups listed (SNOW-1477).
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

    def test_summary_counts_every_failure_not_just_ten_groups(self) -> None:
        """Eleven kinds of failure: ten groups listed, eleven counted."""
        from django_tasks_db.models import DBTaskResult

        now = timezone.now()
        common = {
            "args_kwargs": {"args": [], "kwargs": {}},
            "backend_name": "default",
            "run_after": now,
            "traceback": "",
            "status": "FAILED",
            "task_path": "apps.x.send",
        }
        for i in range(11):
            DBTaskResult.objects.create(
                finished_at=now, exception_class_path=f"x.Error{i}", **common
            )
        DBTaskResult.objects.create(
            finished_at=now - dt.timedelta(hours=25),
            exception_class_path="x.Old",
            **common,
        )
        tasks = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}
        with override_settings(TASKS=tasks):
            result = task_queue_health(now=now)

        assert len(result["recent_failures"]) == 10
        assert "11 failure(s) in 24h" in result["summary"]
        assert set(result) == {
            "backend",
            "by_status",
            "oldest_ready_age_seconds",
            "recent_failures",
            "summary",
        }
