"""
apps/trips/models.py — Database models for the trips application (SNOW-819).

Two models:

- ``Trip``: a route the organiser already owns, on a named day, meeting
  somewhere at a stated time. The geometry is a SNAPSHOT copied from the
  route at creation and never re-read, so renaming, editing or deleting the
  source route cannot change what a trip page shows the people going on it.
- ``TripParticipant``: one account on one trip. The organiser gets a row of
  their own at creation, so "everyone on this trip" is one relation with no
  union and no special case at the top of every roster query.

**A trip is one object with a roster, not a thing that gets copied.** A
route can be handed out hundreds of times and each recipient sensibly gets
their own copy (``RouteShare``); a trip is the opposite — everyone on it is
meeting each other, at one place, at one time, so they belong on the same
row. See ``docs/decisions/a-trip-is-one-object-with-a-roster.md``.

The mutating entry points live in ``apps/trips/services/trips.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel

if TYPE_CHECKING:
    import datetime

    from django.contrib.auth.models import User


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class TripQuerySet(models.QuerySet["Trip"]):
    """Custom queryset for Trip."""

    def for_user(self, user: "User") -> "TripQuerySet":
        """Return every trip the user is ON, organised or joined.

        Scoped by PARTICIPATION and not by ``created_by``: the organiser
        holds a participant row of their own from the moment the trip is
        created (see ``TripParticipant``), so one filter answers both
        halves and no caller has to remember to union them.

        Args:
            user: The user to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(participants__user=user)

    def upcoming(self, on: "datetime.date") -> "TripQuerySet":
        """Return trips dated on or after ``on``, soonest first.

        A trip dated ``on`` counts as upcoming: the day it exists for has
        not finished, and a group still meeting this afternoon must not
        find their trip filed under "past" over breakfast.

        Args:
            on: The date to split against — normally today at the
                reader's own date, passed in rather than read here so the
                split is testable against a frozen clock.

        Returns:
            Filtered queryset, ordered soonest first.

        """
        return self.filter(date__gte=on).order_by("date", "start_time")

    def past(self, on: "datetime.date") -> "TripQuerySet":
        """Return trips dated strictly before ``on``, most recent first.

        Args:
            on: The date to split against. The boundary belongs to
                ``upcoming`` — see its docstring.

        Returns:
            Filtered queryset, ordered most recent first.

        """
        return self.filter(date__lt=on).order_by("-date", "-start_time")

    def shared(self) -> "TripQuerySet":
        """Return trips whose share link works right now.

        The SQL twin of ``Trip.share_is_live``. Every read of a tokenised
        trip goes through here, so "unknown token", "revoked" and "expired"
        are decided in one place and answered identically — a holder of a
        real link and a guesser walking the token space must not be able to
        tell them apart.

        Returns:
            Filtered queryset of live-linked trips.

        """
        return self.filter(
            share_token__isnull=False, share_expires_at__gt=timezone.now()
        )


# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------


