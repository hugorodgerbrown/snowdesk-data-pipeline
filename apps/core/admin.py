"""
apps/core/admin.py — Django admin registrations for the core application.

Provides read-only inspection views for RequestLog so that operators can
diagnose request context captured at inflection points (sign-up, sign-in,
subscribe, share-click) without direct database access.

Also registers ``IdempotencyRecord`` — the cache table used by
``apps.core.idempotency.IdempotencyMiddleware`` to deduplicate PWA mutation
retries. Operators occasionally need to inspect (or manually purge) a
stuck key; the admin surface makes that possible without shell access.

And ``CommandRun`` — the management command run register (SNOW-1189). Read
only: a row is a record of what happened, written by the command itself.
"""

import logging

from django.contrib import admin
from django.http import HttpRequest

from .models import CommandRun, IdempotencyRecord, RequestLog

logger = logging.getLogger(__name__)


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    """Admin view for RequestLog — read-only diagnostic surface."""

    list_display = [
        "id",
        "method",
        "path",
        "ip_address",
        "country_code",
        "city",
        "language",
        "account",
        "created_at",
    ]
    list_filter = ["country_code", "method", "language"]
    search_fields = ["ip_address", "path", "account__user__email", "city"]
    list_select_related = ["account", "account__user"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "account",
        "session_key",
        "method",
        "path",
        "referer",
        "user_agent",
        "ip_address",
        "country_code",
        "subdivision_code",
        "city",
        "latitude",
        "longitude",
        "accuracy_radius_km",
        "accept_language",
        "language",
        "created_at",
        "updated_at",
    ]
    raw_id_fields = ["account"]


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """Admin view for IdempotencyRecord — cached PWA mutation replies."""

    list_display = [
        "id",
        "method",
        "path",
        "status",
        "response_status",
        "expires_at",
        "created_at",
    ]
    list_filter = ["status", "method", "response_status"]
    search_fields = ["key", "path"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "key",
        "method",
        "path",
        "status",
        "response_status",
        "response_content_type",
        "response_body",
        "expires_at",
        "created_at",
        "updated_at",
    ]


@admin.register(CommandRun)
class CommandRunAdmin(admin.ModelAdmin):
    """Admin view for CommandRun — the read-only command run register."""

    list_display = [
        "id",
        "command",
        "mode",
        "status",
        "invoked_via",
        "operator",
        "environment",
        "release",
        "started_at",
        "finished_at",
    ]
    list_filter = ["status", "mode", "invoked_via", "environment", "release", "command"]
    search_fields = ["command", "argv", "operator", "git_sha", "task_id"]
    date_hierarchy = "started_at"
    ordering = ["-started_at"]
    list_select_related = ["parent"]
    readonly_fields = [
        "uuid",
        "command",
        "app_label",
        "arguments",
        "argv",
        "mode",
        "status",
        "exit_code",
        "started_at",
        "finished_at",
        "counts",
        "output_tail",
        "error",
        "invoked_via",
        "operator",
        "reason",
        "task_id",
        "host",
        "service",
        "instance",
        "environment",
        "git_sha",
        "release",
        "parent",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Refuse: a run is recorded by the command, never typed in."""
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: CommandRun | None = None
    ) -> bool:
        """Refuse: a recorded run is never edited."""
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: CommandRun | None = None
    ) -> bool:
        """Refuse: the register is an audit trail; only prune_command_runs trims it."""
        return False
