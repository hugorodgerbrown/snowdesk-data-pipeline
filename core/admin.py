"""
core/admin.py — Django admin registrations for the core application.

Provides read-only inspection views for RequestLog so that operators can
diagnose request context captured at inflection points (sign-up, sign-in,
subscribe, share-click) without direct database access.

Also registers ``IdempotencyRecord`` — the cache table used by
``core.idempotency.IdempotencyMiddleware`` to deduplicate PWA mutation
retries. Operators occasionally need to inspect (or manually purge) a
stuck key; the admin surface makes that possible without shell access.
"""

import logging

from django.contrib import admin

from .models import IdempotencyRecord, RequestLog

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
        "subscriber",
        "created_at",
    ]
    list_filter = ["country_code", "method", "language"]
    search_fields = ["ip_address", "path", "subscriber__email", "city"]
    list_select_related = ["subscriber"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "subscriber",
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
    raw_id_fields = ["subscriber"]


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """Admin view for IdempotencyRecord — cached PWA mutation replies."""

    list_display = [
        "id",
        "method",
        "path",
        "response_status",
        "expires_at",
        "created_at",
    ]
    list_filter = ["method", "response_status"]
    search_fields = ["key", "path"]
    ordering = ["-created_at"]
    readonly_fields = [
        "uuid",
        "key",
        "method",
        "path",
        "response_status",
        "response_content_type",
        "response_body",
        "expires_at",
        "created_at",
        "updated_at",
    ]
