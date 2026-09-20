"""
apps/trips/admin.py — Django admin registration for the trips app.

Read-mostly admin views for ``Trip`` and ``TripParticipant``. Both are
user-generated content — somebody's plan and somebody's acceptance of it —
and should not be edited by staff except to delete test data. Mirrors
``RouteAdmin`` and ``DownloadAreaAdmin``.

``points`` and ``bounds`` are absent from ``list_display``: a JSON blob in a
changelist column identifies nothing a staff member could act on. The date,
the time and the organiser are what name a row.

EVERY FIELD IN ``_SNAPSHOT_FIELDS`` BELONGS IN ``readonly_fields``. The
snapshot is copied from the source route at creation and never re-read, so
an editable one could be saved to a value no track ever held — silently,
because nothing goes back to the route to notice. ``readonly_fields`` is an
explicit list and does not grow on its own; ``duration`` and
``slope_samples`` were each added to the snapshot without it.
``tests/trips/test_admin.py`` asserts the pairing against the tuple itself,
so the next one fails a test rather than shipping editable.
"""

import logging

from django.contrib import admin

from .models import Trip, TripParticipant

logger = logging.getLogger(__name__)


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    """Read-mostly admin view for Trip."""

    list_display = [
        "created_by",
        "name",
        "route_name",
        "date",
        "start_time",
        "point_count",
        "created_at",
    ]
    # ``created_by`` is in list_display and its __str__ is a query, so the
    # changelist would otherwise cost one per row.
    list_select_related = ["created_by"]
    list_filter = ["date", "created_at"]
    search_fields = [
        "created_by__email",
        "name",
        "route_name",
    ]
    readonly_fields = [
        "created_by",
        "route",
        "meeting_point",
        "date",
        "start_time",
        "name",
        "description",
        "points",
        "bounds",
        "distance_m",
        "ascent_m",
        "descent_m",
        "duration",
        "point_count",
        "route_name",
        "slope_samples",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-created_at"]


@admin.register(TripParticipant)
class TripParticipantAdmin(admin.ModelAdmin):
    """Read-mostly admin view for TripParticipant."""

    list_display = ["trip", "user", "joined_at"]
    list_filter = ["joined_at"]
    search_fields = ["user__email", "trip__name"]
    readonly_fields = [
        "trip",
        "user",
        "joined_at",
        "uuid",
        "created_at",
        "updated_at",
    ]
    ordering = ["-joined_at"]