class Trip(BaseModel):
    """A planned outing: one route, one day, one meeting point, one roster.

    **The snapshot is the trip.** ``points``, ``bounds``, ``distance_m``,
    ``ascent_m``, ``descent_m``, ``point_count`` and ``route_name`` are
    copied from the source ``Route`` when the trip is created and are never
    re-read from it. Everything a trip page draws comes from these fields,
    so a trip stays exactly what its organiser shared even after they
    rename, re-upload or delete the route it came from — and a participant
    who saved the route (SNOW-824) got the geometry they were shown rather
    than whatever the organiser's row happens to hold today.

    ``ascent_m`` and ``descent_m`` are nullable and the null is copied AS
    NULL, never as zero. ``Route``'s own docstring is explicit that "we
    don't know" and "flat" are different facts, and flattening one into the
    other is a safety-relevant lie about terrain somebody is about to ski.

    ``date`` and ``start_time`` are **wall-clock at the meeting point**,
    stored as a plain ``DateField`` and ``TimeField`` and never combined
    into an aware datetime. This looks like a breach of the project's
    "every datetime carries tzinfo" convention and is not one: neither
    field is a datetime. Converting would be the bug — "07:30" is what the
    organiser typed and what everyone standing at the lift station will
    read off their own watch, and rendering it through a friend's phone
    timezone would show a group member in another country a different
    meeting time for the same meeting.

    ``route`` is **provenance only**. It records which route this trip was
    planned from, for the organiser's own benefit and for a later "trips on
    this route" read; NO rendering path follows it, and none should. It is
    nullable (``SET_NULL``) because the organiser may delete their route
    and the trip must survive intact — which it does, because the snapshot
    above is what the page reads.

    ``meeting_point`` is ``PROTECT``, and that is not negotiable.
    ``apps.favourites.services._delete_location_if_orphaned`` asks the
    database "is anything still referencing this location?" by catching
    ``ProtectedError``; a ``SET_NULL`` or ``CASCADE`` referent would answer
    "no" while still pointing at the row, and would silently break that
    sweep for every app that mints anonymous locations.

    ``share_token`` and ``share_expires_at`` are the ONE link a trip has
    (SNOW-821), and there is no ``TripShare`` table — see the fields' own
    comment. The expiry derives from ``date``, not from the mint time: a
    trip planned three months out must not have its link die two months
    before the day it exists for.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trips_created",
        help_text=(
            "The organiser — the account that created this trip. "
            "Organiser-ness is derived from this column, never stored a "
            "second time on the participant row."
        ),
    )
    route = models.ForeignKey(
        "routes.Route",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trips",
        help_text=(
            "The route this trip was planned from. PROVENANCE ONLY — no "
            "rendering path follows it; the snapshot fields below are what "
            "every surface reads. Nulled when the organiser deletes the "
            "route, which leaves the trip intact."
        ),
    )
    meeting_point = models.ForeignKey(
        "locations.Location",
        on_delete=models.PROTECT,
        related_name="trips",
        help_text=(
            "Where the group meets. A Location minted for this trip alone, "
            "carrying no name and no kind. PROTECT — see the class "
            "docstring for why the orphan sweep depends on it."
        ),
    )
    date = models.DateField(
        help_text=(
            "The day of the trip, as a wall-clock date at the meeting "
            "point. Never converted between timezones."
        ),
    )
    start_time = models.TimeField(
        help_text=(
            "The meeting time, as wall-clock at the meeting point. Never "
            "converted between timezones — see the class docstring."
        ),
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "The organiser's label for the trip. May be blank, in which "
            "case surfaces fall back to route_name."
        ),
    )
    description = models.TextField(
        blank=True,
        help_text=(
            "The organiser's note to the group — what to bring, where to "
            "park, who has the rope. Plain text, auto-escaped on render."
        ),
    )
    points = models.JSONField(
        help_text=(
            "Snapshot of the route's simplified track as [[lon, lat, ele], "
            "…] in GeoJSON axis order, copied at creation and never "
            "re-read from the route."
        ),
    )
    bounds = models.JSONField(
        help_text=(
            "Snapshot of the route's GeoJSON bbox: "
            "[min_lon, min_lat, max_lon, max_lat]."
        ),
    )
    distance_m = models.FloatField(
        help_text="Snapshot of the route's length in metres.",
    )
    ascent_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Snapshot of the route's total climb in metres. Null — not "
            "zero — when the source route had no elevation data at all."
        ),
    )
    descent_m = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Snapshot of the route's total drop in metres, as a positive "
            "magnitude. Null on the same condition as ascent_m."
        ),
    )
    point_count = models.PositiveIntegerField(
        help_text="Number of coordinates stored in points.",
    )
    route_name = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "The source route's label at the moment the trip was created. "
            "The fallback for a trip with no name of its own, and the seed "
            "for a saved copy's name (SNOW-824)."
        ),
    )

    # --- Sharing (SNOW-821) ------------------------------------------------
    #
    # ONE LINK PER TRIP, and the token lives here rather than on a TripShare
    # table. ``RouteShare`` is a separate model because a route is handed out
    # in many independent grants, each with its own claim counters worth
    # auditing; a trip is one object with one roster, so a second grant would
    # be a second name for the same thing. The cost is stated so it is a
    # choice: no per-link audit trail, and no way to hand two groups
    # different links. Neither is a thing a trip needs — the roster is the
    # record of who came.
    share_token = models.CharField(
        max_length=32,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text=(
            "URL-safe random token used in the /trips/s/<token>/ short URL. "
            "Null until the organiser shares the trip, and nulled again when "
            "they revoke it."
        ),
    )
    share_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the link stops working. Derived from the trip's own DATE "
            "plus settings.TRIP_SHARE_MAX_AGE_DAYS, never from the mint "
            "time — see share_is_live."
        ),
    )

    objects = TripQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        # A trip is read as an agenda, so the default order is the one an
        # agenda has: the day it happens, then the time it starts. Every
        # other model in this project orders by ``-created_at``, which for a
        # trip would sort by when it was planned — a fact nobody reads.
        ordering = ["date", "start_time"]

    @property
    def share_is_live(self) -> bool:
        """Whether this trip's share link works right now.

        The row-level twin of ``TripQuerySet.shared()``, for a trip already
        in hand. Both state the same two conditions — a token exists, and
        the window is still open — because Django has no way to share a
        predicate between Python and SQL; the pair is asserted equivalent
        by ``tests/trips/test_models.py``, exactly as ``RouteShare``'s is.

        Returns:
            True when the token is set and has not expired.

        """
        return self.share_token is not None and (
            self.share_expires_at is not None and self.share_expires_at > timezone.now()
        )

    @property
    def distance_km(self) -> float:
        """Return ``distance_m`` in kilometres.

        A display helper, matching ``Route.distance_km``: metres is what
        the maths produces, kilometres is what a route is read in.
        """
        return self.distance_m / 1000

    @property
    def display_name(self) -> str:
        """Return the trip's label, falling back to the source route's.

        A trip may be created with no name — the ordinary case is a group
        going up a route that already has one — and every surface should
        then read the route's own label rather than a blank line or an
        invented "Untitled".
        """
        return self.name or self.route_name

    def to_string(self) -> str:
        """Return a concise human-readable description of this trip.

        Format: ``"{label} — {date} {start_time}"``
        """
        label = self.display_name or "Untitled trip"
        return f"{label} — {self.date:%Y-%m-%d} {self.start_time:%H:%M}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()


# ---------------------------------------------------------------------------
# TripParticipant
# ---------------------------------------------------------------------------


class TripParticipantQuerySet(models.QuerySet["TripParticipant"]):
    """Custom queryset for TripParticipant."""

    def for_trip(self, trip: Trip) -> "TripParticipantQuerySet":
        """Return the roster for one trip, in join order.

        Args:
            trip: The trip whose roster is wanted.

        Returns:
            Filtered queryset.

        """
        return self.filter(trip=trip)


class TripParticipant(BaseModel):
    """One account on one trip.

    **The organiser gets a row here at creation.** It would be smaller not
    to — ``Trip.created_by`` already names them — but then "everyone on
    this trip" would be a union of a foreign key and a relation, written
    out at the top of every roster query, count and template loop, and
    eventually written out wrongly in one of them. Organiser-ness stays
    DERIVED (``trip.created_by_id == user.id``) rather than becoming a
    second column that can disagree with the first.

    ``(trip, user)`` is unique, so joining is naturally idempotent at the
    database level; ``apps.trips.services.participants.join_trip`` makes it
    idempotent at the service level too, so a double-tapped Join never
    surfaces an ``IntegrityError`` on a request path.

    ``joined_at`` is a real field rather than a read of ``created_at``: the
    roster is ordered by it, and ordering a user-facing list by a BaseModel
    audit column would make the display depend on a column nothing else
    treats as meaningful.
    """

    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name="participants",
        help_text=(
            "The trip this participation is on. CASCADE — deleting a trip "
            "removes it for everyone, which is what the delete confirmation "
            "says it does."
        ),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="trip_participations",
        help_text="The account going on the trip.",
    )
    joined_at = models.DateTimeField(
        default=timezone.now,
        help_text=(
            "When this account joined. The organiser's row carries the "
            "moment the trip was created."
        ),
    )

    objects = TripParticipantQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        # Join order, so the roster reads as the group filled up — and the
        # organiser, whose row is written first, is always at the top.
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "user"],
                name="unique_trip_participant",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this row.

        Format: ``"{user} on {trip}"``
        """
        return f"{self.user} on {self.trip.to_string()}"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
