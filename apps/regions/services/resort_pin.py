"""apps/regions/services/resort_pin.py — the one writer of a resort's map pin.

A resort's pin is a ``Location`` (SNOW-1144,
``docs/decisions/location-is-the-primitive.md``): ``Resort.location`` is the
row its weather hangs off, so a resort pinned in the map editor has weather
from the next ``fetch_weather`` run without an operator linking anything.
Before this, the pin was two floats and the ``Location`` was a separate
manual step (``link_resort_locations``) that production was routinely
behind on.

Every path that places, moves or clears a pin comes through
``set_resort_pin`` — the edit-resorts endpoints, ``import_resorts``, the
admin, and the one-time ``link_resort_locations`` backfill — so the rules
below are stated once:

* **Reuse, never duplicate.** The resort's own ``location`` first; failing
  that, the ``Location`` its primary ``ResortLocation`` already points at
  (the anonymous row the pre-SNOW-1144 ``link_resort_locations`` minted);
  failing that, ``anchor_location``, which reuses an anonymous row already
  at the coordinate.
* **A move is a correction of the same row.** When the pin moves and the row
  belongs to this resort alone, it is updated in place, so its ``Weather``
  history stays attached — the rule ``edit_location_save`` follows for a
  curated point. The coordinate-derived columns die with the old
  coordinate: ``what3words`` is cleared, and ``elevation_m`` becomes the
  resort's recorded ``base_elevation_m`` (the height ``anchor_location`` is
  given), or null for a later resolution pass.
* **A shared row is never moved.** A named place, or one another resort, a
  region centroid, a favourite, an observation or a trip also reaches, is
  someone else's too; the pin is re-anchored instead and the shared row is
  left exactly where it was.
* **Clearing a pin clears the FK only.** The ``Location`` and its weather
  stay; ``prune_orphan_locations`` collects the row if nothing else wants it.

During the transition the service also mirrors the coordinate onto
``Resort.latitude``/``longitude``, which ``dump_resorts_sheet``,
``audit_resort_regions`` and the edit panel's payload still read.
"""

from __future__ import annotations

import logging

from django.db import transaction

from apps.locations.models import Location
from apps.locations.services.anchor import anchor_location
from apps.regions.models import Resort

logger = logging.getLogger(__name__)

# The resort columns this service owns. A caller that saves the resort itself
# with ``update_fields`` must leave these out — the service has written them.
PIN_FIELDS = ("latitude", "longitude", "location")


def _current_location(resort: Resort) -> Location | None:
    """Return the Location this resort's pin already uses, if any.

    The FK when set; otherwise the Location behind the resort's primary
    ``ResortLocation`` — the row the pre-SNOW-1144 ``link_resort_locations``
    minted at the pin, which carries the resort's weather history and must
    be adopted rather than duplicated.

    Args:
        resort: The resort whose pin is being written.

    Returns:
        The existing Location, or ``None`` when the resort has none.

    """
    if resort.location_id is not None:
        return resort.location
    link = (
        resort.resort_locations.primary()
        .select_related("location")
        .order_by("id")
        .first()
    )
    return link.location if link is not None else None


def _belongs_to_resort_alone(location: Location, resort: Resort) -> bool:
    """Return whether moving this Location would move nothing but this pin.

    Anonymous, and reached by no other resort's pin or link, no region
    centroid, no favourite, no field observation and no trip. Asked only
    when a pin actually moves, so the handful of ``exists()`` queries is
    paid on an editor save, never on a read.

    Args:
        location: The candidate row.
        resort: The resort whose pin it is.

    Returns:
        True when the row can be corrected in place.

    """
    if location.name:
        return False
    return not (
        location.pinned_resorts.exclude(pk=resort.pk).exists()
        or location.resort_locations.exclude(resort_id=resort.pk).exists()
        or location.micro_regions.exists()
        or location.favourites.exists()
        or location.field_observations.exists()
        or location.trips.exists()
    )


def _move_in_place(
    location: Location, latitude: float, longitude: float, resort: Resort
) -> None:
    """Re-place a resort's own pin Location, keeping the row and its weather.

    Args:
        location: The row to correct.
        latitude: The new latitude.
        longitude: The new longitude.
        resort: The resort whose pin it is — its ``base_elevation_m`` is
            the new height, as ``anchor_location`` is given at mint time.

    """
    location.latitude = latitude
    location.longitude = longitude
    location.elevation_m = (
        float(resort.base_elevation_m) if resort.base_elevation_m is not None else None
    )
    # SNOW-840: the address is derived from the coordinate, so it dies with
    # it — the rule edit_location_save applies to a curated point.
    location.what3words = None
    location.what3words_fetched_at = None
    location.save(
        update_fields=[
            "latitude",
            "longitude",
            "elevation_m",
            "what3words",
            "what3words_fetched_at",
            "updated_at",
        ]
    )


@transaction.atomic
def set_resort_pin(
    resort: Resort, latitude: float | None, longitude: float | None
) -> Location | None:
    """Place, move or clear a resort's map pin, and persist it.

    Writes ``resort.location`` and the mirrored ``latitude``/``longitude``
    and saves just those columns (plus ``updated_at``) — and only when one
    of them changed, so re-running it over an unchanged estate writes
    nothing. See the module docstring for the reuse and move rules.

    Args:
        resort: A saved resort.
        latitude: The pin's latitude, or ``None`` with ``longitude`` to
            clear the pin.
        longitude: The pin's longitude, or ``None`` with ``latitude``.

    Returns:
        The pin's Location, or ``None`` when the pin was cleared.

    Raises:
        ValueError: The resort is unsaved, or only one of the pair is given.

    """
    if resort.pk is None:
        raise ValueError("set_resort_pin needs a saved resort.")
    if (latitude is None) != (longitude is None):
        raise ValueError("A resort pin needs both latitude and longitude, or neither.")

    location: Location | None
    if latitude is None or longitude is None:
        location = None
    else:
        current = _current_location(resort)
        if current is not None and (current.latitude, current.longitude) == (
            latitude,
            longitude,
        ):
            location = current
        elif current is not None and _belongs_to_resort_alone(current, resort):
            _move_in_place(current, latitude, longitude, resort)
            location = current
            logger.info(
                "set_resort_pin: moved location id=%s for resort id=%s",
                current.pk,
                resort.pk,
            )
        else:
            location = anchor_location(
                latitude,
                longitude,
                float(resort.base_elevation_m)
                if resort.base_elevation_m is not None
                else None,
            )

    changed = (
        resort.latitude != latitude
        or resort.longitude != longitude
        or resort.location_id != (location.pk if location is not None else None)
    )
    resort.latitude = latitude
    resort.longitude = longitude
    resort.location = location
    if changed:
        resort.save(update_fields=[*PIN_FIELDS, "updated_at"])
    return location
