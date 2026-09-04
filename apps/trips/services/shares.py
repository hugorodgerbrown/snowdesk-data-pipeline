"""
apps/trips/services/shares.py — the one link a trip has (SNOW-821).

Two entry points, ``mint_trip_share`` and ``revoke_trip_share``, both
organiser-scoped.

**A trip has ONE link, and the token lives on the row.** There is no
``TripShare`` model. ``RouteShare`` is a separate table because a route is
handed out in many independent grants, each with claim counters worth
auditing; a trip is one object with one roster, so a second grant would be a
second name for the same thing. See
``docs/decisions/a-trip-is-one-object-with-a-roster.md`` for the cost that
buys and why a trip does not need it.

**The window is measured from the trip's DATE, never from the mint time.**
A share link that expired ``TRIP_SHARE_MAX_AGE_DAYS`` after it was created
would die two months before a trip planned three months out — the one case
where a person definitely still needs it. ``ROUTE_SHARE_MAX_AGE_DAYS``
measures from the mint because a route has no date of its own to measure
from; a trip does, and it is the whole point of the object.

Token minting follows ``apps.routes.services.shares.create_route_share``
exactly: ``secrets.token_urlsafe(8)`` — eleven URL-safe characters, ~66 bits
— behind the same collision-retry loop and the same ceiling.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, time, timedelta, timezone as dt_timezone
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError

from apps.trips.models import Trip

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# How many times a token collision is retried before giving up. Mirrors
# ``apps.routes.services.shares._TOKEN_MAX_RETRIES``: with ~66 bits of
# entropy a single collision is already implausible, so this is a runaway
# guard rather than a realistic path.
_TOKEN_MAX_RETRIES = 5


class TripShareTokenCollision(Exception):
    """Raised when a unique token could not be minted after every retry."""


def share_expiry_for(trip: Trip) -> datetime:
    """Return when ``trip``'s share link should stop working.

    Midnight UTC at the END of ``trip.date`` plus
    ``settings.TRIP_SHARE_MAX_AGE_DAYS``. The trip's own wall-clock date is
    anchored to UTC rather than converted from a timezone the trip does not
    record: an expiry is a coarse boundary measured in weeks, so the worst
    a fixed anchor costs is a few hours at one end, and inventing a
    timezone for it would be inventing a fact (see ``Trip.date``).

    Args:
        trip: The trip whose link is being minted.

    Returns:
        An aware datetime, the moment the link stops working.

    """
    end_of_day = datetime.combine(trip.date, time.max, tzinfo=dt_timezone.utc)
    return end_of_day + timedelta(days=settings.TRIP_SHARE_MAX_AGE_DAYS)


def mint_trip_share(user: "User", uuid: UUID) -> Trip:
    """Mint (or re-mint) the share link for one of the organiser's own trips.

    Organiser-scoped by the lookup, not by a check afterwards: a uuid that
    is not this user's raises ``Trip.DoesNotExist``, which the view turns
    into a 404 rather than a 403, so a probing request cannot tell "not
    yours" from "doesn't exist".

    **Re-minting ROTATES.** Calling this on a trip that already has a live
    link replaces the token, so the previous link stops working — which is
    the only revoke-and-reshare an organiser has, and the reason a link
    sent to the wrong person is recoverable at all.

    Args:
        user: The authenticated organiser.
        uuid: The Trip's uuid.

    Returns:
        The trip, with ``share_token`` and ``share_expires_at`` set.

    Raises:
        Trip.DoesNotExist: When no trip with that uuid was organised by
            ``user``.
        TripShareTokenCollision: When ``_TOKEN_MAX_RETRIES`` unique-token
            attempts all collided.

    """
    trip = Trip.objects.get(uuid=uuid, created_by=user)
    expires_at = share_expiry_for(trip)

    for attempt in range(_TOKEN_MAX_RETRIES):
        trip.share_token = secrets.token_urlsafe(8)
        trip.share_expires_at = expires_at
        try:
            # ``updated_at`` is auto_now, applied in Python on save() and
            # skipped for any field absent from update_fields — so it is
            # listed, or the column is left stale.
            trip.save(update_fields=["share_token", "share_expires_at", "updated_at"])
        except IntegrityError:
            # ``share_token`` is the only unique column that can collide
            # here, so an IntegrityError is a collision and nothing else.
            logger.warning("trip_share: token collision, retrying")
            if attempt == _TOKEN_MAX_RETRIES - 1:
                logger.error(
                    "trip_share: token collision after %d retries",
                    _TOKEN_MAX_RETRIES,
                )
            continue

        logger.info(
            "Trip share minted: user=%s trip=%s token=%s",
            user.pk,
            trip.uuid,
            trip.share_token,
        )
        return trip

    raise TripShareTokenCollision(
        f"Could not mint a unique trip-share token in {_TOKEN_MAX_RETRIES} attempts."
    )


def revoke_trip_share(user: "User", uuid: UUID) -> Trip:
    """Stop one of the organiser's own trips being reachable by link.

    Nulls the token rather than expiring it. A nulled token cannot be
    reached by any read path — ``TripQuerySet.shared()`` filters on
    ``share_token__isnull=False`` — and it frees the value, so a later
    re-share genuinely mints a new link rather than reviving the one that
    was already sent to the wrong person.

    Idempotent: revoking an unshared trip is not an error.

    Args:
        user: The authenticated organiser.
        uuid: The Trip's uuid.

    Returns:
        The trip, with no live link.

    Raises:
        Trip.DoesNotExist: When no trip with that uuid was organised by
            ``user``.

    """
    trip = Trip.objects.get(uuid=uuid, created_by=user)
    trip.share_token = None
    trip.share_expires_at = None
    trip.save(update_fields=["share_token", "share_expires_at", "updated_at"])
    logger.info("Trip share revoked: user=%s trip=%s", user.pk, uuid)
    return trip
