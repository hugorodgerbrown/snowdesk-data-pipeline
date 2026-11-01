"""
apps/routes/models.py — Database models for the routes application.

Defines four models:

- ``Route``: a polyline an authenticated user imported from a GPX file or,
  since SNOW-1255, drew on the map (``source`` says which).
  The row stores the coordinate list itself (``points``) plus the derived
  figures the list rows and the later fit-to-bounds need (``distance_m``,
  ``ascent_m``, ``point_count``, ``bounds``) plus the recording's two ends
  (``started_at``, ``finished_at``), so neither surface ever has to
  re-walk the geometry.
- ``RouteShare`` (SNOW-764): a tokenised link that lets the owner hand one
  of their routes to another account, which claims a COPY of it.

- ``SkiSegment`` and ``SkiTour`` (SNOW-1276): this instance's read-only
  copy of swisstopo's ski-touring network and of SAC's named tours over
  it — reference data, not user data. Each is keyed by the
  reference-data service's uuid (``source_uuid``) and filled by
  ``import_ski_tours`` (the service's own host) or ``pull_reference_data``
  (every other instance), never edited here. Each row carries its line's
  bounding box, so "what crosses this map view" is four comparisons.

The uploaded ``.gpx`` is parsed and discarded — there is no ``FileField``
here and no copy of the original on disk. See
``docs/decisions/gpx-uploads-are-parsed-not-stored.md`` for why.

Parsing, simplification and the derived figures live in
``apps/routes/services/gpx.py``; the mutating entry points (per-user cap,
create, delete) live in ``apps/routes/services/routes.py``, and the
sharing half in ``apps/routes/services/shares.py``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.durations import split_hours_minutes
from apps.core.models import BaseModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from track.terrain_heights import ClimbFigures


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

    ``source_point_count`` (SNOW-988) is the OTHER count: how many
    coordinates the uploaded file carried, before
    ``apps.routes.services.gpx._simplify`` ran. Equal to ``point_count``
    on every track under ``MAX_POINTS``, because simplification returns
    such a track untouched; larger on one that was thinned. Storing both
    is what makes "is the point cap ever binding, and on how many routes"
    a query rather than an argument — a row with 894 points might have
    been recorded at 894 or cut down from 30,000, and until this column
    existed the two read identically.

    **Null means WE DID NOT RECORD IT**, which is neither zero nor
    ``point_count``. Rows created before this column cannot be backfilled:
    the uploaded ``.gpx`` is parsed and discarded, so the figure is gone
    for them. Same contract as ``ascent_m``'s null — a missing measurement
    is not a measurement of nothing.

    ``slope_samples`` (SNOW-910) is how steep the GROUND the track crosses
    is, sampled from the terrain grid at a fixed stride. It is NEVER
    derived from ``points``' own third ordinate, even though that would be
    free: a skin track zigzagging up a 38° face rises about 15° along its
    own length, and a rising traverse across one rises near zero. Painting
    either of those green is the failure the whole feature exists to
    avoid. See ``apps.routes.services.slope_segments`` for the record's
    shape and the stride.

    **Null means NEVER SAMPLED.** That is a different fact from a segment
    inside the record whose ``unknown`` reason is set, which means the
    terrain was asked and had no answer there, and the two must never
    render alike: one is a route nothing has looked at yet, the other is
    ground no survey covers. All three of
    ``track.terrain.TerrainUnknown``'s reasons are kept
    per segment even though the map collapses them to one treatment,
    because the module's contract is that a reason is never reduced to a
    null and SNOW-911 and SNOW-839 read the same record.
    """

    class Source(models.TextChoices):
        """How a route reached the account (SNOW-1255).

        ``GPX`` is an uploaded file. ``DRAWN`` is a line of waypoints the
        owner placed on the map, joined by straight segments. A drawn row
        leaves every RECORDED field null — ``ascent_m``, ``descent_m``,
        ``started_at``, ``finished_at``, ``source_point_count`` and each
        point's ``ele`` — because nothing was recorded: its heights and its
        climb come from the terrain model through ``slope_samples``, the
        same path an altimeter-less upload already takes. See
        docs/decisions/a-drawn-route-is-a-route.md.
        """

        GPX = "GPX", "GPX upload"
        DRAWN = "DRAWN", "Drawn on the map"
        # SNOW-1278: a copy of one of SAC's tours (``SkiTour``). Its line is
        # the tour's, so it leaves the recorded fields null as a drawn row
        # does, and ``ski_tour_uuid`` names the tour it came from.
        SKI_TOUR = "SKI_TOUR", "SAC route"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="routes",
        help_text="User who uploaded this route.",
    )
    source = models.CharField(
        max_length=8,
        choices=Source.choices,
        default=Source.GPX,
        help_text=(
            "How the route arrived: an uploaded GPX file, or waypoints drawn "
            "on the map. A drawn route carries no recorded heights or times."
        ),
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "Route label — seeded from the GPX's own name, editable by the "
            "owner afterwards."
        ),
    )
    ski_tour_uuid = models.UUIDField(
        null=True,
        blank=True,
        help_text=(
            "The SAC tour this route was copied from (SkiTour.source_uuid), "
            "kept so a later correction to the tour can be offered. Null for "
            "every other route."
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
    crossed_regions = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The MicroRegion.region_ids the track crosses, in the order it "
            "first enters each (SNOW-945). Null until resolved; [] when the "
            "track is outside coverage."
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
    source_point_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "How many coordinates the uploaded file carried, before "
            "simplification. Null means we did not record it — not zero, and "
            "not the same as point_count."
        ),
    )
    bounds = models.JSONField(
        help_text=(
            "GeoJSON bbox over the stored points: [min_lon, min_lat, max_lon, max_lat]."
        ),
    )
    slope_samples = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Steepness of the GROUND the track crosses, sampled from the "
            "terrain grid at a fixed stride — never derived from the "
            "track's own elevation. Null means NEVER SAMPLED, which is not "
            "the same fact as a segment whose unknown reason is set (that "
            "is ground the survey does not cover); the two must never "
            "render alike. Carries a 'summary' key holding the same walk "
            "in figures (track/slope_summary.py), written "
            "by the sampler and recomputed on read when a record predates "
            "it."
        ),
    )
    track_render = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Everything the map, the route panel and the point card draw "
            "this track from — the terrain track, ascent and descent, "
            "slope classes, legs, terrain figures and point-card rows — "
            "built once by apps/routes/services/track_render.py whenever "
            "slope_samples is written, and only read after (SNOW-1309). "
            "Null or an old 'v' means not built yet: readers build it in "
            "memory until rebuild_track_renders --commit runs."
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

    @property
    def duration_hm(self) -> dict[str, str] | None:
        """Return the elapsed time split for display, or ``None`` if untimed.

        A display helper in the same family as ``distance_km``. The rule
        itself — whole minutes rounded half up, the empty hours string
        under an hour, the padded minutes above one — lives in
        ``apps.core.durations.split_hours_minutes`` because ``Trip`` needs
        the identical spelling for the length of its day (SNOW-995) and
        the two surfaces must agree to the character.

        Returns:
            ``{"hours": "4", "minutes": "05"}``, ``{"hours": "",
            "minutes": "41"}``, or ``None`` when the file carried no
            usable timing — in which case the caller omits the figure
            rather than rendering a zero, the same contract
            ``ascent_m``'s null carries.

        """
        return split_hours_minutes(self.duration)

    @property
    def climb(self) -> ClimbFigures:
        """Return the ascent and descent to show for this route (SNOW-1043).

        The terrain model's figures when the slope record carries heights,
        the stored ``ascent_m`` / ``descent_m`` otherwise — the rule is
        ``terrain_heights.climb_figures``, and every surface that quotes a
        figure reads it through here or through that function, so the map
        feed and the templates cannot disagree. Read from the row's own
        ``points`` and ``slope_samples``: no query. A plain property rather
        than a cached one, because the sampler writes ``slope_samples`` to
        a row a caller may already hold; a template that reads it more than
        once binds it once with ``with``.

        Returns:
            ``ClimbFigures(ascent_m, descent_m)``, either possibly None.

        """
        # Imported here rather than at module scope: the service imports
        # the slope sampler, which imports ``apps.routes.models``.
        from track.render import is_current  # noqa: PLC0415
        from track.terrain_heights import (  # noqa: PLC0415
            ClimbFigures,
            climb_figures,
        )

        # SNOW-1309: the stored render's figures, which the map reads too.
        if is_current(self.track_render):
            return ClimbFigures(
                self.track_render["ascent_m"], self.track_render["descent_m"]
            )
        return climb_figures(
            self.points, self.slope_samples, self.ascent_m, self.descent_m
        )

    def to_string(self) -> str:
        """Return a concise human-readable description of this route.

        Format: ``"{user} — {name or source_filename} ({distance} km)"``
        """
        label = self.name or self.source_filename or "Untitled route"
        return f"{self.user} — {label} ({self.distance_km:.1f} km)"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# RouteShare
