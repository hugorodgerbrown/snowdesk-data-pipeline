"""
apps/routes/admin.py — Django admin registration for the routes app.

Provides read-mostly admin views for ``Route`` and ``RouteShare`` rows — an
imported route is user-generated content and should not be edited by staff
except to delete spam or test data. Mirrors ``FavouriteAdmin``.

``points`` is deliberately absent from ``list_display``: it is the whole
geometry, up to a couple of thousand coordinates, and rendering it in a
changelist column would be unreadable and slow. The derived columns
(``distance_m``, ``ascent_m``, ``point_count``) are what a staff member
actually needs to identify a row.

SNOW-988 adds the one action on either model: downloading a route back out
as a ``.gpx``. It is a READ, which is why it sits on an otherwise
read-mostly admin without contradicting it — nothing about the row changes.
"""

import logging

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse

from .models import Route, RouteShare
from .services.gpx_export import build_gpx, gpx_filename

logger = logging.getLogger(__name__)


@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    """Read-mostly admin view for Route.

    Staff can search and filter routes, but all meaningful fields are
    read-only to preserve the integrity of user-generated content.
    """

    list_display = [
        "user",
        "name",
        "source_filename",
        "distance_m",
        "ascent_m",
        "started_at",
        "point_count",
        "source_point_count",
        "created_at",
    ]
    list_filter = ["created_at"]
    search_fields = [
        "user__email",
        "name",
        "source_filename",
    ]
    ordering = ["-created_at"]
    readonly_fields = [
        "id",
        "uuid",
        "user",
        "name",
        "source_filename",
        "points",
        "distance_m",
        "ascent_m",
        # descent_m was added by SNOW-686's follow-up and never reached
        # this list, leaving one derived figure editable while its pair
        # was not — every field here is read-only for the same reason.
        "descent_m",
        "started_at",
        "finished_at",
        "point_count",
        "source_point_count",
        "bounds",
        # SNOW-910. Read-only like the rest, though for a different
        # reason: this one is not the user's content but the sampler's
        # answer, and an edited copy would claim a steepness no terrain
        # tile ever reported.
        "slope_samples",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
    actions = ["download_as_gpx"]

    @admin.action(description="Download as GPX")
    def download_as_gpx(
        self, request: HttpRequest, queryset: QuerySet[Route]
    ) -> HttpResponse | None:
        """Stream one selected route back out as a ``.gpx`` file.

        ONE route, not a selection. An admin action that returns a response
        can return exactly one, and the alternatives — a zip, or silently
        taking the first row — are both worse than saying so: a zip is a
        second format to explain for a job that is almost always done one
        route at a time, and picking a row the operator did not choose is
        the kind of quiet guess this admin avoids everywhere else.

        What comes back is a reconstitution rather than the upload; see
        ``apps.routes.services.gpx_export`` for the three ways it differs
        and why the file states them itself.

        Args:
            request: The admin request.
            queryset: The selected routes.

        Returns:
            A GPX file response, or None when the selection was not a
            single route — in which case the operator is told why.

        """
        routes = list(queryset[:2])
        if len(routes) != 1:
            self.message_user(
                request,
                "Select exactly one route to download — a GPX file holds one "
                "track, and this action returns one file.",
                level=messages.WARNING,
            )
            return None

        route = routes[0]
        response = HttpResponse(
            build_gpx(route).encode("utf-8"),
            content_type="application/gpx+xml",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{gpx_filename(route)}"'
        )
        logger.info(
            "Route exported as GPX: uuid=%s points=%d by staff=%s",
            route.uuid,
            route.point_count,
            request.user.pk,
        )
        return response


@admin.register(RouteShare)
class RouteShareAdmin(admin.ModelAdmin):
    """Read-mostly admin view for RouteShare (SNOW-764).

    Read-mostly for the same reason ``RouteAdmin`` is: a share row records
    a grant a user made over their own data, and editing one after the fact
    would rewrite that record rather than correct it. Revoking a link is
    already expressible without a writable field — delete the row, or let
    it expire.

    ``claim_count`` and ``last_claimed_at`` are the columns a staff member
    reads when a user asks how far a link travelled, so both are on the
    changelist rather than only on the detail page.
    """

    list_display = [
        "token",
        "route",
        "created_by",
        "expires_at",
        "claim_count",
        "last_claimed_at",
        "created_at",
    ]
    list_filter = ["expires_at", "created_at"]
    search_fields = [
        "token",
        "created_by__email",
        "route__name",
    ]
    ordering = ["-created_at"]
    # Every field, as on RouteAdmin. ``route`` and ``created_by`` are here
    # rather than raw_id_fields because nothing on this page is editable —
    # a picker widget for a read-only value would offer an action that
    # cannot be taken.
    readonly_fields = [
        "id",
        "uuid",
        "token",
        "route",
        "created_by",
        "expires_at",
        "claim_count",
        "last_claimed_at",
        "created_at",
        "updated_at",
    ]
    date_hierarchy = "created_at"
