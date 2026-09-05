"""
apps/trips/services/trips.py — creating, editing and deleting a Trip.

The mutating entry points ``apps/trips/views.py`` calls: ``create_trip``,
``update_trip``, ``delete_trip``, and the account-erasure counterpart
``delete_trips_for_user``.

**The snapshot is copied here and nowhere else.** ``create_trip`` reads the
source ``Route``'s geometry and derived figures once, writes them onto the
``Trip``, and never looks at the route again. Everything downstream — the
trip page, the map, the elevation profile, the saved copy SNOW-824 writes —
reads the snapshot, so the organiser renaming or deleting their route
cannot change what the group was shown. See ``Trip``'s own docstring.

**The meeting point mints its own Location**, following
``apps.favourites.services.create_favourite`` exactly: ``Location.objects
.create(...)`` with no ``name`` and no ``kind``, inside the transaction that
writes the trip. Never ``anchor_location()`` — that helper deliberately
REUSES an existing anonymous row for a coordinate it has seen before, which
is right for fixture-derived points and wrong here: two trips meeting at the
same lift station are two rows, and a shared row would mean deleting one
trip could not sweep the location the other still uses (it could, via
PROTECT — but the two trips would also be editing each other's meeting
point, which is the real defect).

The per-user cap is ``settings.TRIPS_MAX_PER_USER``, enforced by the pair
below.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import ProtectedError

from apps.locations.models import Location
from apps.routes.models import Route
from apps.trips.models import Trip, TripParticipant

if TYPE_CHECKING:
    import datetime

    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# Must match Trip.name / Trip.route_name max_lengths. A route may be named
# anything the uploaded GPX said, so the snapshot is truncated here rather
# than left to raise a DB DataError on a trip we could otherwise store.
_NAME_MAX_LENGTH = 100

# The Route fields a trip's snapshot inherits. Listed rather than derived
# from ``Route._meta`` so adding a field to Route is a deliberate decision
# about whether a trip should carry it, not an automatic yes. ``name`` is
# absent because it lands on ``Trip.route_name`` rather than ``Trip.name``;
# ``source_filename`` and the ``started_at``/``finished_at`` pair are absent
# because a trip is a PLAN and never a recording — see SNOW-824's copy for
# the other end of the same refusal.
_SNAPSHOT_FIELDS = (
    "points",
    "bounds",
    "distance_m",
    "ascent_m",
    "descent_m",
    "point_count",
)


class TripLimitReached(Exception):
    """Raised when a user has reached ``settings.TRIPS_MAX_PER_USER``."""


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------
#
# A deliberate SIBLING of ``apps.routes.services.routes._assert_under_cap`` /
# ``_locked_cap_recheck`` rather than a call into them. Those two are
# hard-coded to ``Route.objects.for_user`` and
# ``settings.ROUTES_MAX_PER_USER``, so they cannot enforce a trips cap; what
# is shared is the IDIOM (SNOW-465's ``create_favourite`` is the original),
# and the idiom is four lines. Generalising it would mean a helper taking a
# queryset, a setting name and an exception class — three parameters to
# avoid eight lines, in ``apps/core/`` where neither caller lives. If a
# fourth caller appears, that is the moment; three is not yet it.
#
# The routes pair IS reused verbatim by ``apps/trips/services/routes.py``,
# where the cap genuinely is the routes cap.


def _assert_under_cap(user: "User") -> None:
    """Raise ``TripLimitReached`` when the user is already at the cap.

    The cheap, unlocked check: racy on its own, and not the enforcement —
    it is the early exit that keeps the snapshot copy and the Location
    mint from happening for a row that cannot be stored.

    Counted over trips the user ORGANISED, not trips they are on: joining
    a friend's trip is not a thing this account should be rationed for,
    and a cap that counted joins would let one prolific organiser exhaust
    everybody else's.

    Args:
        user: The user about to gain a trip.

    Raises:
        TripLimitReached: When the user already organises
            ``settings.TRIPS_MAX_PER_USER`` trips.

    """
    if Trip.objects.filter(created_by=user).count() >= settings.TRIPS_MAX_PER_USER:
        raise TripLimitReached(
            f"User {user.pk} has reached the {settings.TRIPS_MAX_PER_USER}-trip limit."
        )


def _locked_cap_recheck(user: "User") -> None:
    """Lock the user row, then re-check the cap. Call inside ``atomic()``.

    Locking first is what makes the cap hold under concurrency. A bare
    ``count()`` takes no lock, so under PostgreSQL READ COMMITTED two
    requests could both read below the cap and both insert.
    ``select_for_update()`` makes the second transaction block until the
    first commits, so its count reflects the first insert. On SQLite
    (tests) ``FOR UPDATE`` is a silent no-op, which is why this is argued
    rather than demonstrated.

    The lock is on the USER row: the thing being serialised is "how many
    trips does this user organise", and there is no row to lock for a trip
    that does not exist yet.

    Args:
        user: The user about to gain a trip.

    Raises:
        TripLimitReached: When the user already organises
            ``settings.TRIPS_MAX_PER_USER`` trips.

    """
    get_user_model().objects.select_for_update().get(pk=user.pk)
    _assert_under_cap(user)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def _meeting_coordinates(
    route: Route,
    latitude: float | None,
    longitude: float | None,
) -> tuple[float, float]:
    """Return the (latitude, longitude) the meeting point sits at.

    An explicit pair wins; otherwise the route's FIRST coordinate is the
    default, because the start of the track is where a group meets far
    more often than not. Coordinate arguments are latitude-first, per the
    repo convention, while ``Route.points`` stores ``[lon, lat, ele]`` in
    GeoJSON axis order — the swap happens here, once, rather than at every
    call site.

    Args:
        route: The source route, read for its first stored coordinate.
        latitude: Explicit latitude, or None to take the route's.
        longitude: Explicit longitude, or None to take the route's.

    Returns:
        The (latitude, longitude) pair to mint the Location at.

    Raises:
        ValueError: When no explicit pair was given and the route stores
            no coordinates to default from.

    """
    if latitude is not None and longitude is not None:
        return latitude, longitude

    if not route.points:
        raise ValueError(
            f"Route {route.uuid} has no coordinates to place a meeting point at."
        )
    first = route.points[0]
    return float(first[1]), float(first[0])


def create_trip(
    user: "User",
    *,
    route_uuid: UUID,
    date: "datetime.date",
    start_time: "datetime.time",
    name: str = "",
    description: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> Trip:
    """Create a trip from one of the given user's own routes.

    Owner-scoped by the lookup and not by a check afterwards: a uuid that
    is not this user's raises ``Route.DoesNotExist``, which the view turns
    into a 404 rather than a 403, so a probing request cannot tell "not
    yours" from "doesn't exist".

    The cap is checked twice — an unlocked early exit, then a locked
    re-check inside the transaction. The snapshot copy, the meeting
    point's ``Location`` and the organiser's own participant row are all
    written inside that one transaction, so a failed re-check cannot leave
    an orphaned Location behind.

    Args:
        user: The authenticated organiser.
        route_uuid: The source Route's uuid. Must belong to ``user``.
        date: The day of the trip — wall-clock at the meeting point.
        start_time: The meeting time — likewise wall-clock.
        name: The organiser's label for the trip. Defaults to "".
        description: The organiser's note to the group. Defaults to "".
        latitude: Explicit meeting-point latitude, or None to default to
            the route's first coordinate.
        longitude: Explicit meeting-point longitude, on the same terms.

    Returns:
        The newly created Trip.

    Raises:
        Route.DoesNotExist: When no route with that uuid belongs to
            ``user``.
        TripLimitReached: When the user already organises
            ``settings.TRIPS_MAX_PER_USER`` trips.
        ValueError: When the meeting point can be neither given nor
            derived — see ``_meeting_coordinates``.

    """
    _assert_under_cap(user)

    route = Route.objects.for_user(user).get(uuid=route_uuid)
    meeting_latitude, meeting_longitude = _meeting_coordinates(
        route, latitude, longitude
    )

    with transaction.atomic():
        _locked_cap_recheck(user)
        # No name and no kind: naming a place is a curation act, and this
        # point is one trip's meeting spot rather than a place on the map.
        # ``elevation_m`` is left null — it is resolved out of band, and a
        # meeting point's height is not a figure any trip surface reads.
        meeting_point = Location.objects.create(
            latitude=meeting_latitude,
            longitude=meeting_longitude,
        )
        trip = Trip.objects.create(
            created_by=user,
            route=route,
            meeting_point=meeting_point,
            date=date,
            start_time=start_time,
            name=name[:_NAME_MAX_LENGTH],
            description=description,
            route_name=route.name[:_NAME_MAX_LENGTH],
            **{field: getattr(route, field) for field in _SNAPSHOT_FIELDS},
        )
        # The organiser is ON the trip, from the first moment it exists.
        # See TripParticipant's docstring for why this is a row rather than
        # a special case in every roster query.
        TripParticipant.objects.create(trip=trip, user=user)

    logger.info(
        "Trip created: user=%s uuid=%s route=%s",
        user.pk,
        trip.uuid,
        route.uuid,
    )
    return trip


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def update_trip(
    user: "User",
    uuid: UUID,
    *,
    date: "datetime.date",
    start_time: "datetime.time",
    name: str = "",
    description: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
) -> Trip:
    """Update the plan half of one of the organiser's own trips.

    Only the plan is editable: the day, the time, the label, the note and
    where the group meets. The SNAPSHOT is not, and there is no argument
    that could change it — a trip whose route could be swapped underneath
    the people who joined it is a different trip wearing the same link.

    The meeting point is moved by writing the new coordinates onto the
    trip's OWN ``Location`` rather than by minting a second one. The row
    exists for this trip alone, so there is nothing else to disturb, and
    minting per edit would leave an orphan behind on every correction.

    Args:
        user: The authenticated organiser.
        uuid: The Trip's uuid.
        date: The new date.
        start_time: The new start time.
        name: The new label. Defaults to "".
        description: The new note. Defaults to "".
        latitude: New meeting-point latitude, or None to leave it where
            it is.
        longitude: New meeting-point longitude, on the same terms.

    Returns:
        The updated Trip.

    Raises:
        Trip.DoesNotExist: When no trip with that uuid was organised by
            ``user``.

    """
    trip = Trip.objects.select_related("meeting_point").get(uuid=uuid, created_by=user)

    with transaction.atomic():
        if latitude is not None and longitude is not None:
            location = trip.meeting_point
            moved = location.latitude != latitude or location.longitude != longitude
            location.latitude = latitude
            location.longitude = longitude
            # ``updated_at`` is auto_now, applied in Python on save() and
            # skipped for any field absent from update_fields — so it is
            # listed, or the column is left stale.
            update_fields = ["latitude", "longitude", "updated_at"]
            if moved:
                # SNOW-840: the cached three word address named the square
                # the pin USED to stand on, and nothing else would notice
                # for up to 30 days. A stale address is worse than none:
                # the trip page renders it as the meeting point while the
                # coordinates in the same element's ``title`` say
                # somewhere else, and the whole reason to show an address
                # is that somebody navigates to it. Cleared here, so the
                # next render converts the square the pin actually stands
                # on.
                #
                # Guarded on ``moved`` rather than cleared on every edit
                # because a conversion is billed: an organiser correcting
                # a start time must not spend one.
                #
                # The same rule, for the same reason, as
                # ``apps.public.api`` nulling ``elevation_m`` when a
                # location's coordinate is rewritten — a value derived
                # from a coordinate does not survive that coordinate
                # changing.
                location.what3words = None
                location.what3words_fetched_at = None
                update_fields += ["what3words", "what3words_fetched_at"]
            location.save(update_fields=update_fields)

        trip.date = date
        trip.start_time = start_time
        trip.name = name[:_NAME_MAX_LENGTH]
        trip.description = description
        trip.save(
            update_fields=["date", "start_time", "name", "description", "updated_at"]
        )

    logger.info("Trip updated: user=%s uuid=%s", user.pk, uuid)
    return trip


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


def _delete_location_if_orphaned(location: Location) -> None:
    """Delete an anonymous Location that nothing references any more.

    The trips twin of ``apps.favourites.services._delete_location_if_orphaned``
    — same rule, same reasoning, and deliberately not imported from there:
    that function is private to the favourites service, and reaching across
    apps for a private helper is how one app's refactor breaks another's
    erasure guarantee. Both are eight lines; a shared home for them is a
    move for whichever ticket adds the third caller.

    Args:
        location: The location whose last known referent was just removed.

    """
    if location.name:
        # Curated data. A trip meeting at Mont Fort must not delete Mont
        # Fort when it is cancelled.
        return
    try:
        # Ask the database rather than enumerating reverse relations. Every
        # referent of Location is PROTECT, so a ProtectedError *is* the
        # answer "something still references this", and it stays correct as
        # later tickets add referents this function has never heard of. A
        # savepoint keeps the failed delete from poisoning the surrounding
        # atomic block.
        with transaction.atomic():
            location.delete()
    except ProtectedError:
        logger.debug("Location id=%s still referenced; left in place.", location.pk)


def delete_trip(user: "User", uuid: UUID) -> None:
    """Delete one of the organiser's own trips, meeting point included.

    Organiser-checked: only deletes a row this user created. Deleting a
    trip removes it for EVERYONE — the participant rows cascade — which is
    what the delete confirmation says out loud, naming how many other
    people it affects.

    Args:
        user: The authenticated organiser.
        uuid: The Trip's uuid.

    Raises:
        Trip.DoesNotExist: When no trip with that uuid was organised by
            this user.

    """
    trip = Trip.objects.select_related("meeting_point").get(uuid=uuid, created_by=user)
    location = trip.meeting_point
    with transaction.atomic():
        trip.delete()
        _delete_location_if_orphaned(location)
    logger.info("Trip deleted: user=%s uuid=%s", user.pk, uuid)


def delete_trips_for_user(user: "User") -> int:
    """Delete every trip ``user`` organised, minted Locations included.

    The whole-roster counterpart to ``delete_trip``, for account erasure,
    and the exact twin of ``delete_favourites_for_user``.
    ``Trip.created_by`` is ``CASCADE``, so deleting the user removes the
    trip rows in bulk — but a bulk cascade runs no Python, so each trip's
    minted meeting-point ``Location`` would survive account erasure with
    its real coordinates, referenced by nothing. Deleting the rows one at
    a time here, before the user goes, is what puts the orphan sweep back
    on the erasure path.

    Deleting the trip first and asking about its location second is what
    makes the ``ProtectedError`` probe answer honestly.

    Args:
        user: The user whose organised trips are being removed.

    Returns:
        The number of trips deleted.

    """
    deleted = 0
    with transaction.atomic():
        for trip in Trip.objects.filter(created_by=user).select_related(
            "meeting_point"
        ):
            location = trip.meeting_point
            trip.delete()
            _delete_location_if_orphaned(location)
            deleted += 1
    if deleted:
        logger.info("Trips deleted for user=%s: %s row(s)", user.pk, deleted)
    return deleted