# ---------------------------------------------------------------------------


class RouteShareQuerySet(models.QuerySet["RouteShare"]):
    """Custom queryset for RouteShare."""

    def active(self) -> "RouteShareQuerySet":
        """Return shares that can still be claimed right now.

        Two conditions, and both are the same kind of fact — the link has
        stopped pointing at something claimable:

        * ``route`` is still set. The FK is ``SET_NULL``, so an owner
          deleting the route leaves the share row behind with a null; a
          link to a route that no longer exists must stop working the
          moment it is deleted, not when it expires.
        * ``expires_at`` is still in the future. A share link is a
          reusable, time-bounded grant (see the class docstring), so the
          window is what bounds it rather than a claim count.

        Every read path goes through here — the redirect, the pending-list
        resolution and the claim itself — so "expired" and "revoked by
        deletion" are decided in exactly one place and can never disagree
        between the surface that lists a pending share and the endpoint
        that claims it.

        Returns:
            Filtered queryset of claimable shares.

        """
        # The example share (SNOW-1230) never expires: it is the curated
        # demonstration every visitor is offered, not a grant one user
        # made over their own data, so the window that bounds an ordinary
        # link has nothing to bound. Deleting its route still stops it.
        return self.filter(route__isnull=False).filter(
            models.Q(expires_at__gt=timezone.now()) | models.Q(is_example=True)
        )

    def example(self) -> "RouteShareQuerySet":
        """Return the example share (SNOW-1230), if one is set and live.

        At most one row is ever marked — a partial unique constraint holds
        that — so this is a queryset of zero or one. Through ``active()``
        so a deleted example route answers "no example" rather than a link
        to nothing.

        Returns:
            The live example share, as a queryset of at most one.

        """
        return self.active().filter(is_example=True)

    def claimable(self) -> "RouteShareQuerySet":
        """Return the shares a recipient may copy onto their account.

        ``active()`` less the example (SNOW-1230). The example is shown to
        everyone and owned by no one who sent it: a visitor exploring it
        is reading a demonstration, and a Save that wrote it onto their
        account would put a route they never skied into their own list.

        Returns:
            Filtered queryset of claimable shares.

        """
        return self.active().filter(is_example=False)


