"""
apps/public/area_items.py — the signed-in user's own things inside a downloaded area.

SNOW-965. ``/api/area-content/`` answers what PUBLIC content an area's
boundary contains — the region bulletins and the curated weather sheets —
and is the same answer for every caller, which is why it is cached. Two
things a person heading out needs are missing from it, and both are
private:

* **Their trips.** A trip's page and its ``route.geojson`` are in no
  download plan, so a trip inside a downloaded area did not open offline.
* **Their pins' forecasts.** A saved pin borrows the forecast of the
  nearest curated point (``apps.weather.services.nearest``), at any
  distance — so that point can sit outside the box, and its weather sheet
  was never fetched.

This module answers both for one user and one rectangle. It never selects
by real geometry, only by the same edge-inclusive rectangle rule
``apps.regions.services.area_content`` applies to the public content
(``docs/decisions/inside-the-boundary-is-complete.md``): a trip is kept
when its snapshot ``bounds`` touch the box, a pin when its point sits in
it. A borrowed forecast is returned even when the point it names is
outside the box — that is the whole reason it is here.

The answer is per-user and is never cached anywhere; the view that serves
it says so in its headers. See
``docs/decisions/an-area-download-carries-your-trips-and-borrowed-forecasts.md``.

Query cost is flat: an anonymous caller costs nothing, a signed-in one
costs one query for trips, one for pins and — only when at least one pin
was kept — one for the day's forecast candidates, however many pins there
are.

SNOW-1308 adds a ``name`` to every trip and pin, so the ``/offline/``
panels can list each one by what the reader called it. The name rides on
the same two queries (a trip's own label or its route's; a pin's own label
or its resort's) and costs nothing more.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, cast

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User

from apps.favourites.models import Favourite
from apps.regions.services.area_content import BBox, bboxes_overlap, point_in_bbox
from apps.trips.models import Trip
from apps.weather.services.nearest import forecast_candidates, nearest_candidate

logger = logging.getLogger(__name__)


def area_items(
    user: AbstractBaseUser | AnonymousUser,
    bbox: BBox,
    *,
    today: datetime.date,
) -> dict[str, Any]:
    """Return the user's trips and pins inside ``bbox``, with borrowed forecasts.

    Args:
        user: The requesting user. Anonymous gets the empty answer without
            a query — they have no trips and no pins.
        bbox: ``[west, south, east, north]`` in degrees, already validated
            by the caller.
        today: The day whose forecast a pin borrows — the same day the
            pin's own sheet reads.

    Returns:
        ``{"trips": [{"uuid", "name"}, …], "pins": [{"uuid", "name",
        "forecast"}, …]}``, where ``name`` is the label the reader sees
        (SNOW-1308; ``""`` for an unnamed pin, which the client words
        itself), and ``forecast`` is ``{"short_id"}`` for the curated point the
        pin borrows from, or ``None`` when no public point has a row for
        ``today``. Each list is in the queryset's own ordering.

    """
    if not user.is_authenticated:
        return {"trips": [], "pins": []}
    # Authenticated, so the concrete user every ``for_user`` takes.
    owner = cast("User", user)

    # SNOW-1308: the name a trip displays — its own label, or the route's
    # (``Trip.display_name``), read in the same query.
    trips = [
        {"uuid": str(trip_uuid), "name": name or route_name or ""}
        for trip_uuid, bounds, name, route_name in Trip.objects.for_user(
            owner
        ).values_list("uuid", "bounds", "name", "route_name")
        if bboxes_overlap(bbox, bounds)
    ]

    # SNOW-1308: and each pin's label — its own, or its resort's for a
    # saved resort — joined into the same query.
    rows = (
        Favourite.objects.for_user(owner)
        .placed()
        .values_list("uuid", "latitude", "longitude", "name", "resort__name")
    )
    placed = [
        (pin_uuid, latitude, longitude, name or resort_name or "")
        for pin_uuid, latitude, longitude, name, resort_name in rows
        # ``placed()`` excludes a null latitude; the longitude test is
        # the type checker's, the database's own constraint pairs them.
        if latitude is not None
        and longitude is not None
        and point_in_bbox(longitude, latitude, bbox)
    ]
    # Read only when a pin was kept: an area with none of the user's pins
    # in it has no forecast to borrow, and the scan is the one query here
    # whose cost grows with the estate.
    candidates = forecast_candidates(today) if placed else []
    pins = []
    for pin_uuid, latitude, longitude, label in placed:
        best = nearest_candidate(latitude, longitude, candidates)
        pins.append(
            {
                "uuid": str(pin_uuid),
                "name": label,
                "forecast": {"short_id": best[0].short_id} if best else None,
            }
        )
    logger.debug("area_items: %d trip(s), %d pin(s) in %s", len(trips), len(pins), bbox)
    return {"trips": trips, "pins": pins}
