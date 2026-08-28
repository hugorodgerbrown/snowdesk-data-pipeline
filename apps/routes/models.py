"""
apps/routes/models.py — Database models for the routes application.

Defines ``Route``: a polyline an authenticated user imported from a GPX
file. The row stores the coordinate list itself (``points``) plus the
derived figures the list rows and the later fit-to-bounds need
(``distance_m``, ``ascent_m``, ``point_count``, ``bounds``) plus the
recording's two ends (``started_at``, ``finished_at``), so neither
surface ever has to re-walk the geometry.

The uploaded ``.gpx`` is parsed and discarded — there is no ``FileField``
here and no copy of the original on disk. See
``docs/decisions/gpx-uploads-are-parsed-not-stored.md`` for why.

Parsing, simplification and the derived figures live in
``apps/routes/services/gpx.py``; the mutating entry points (per-user cap,
create, delete) live in ``apps/routes/services/routes.py``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class RouteQuerySet(models.QuerySet["Route"]):
    """Custom queryset for Route."""

    def for_user(self, user: "User") -> "RouteQuerySet":
        """Return routes owned by the given user.

        Args:
            user: The user to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(user=user)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class Route(BaseModel):
    """A polyline imported from a user-uploaded GPX file.

    ``points`` is the geometry, stored as ``[[lon, lat, ele], …]`` in
    **GeoJSON axis order** (longitude first, RFC 7946) so it can be handed
    to a MapLibre ``LineString`` source without a per-render swap. ``ele``
    is metres above sea level, or ``null`` for a point whose ``<ele>`` the
    source file omitted. The list is the *simplified* track — see
    ``apps.routes.services.gpx.parse_gpx`` for the point bound and why it
    exists.

    ``name`` defaults to the name carried inside the GPX (``<trk><name>``
    and friends) and is user-editable afterwards; it may be blank, in
    which case the view layer falls back to ``source_filename``.

    ``source_filename`` is the name of the uploaded file, kept purely as a
    label — the file itself is never written to disk.

    ``distance_m``, ``ascent_m`` and ``descent_m`` are derived at ingest
    from the **full-resolution** track, before simplification, so a coarser
    stored geometry never shortens the reported distance or flattens either
    the climb or the drop. ``ascent_m`` and ``descent_m`` are null — not
    zero — when the source file carries no elevation data at all: "we don't
    know" and "flat" are different facts, and rendering the second for the
    first would be a safety-relevant lie. They are never netted against
    each other: an out-and-back carries its full climb AND its full drop.

    ``descent_m`` was added after ``ascent_m`` had already been shipping.
    Rows created before it could not be given a full-resolution figure —
    the source ``.gpx`` is parsed and discarded, so the only geometry left
    for them is the simplified ``points``. Their backfilled ``descent_m``
    is therefore measured on that coarser track; see
    ``apps/routes/migrations/0002_route_descent_m.py`` for what that costs.

    ``started_at`` and ``finished_at`` are the first and last track
    point's own ``<time>``, and their span is the tour's **elapsed**
    time — a track paused for lunch or overnight counts the pause.
    Moving time would need a timestamp on every stored point (SNOW-751).
    They are null on the same rule as ``ascent_m``: a planned ``<rte>``, a
    planning tool's export, and an unparseable series all mean "we don't
    know", never "instantaneous". The pair is read and validated
    together, so one is never set without the other.

    Rows created before SNOW-750 keep both as null and cannot be
    backfilled. ``descent_m`` could be recovered from the simplified
    ``points`` when it was added late; timing cannot, because the
    uploaded ``.gpx`` is parsed and discarded and no copy of it survives.

    ``point_count`` and ``bounds`` describe what is actually stored in
    ``points``: the count is ``len(points)``, and ``bounds`` is a GeoJSON
    bbox — ``[min_lon, min_lat, max_lon, max_lat]`` — over the stored
    coordinates, so a fit-to-bounds frames exactly the line that gets
    drawn.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="routes",
        help_text="User who uploaded this route.",
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Route label — seeded from the GPX's own name, editable by the "
            "owner afterwards."
        ),
    )
    source_filename = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Name of the uploaded .gpx file. A label only — the file itself "
            "is parsed and discarded, never stored."
        ),
    )
    points = models.JSONField(
        help_text=(
            "The simplified track as [[lon, lat, ele], …] in GeoJSON axis "
            "order. ele is metres, or null where the source had no <ele>."
        ),
    )
    distance_m = models.FloatField(
        help_text=(
            "Great-circle length of the full-resolution track in metres, "
            "derived at ingest before simplification."
        ),
    )
    ascent_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Total positive elevation gain in metres over the "
            "full-resolution track. Null — not zero — when the source file "
            "carries no elevation data."
        ),
    )
    descent_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Total elevation loss in metres over the full-resolution "
            "track, as a positive magnitude. Null on the same condition as "
            "ascent_m — the two are read off one elevation series and are "
            "always known or unknown together."
        ),
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "The first track point's own <time>. Null when the source file "
            "carried no usable timing."
        ),
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "The last track point's own <time>. Null on the same condition "
            "as started_at — the pair is read and validated together."
        ),
    )
    point_count = models.PositiveIntegerField(
        help_text="Number of coordinates stored in points.",
    )
    bounds = models.JSONField(
        help_text=(
            "GeoJSON bbox over the stored points: [min_lon, min_lat, max_lon, max_lat]."
        ),
    )

    objects = RouteQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]

    @property
    def distance_km(self) -> float:
        """Return ``distance_m`` in kilometres.

        A display helper — metres is the stored unit because that is what
        the maths produces, but kilometres is what a route is read in.
        """
        return self.distance_m / 1000

    @property
    def duration(self) -> timedelta | None:
        """Return the recording's elapsed time, or ``None`` if untimed.

        Elapsed, not moving: the span includes every stop the recording
        sat through. ``None`` means the file carried no usable timing, and
        callers must omit the figure rather than render a zero — the same
        contract ``ascent_m``'s null carries.
        """
        if self.started_at is None or self.finished_at is None:
            return None
        return self.finished_at - self.started_at

    def to_string(self) -> str:
        """Return a concise human-readable description of this route.

        Format: ``"{user} — {name or source_filename} ({distance} km)"``
        """
        label = self.name or self.source_filename or "Untitled route"
        return f"{self.user} — {label} ({self.distance_km:.1f} km)"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