class RouteShare(BaseModel):
    """A tokenised link handing one Route to another account (SNOW-764).

    A ``Route`` is otherwise reachable by exactly one account: ``user`` is
    a plain FK and every endpoint in ``apps/routes/views.py`` is
    owner-scoped through ``Route.objects.for_user()``. This row is the one
    way a second account gets at one, and it is deliberately the smallest
    thing that does the job.

    **A claim COPIES, it never transfers.** ``claim_route_share`` writes a
    new ``Route`` owned by the claimer with the geometry and derived
    figures copied across; the sharer keeps theirs untouched. Two people
    who ski a tour together then each own their own row, and neither can
    rename or delete the other's. A transfer would make one person's
    Remove take a route off somebody else's map, which is not what handing
    a friend a track means.

    **The link is reusable and time-bounded.** The owner shares once and
    the link works for everyone they send it to until ``expires_at``, which
    is set ``settings.ROUTE_SHARE_MAX_AGE_DAYS`` ahead at creation. A
    single-use token would break the ordinary case (a group chat, three
    people tapping the same message), and an unbounded one would leave a
    grant on the user's own data alive forever with nothing that ever
    revokes it. ``claim_count`` and ``last_claimed_at`` record the use
    without gating it — they are what an owner or a staff member reads to
    see whether a link went further than intended.

    ``token`` follows ``apps.bulletins.models.BulletinShare`` exactly:
    ``secrets.token_urlsafe(8)``, 11 URL-safe characters, ~66 bits of
    entropy. This grant is bigger than that one's (it writes a row on the
    claimer's account rather than opening a public page), and the bound
    that makes 66 bits enough is that guessing gains an attacker a copy of
    a stranger's ski track and nothing else: there is no account access
    behind it, the claim is cap-checked and rate-limited, and the window
    closes.

    ``route`` is nullable (``SET_NULL``) so deleting a route does not
    delete the audit of who it was shared with; ``created_by`` is
    ``CASCADE``, because a deleted account's shares have no owner to
    account to and nothing left to grant.

    There is no click-tracking sidecar (``BulletinShareClick``'s twin).
    That table exists to measure the reach of a public marketing link;
    this token grants a copy of one user's own data and is not a surface
    whose reach anyone is trying to grow.
    """

    token = models.CharField(
        max_length=32,
        unique=True,
        db_index=True,
        help_text="URL-safe random token used in the /routes/s/<token>/ short URL.",
    )
    route = models.ForeignKey(
        Route,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shares",
        help_text=(
            "The route this link hands out. Nulled when the owner deletes "
            "the route, which stops the link working; the share row is kept."
        ),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="route_shares",
        help_text="The user who created this share link.",
    )
    expires_at = models.DateTimeField(
        help_text=(
            "When the link stops being claimable. Set "
            "settings.ROUTE_SHARE_MAX_AGE_DAYS ahead at creation."
        ),
    )
    claim_count = models.PositiveIntegerField(
        default=0,
        help_text=(
            "How many copies have been claimed through this link. Recorded, "
            "never enforced — the link is deliberately reusable."
        ),
    )
    last_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the most recent copy was claimed. Null until the first.",
    )
    is_example = models.BooleanField(
        default=False,
        help_text=(
            "The example route every visitor is offered (SNOW-1230). Never "
            "expires and can never be claimed. At most one share is marked."
        ),
    )
    example_date = models.DateField(
        null=True,
        blank=True,
        help_text=(
            "The day the example opens the map on — a past day with a full "
            "bulletin. Set only on the example share."
        ),
    )

    objects = RouteShareQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        constraints = [
            # One example, ever: every surface that offers "the example
            # route" reads a single row, and two marked rows would make
            # which one a visitor sees depend on query order.
            models.UniqueConstraint(
                fields=["is_example"],
                condition=models.Q(is_example=True),
                name="routes_routeshare_one_example",
            ),
        ]

    @property
    def is_live(self) -> bool:
        """Whether this share's link works right now.

        The row-level twin of ``RouteShareQuerySet.active()``, for a share
        already in hand — the redirect has one and would otherwise have to
        re-query to ask the same question. The conditions are stated once
        each here and once each there because Django has no way to share a
        predicate between Python and SQL; the pair is asserted equivalent
        by ``tests/routes/test_models.py``.

        Named ``is_claimable`` until SNOW-1230: the example share is live
        (its link works, forever) without being claimable, so the old name
        stopped describing the predicate. Whether a share may be COPIED is
        ``RouteShareQuerySet.claimable()``'s question.

        Returns:
            True when the route still exists and the window is still open
            (or the share is the example, which has no window).

        """
        return self.route_id is not None and (
            self.is_example or self.expires_at > timezone.now()
        )

    def to_string(self) -> str:
        """Return a concise human-readable description of this share.

        Format: ``RouteShare(<token>, <route label or "deleted">)``
        """
        # Bound to a local rather than tested through ``route_id``: the FK
        # is nullable, so the attribute is ``Route | None`` and only a test
        # on the object itself narrows it.
        route = self.route
        label = route.to_string() if route is not None else "deleted"
        return f"RouteShare({self.token}, {label})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# Ski-touring reference data (SNOW-1276)
