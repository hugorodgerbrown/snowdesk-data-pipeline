"""
apps/trips/services/routes.py — saving a trip's route (SNOW-824).

``save_trip_route``: copy the route a trip is drawn from onto the viewer's
own account, so they have it after the trip is over and wherever they look
at their routes.

**It copies from the SNAPSHOT, not through ``Trip.route``.** That FK is
provenance only (see ``apps.trips.models.Trip``) and may be null — the
organiser can delete their route — so reading through it would make Save
fail exactly when a copy is most useful. The snapshot is also what the
viewer was SHOWN: they get the geometry on the page, not whatever the
organiser's row happens to hold today.

**Nulls stay null.** ``ascent_m`` and ``descent_m`` are copied as they are,
including ``None``. ``Route``'s own docstring is explicit that null means
"the source file carried no elevation data", not "flat", and flattening one
into the other is a safety-relevant lie about terrain somebody is about to
ski.

**The copy carries no timing and no filename.** ``started_at`` and
``finished_at`` are null and ``source_filename`` is ``""`` — a trip is a
PLAN and was never a recording, so it has no elapsed time to inherit, and
there is no uploaded file behind it. This is the same refusal
``Route.ascent_m``'s null exists to make: an invented zero would be read as
a measurement.

**Available to anyone who can see the trip**, participant or not. Saving
does not join and joining does not save: they are different acts — one puts
you in a group, the other puts a track on your map — and a recipient who
wants the line without committing to the day should be able to take it.

**Nothing links the copy back to the trip afterwards**, deliberately. It
becomes an ordinary route the viewer owns, renameable and deletable like
any other; a back-pointer would make it a second class of route every
surface would then have to know about.

**THE COPY IS HANDED THE TRIP'S OWN SLOPE RECORD** (SNOW-962), and is
sampled only when the trip has none to give.

SNOW-910 did the opposite here — re-sampled every time — and said why at
length: a trip had no route to read (``Trip.route`` is provenance only
and may be null), so the choice was "put a slope column on ``Trip`` or
ask the origin again", and the column was declined because **no trip
surface drew a coloured line**. That premise is what SNOW-962 changed.
The trip page now draws its line and its height profile in the slope
classes, so the record has to be on the snapshot for the page to have
anything to draw — and once it is there, this path is in exactly
``claim_route_share``'s position, with the record one field access away.

So the re-sample is gone and the copy is back. What is left of the old
reasoning is its last paragraph, which was right: a trip created from a
route uploaded minutes earlier snapshots a null, so the enqueue is still
needed for that race. It is now the branch rather than the rule.

The full argument, including the four grounds the column was declined on
and which of them SNOW-962 overturned, is in
``docs/decisions/a-slope-segment-is-the-shared-record.md``.

The cap is ``settings.ROUTES_MAX_PER_USER``, enforced through
``apps.routes.services.shares.write_route_copy`` — the routes app's own
cap, reused rather than re-derived, because this genuinely IS the routes
cap and a second copy of the arithmetic would eventually differ.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apps.routes.models import Route
from apps.routes.services.shares import write_route_copy
from apps.routes.services.slope_segments import enqueue_route_slope_sampling
from apps.trips.models import Trip

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User

logger = logging.getLogger(__name__)

# Must match Route.name's max_length.
_NAME_MAX_LENGTH = 100


def _copy_name_for(trip: Trip) -> str:
    """Return the name a saved copy of ``trip``'s route should carry.

    The trip's own label first, falling back to the snapshot's
    ``route_name`` — the source route's label at the moment the trip was
    created, which is why the snapshot carries it at all. Both are the
    organiser's own words; neither is invented here. A copy seeded with
    something like "Copy of …" would put a word of ours inside the user's
    data, which every later surface would render as though they had typed
    it (``claim_route_share`` makes the same refusal).

    Args:
        trip: The trip whose route is being saved.

    Returns:
        A name, possibly empty — ``Route.name`` is blankable and the route
        list falls back to its own "Untitled route".

    """
    return (trip.name or trip.route_name)[:_NAME_MAX_LENGTH]


def _snapshot_fields(trip: Trip) -> dict[str, Any]:
    """Return the Route field values a copy of ``trip``'s route carries.

    Listed rather than derived, so adding a field to ``Trip`` is a
    deliberate decision about whether a saved route should carry it. The
    three absences are the reason this exists: ``source_filename`` (there
    is no uploaded file behind a trip) and the ``started_at`` /
    ``finished_at`` pair (a trip is a plan, never a recording).

    ``slope_samples`` IS supplied, from the snapshot's own copy of it
    (SNOW-962). Null passes through as null — a trip nothing has sampled
    hands the copy nothing, and ``save_trip_route`` enqueues sampling for
    that case alone.

    Args:
        trip: The trip whose snapshot is being copied.

    Returns:
        The field dict ``write_route_copy`` takes.

    """
    return {
        "name": _copy_name_for(trip),
        "source_filename": "",
        "points": trip.points,
        "distance_m": trip.distance_m,
        # None passes straight through: "unknown", never zero.
        "ascent_m": trip.ascent_m,
        "descent_m": trip.descent_m,
        "started_at": None,
        "finished_at": None,
        "point_count": trip.point_count,
        "bounds": trip.bounds,
        "slope_samples": trip.slope_samples,
    }


def already_saved(user: "AbstractBaseUser | AnonymousUser", trip: Trip) -> bool:
    """Return whether ``user`` already holds a route matching ``trip``'s.

    Detected by exact GEOMETRY match on the viewer's own routes rather than
    by a marker row. Nothing links a saved copy back to the trip it came
    from — that is the deliberate choice this module's header states — so
    the only honest question left is "do you already have this track", and
    the geometry is what answers it.

    Narrowed on the cheap indexed-ish columns first (``point_count`` and
    ``distance_m``, which alone exclude essentially every unrelated route)
    and only then confirmed against ``points``, so the JSON comparison
    runs against a handful of rows rather than the user's whole list.

    A false negative costs a duplicate route the user can delete; a false
    positive would hide the control for a track they do not have. The
    ordering above is chosen so the second is only possible for two routes
    with identical geometry, which are the same track.

    Args:
        user: The viewer.
        trip: The trip whose route they may already hold.

    Returns:
        True when a matching route is already on their account.

    """
    if not user.is_authenticated:
        return False
    # Filtered on ``user_id`` rather than through ``Route.objects.for_user``:
    # the parameter is typed as the union a request's ``user`` actually is,
    # and only the pk of it is a lookup value every member of that union
    # supplies. Same narrowing ``is_participant`` makes next door.
    candidates = Route.objects.filter(
        user_id=user.pk,
        point_count=trip.point_count,
        distance_m=trip.distance_m,
    )
    return any(route.points == trip.points for route in candidates)


def save_trip_route(user: "User", trip: Trip) -> Route:
    """Copy ``trip``'s route onto ``user``'s own account.

    The copy inherits the trip's terrain record and is sampled only when
    there is none to inherit (SNOW-962 — the module docstring carries why
    this reversed SNOW-910). The enqueue still matters in that branch:
    nothing else would ever sample the copy, because the organiser's own
    task carries the SOURCE route's pk and the historical backfill runs
    once, so without it a trip-saved route drew flat for good beside an
    identical line that was coloured wherever else it appeared.

    Args:
        user: The authenticated viewer saving the route.
        trip: The trip whose snapshot is being copied. The caller has
            already established that this viewer may see it — through the
            share token or through their own participation — and this
            function does not re-decide that.

    Returns:
        The newly created Route, owned by ``user``.

    Raises:
        apps.routes.services.routes.RouteLimitReached: When ``user`` is
            already at ``settings.ROUTES_MAX_PER_USER``.

    """
    route = write_route_copy(user, _snapshot_fields(trip))

    # AFTER ``write_route_copy`` returns, which is what puts it outside
    # that function's ``atomic()`` block — the placement
    # ``enqueue_route_slope_sampling`` demands, and the one
    # ``create_route`` and ``claim_route_share`` both observe. Under
    # ``ImmediateBackend`` (dev, test AND staging) ``.enqueue()`` runs the
    # sampler inline, so an enqueue inside the block would hold the cap's
    # row lock for a tile-origin round trip per terrain tile the track
    # crosses.
    #
    # Guarded now, where SNOW-910 was unconditional: the snapshot usually
    # HAS a record to hand over, and re-walking the same track would ask
    # the tile origin a question already answered. The branch that remains
    # is the race — a trip created before its source route's own sampler
    # landed, which snapshotted a null.
    if route.slope_samples is None:
        enqueue_route_slope_sampling(route)

    logger.info(
        "Trip route saved: user=%s trip=%s route=%s",
        user.pk,
        trip.uuid,
        route.uuid,
    )
    return route