# ---------------------------------------------------------------------------


class SkiLineQuerySet(models.QuerySet[Any]):
    """Queryset shared by ``SkiSegment`` and ``SkiTour``."""

    def intersecting(
        self, west: float, south: float, east: float, north: float
    ) -> SkiLineQuerySet:
        """Return the lines whose bounding box overlaps the rectangle.

        Edge-inclusive and over-inclusive: a line whose box overlaps but
        whose path misses the rectangle is returned too, which a map view
        draws harmlessly off-screen.

        Args:
            west: The rectangle's western longitude.
            south: Its southern latitude.
            east: Its eastern longitude.
            north: Its northern latitude.

        Returns:
            The filtered queryset.

        """
        return self.filter(
            west__lte=east, east__gte=west, south__lte=north, north__gte=south
        )


class SkiSegmentQuerySet(SkiLineQuerySet):
    """Custom queryset for SkiSegment."""

    def skiable(self) -> SkiSegmentQuerySet:
        """Return the segments for skis, leaving out the snowshoe-only ones.

        Returns:
            The filtered queryset.

        """
        return self.filter(discipline=SkiSegment.SKI_TOUR)


class _SkiLine(BaseModel):
    """What a synced segment and a synced tour share.

    ``source_updated_at`` is the service's ``updated_at`` for the item, so
    an unchanged item is recognised without reading its geometry, and the
    newest one held is where the next incremental pull starts.
    """

    source_uuid = models.UUIDField(
        unique=True, help_text="The reference-data service's id for this line."
    )
    source_updated_at = models.DateTimeField(
        help_text="When the service last changed the item this row copies."
    )
    geometry = models.JSONField(
        help_text="The line as a WGS84 GeoJSON MultiLineString, simplified to ~5 m."
    )
    west = models.FloatField(help_text="Bounding box: western longitude.")
    south = models.FloatField(help_text="Bounding box: southern latitude.")
    east = models.FloatField(help_text="Bounding box: eastern longitude.")
    north = models.FloatField(help_text="Bounding box: northern latitude.")

    class Meta(BaseModel.Meta):
        """Model metadata."""

        abstract = True


class SkiSegment(_SkiLine):
    """One segment of swisstopo's ski-touring network.

    Segments meet at shared ends, so together they are a graph that a
    drawn route can snap to and be routed along (SNOW-1277).
    ``direction`` and ``access`` are swisstopo's codes, kept as published:
    what their values mean is not documented yet, and nothing reads them.
    """

    SKI_TOUR = "ski_tour"

    segment_id = models.PositiveIntegerField(help_text="swisstopo's segm_id.")
    discipline = models.CharField(
        max_length=32, help_text="ski_tour or ski_snowshoe_tour, as published."
    )
    direction = models.SmallIntegerField(null=True, blank=True)
    access = models.SmallIntegerField(null=True, blank=True)

    objects = SkiSegmentQuerySet.as_manager()

    class Meta(_SkiLine.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["west", "east", "south", "north"], name="routes_skiseg_bbox"
            ),
        ]

    def to_string(self) -> str:
        """Return ``segment <id> (<discipline>)``."""
        return f"segment {self.segment_id} ({self.discipline})"

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()


class SkiTour(_SkiLine):
    """One of SAC's named ski tours, as swisstopo publishes it.

    A template, not a route: nobody owns it, and saving one to an account
    copies it into an ordinary ``Route`` (SNOW-1278). ``names``, ``grades``
    and ``urls`` are keyed by language (de, fr, it, en).
    """

    sac_id = models.PositiveIntegerField(help_text="SAC's route id.")
    name = models.CharField(max_length=255)
    names = models.JSONField(default=dict, help_text="The name by language.")
    grade = models.CharField(
        max_length=8, blank=True, default="", help_text="SAC grade, e.g. PD+."
    )
    grades = models.JSONField(default=dict, help_text="The grade by language.")
    difficulty = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="The grade as a rank, 1 (F) to 13 (TD)."
    )
    ascent_m = models.PositiveIntegerField(null=True, blank=True)
    ascent_time_label = models.CharField(max_length=32, blank=True, default="")
    descent_m = models.PositiveIntegerField(null=True, blank=True)
    target_name = models.CharField(max_length=255, blank=True, default="")
    target_altitude_m = models.PositiveIntegerField(null=True, blank=True)
    urls = models.JSONField(default=dict, help_text="SAC's page by language.")
    source = models.CharField(max_length=64, blank=True, default="")
    slope_samples = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The terrain under the tour's line, sampled the first time "
            "anyone opens it (SNOW-1278) and copied into a saved route, in "
            "Route.slope_samples' shape. Null means NEVER SAMPLED; a sync "
            "that rewrites the tour clears it."
        ),
    )
    track_render = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "The tour's line drawn as a route's is, in Route.track_render's "
            "shape (SNOW-1309), built with every slope_samples it holds; "
            "the tours layer draws its legs from it. Null means never "
            "walked: the layer draws SAC's own line. A sync that rewrites "
            "the tour clears it with slope_samples."
        ),
    )

    objects = SkiLineQuerySet.as_manager()

    class Meta(_SkiLine.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["west", "east", "south", "north"], name="routes_skitour_bbox"
            ),
        ]

    def to_string(self) -> str:
        """Return ``<name> (<grade>)``, or the name alone when ungraded."""
        return f"{self.name} ({self.grade})" if self.grade else self.name

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()
