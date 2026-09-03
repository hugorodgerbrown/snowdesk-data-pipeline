"""
apps/favourites/services.py — Business logic for creating and deleting Favourites.

Provides ``create_favourite``, ``create_resort_favourite`` and
``delete_favourite`` — the mutating entry points used by
``apps/favourites/views.py`` — plus ``delete_favourites_for_user``, the
whole-roster variant account erasure calls.

Coordinate-argument convention: every function in this module takes
latitude/longitude in that order — ``(latitude, longitude)`` — matching
``apps.locations.services.elevation.fetch_elevation`` and
``apps.regions.services.point_match.region_for_point`` (both lat-first since
SNOW-426).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.favourites.models import Favourite
from apps.locations.models import Location
from apps.locations.services.elevation import fetch_elevation
from apps.regions.services.point_match import region_for_point

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)


class FavouriteLimitReached(Exception):
    """Raised when a user has reached ``settings.FAVOURITES_MAX_PER_USER``."""


class ResortNotGeocoded(Exception):
    """Raised when a Resort has no latitude/longitude to favourite from."""


def create_favourite(
    user: "User",
    latitude: float,
    longitude: float,
    name: str = "",
) -> Favourite:
    """Create a Favourite for the given user at the given location.

    Resolves the pin's elevation (an Open-Meteo HTTP call, kept outside
    any transaction) and a best-effort ``MicroRegion`` before writing the
    row. The per-user cap is checked once up front — to avoid
    an unnecessary Open-Meteo call when already over the limit — and again
    inside the transaction after locking the user row, which serialises
    concurrent creators so the cap can't be exceeded under a race (SNOW-465).

    Args:
        user: The authenticated user creating the favourite.
        latitude: WGS-84 latitude of the pin.
        longitude: WGS-84 longitude of the pin.
        name: Optional user-supplied label. Defaults to "".

    Returns:
        The newly created Favourite.

    Raises:
        FavouriteLimitReached: When the user already holds
            ``settings.FAVOURITES_MAX_PER_USER`` favourites.

    """
    if Favourite.objects.for_user(user).count() >= settings.FAVOURITES_MAX_PER_USER:
        raise FavouriteLimitReached(
            f"User {user.pk} has reached the "
            f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
        )

    # External HTTP call (Open-Meteo elevation lookup) — kept outside any
    # transaction so a slow or failing request never holds a DB lock.
    elevation = fetch_elevation(latitude, longitude)

    # Best-effort — may be None when the pin falls outside every known
    # boundary. region_for_point is lat-first (matching this module's
    # convention) since SNOW-426.
    region = region_for_point(latitude, longitude)

    with transaction.atomic():
        # Lock the user row before the cap re-check so concurrent creators
        # serialise here. A bare count() takes no lock, so under PostgreSQL
        # READ COMMITTED two requests could both read below the cap and both
        # insert, exceeding it. select_for_update() makes the second
        # transaction block until the first commits, so its count reflects the
        # first insert. On SQLite (tests) FOR UPDATE is a silent no-op.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        if Favourite.objects.for_user(user).count() >= settings.FAVOURITES_MAX_PER_USER:
            raise FavouriteLimitReached(
                f"User {user.pk} has reached the "
                f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
            )
        # The Location is minted inside the transaction with the favourite,
        # so a failed cap re-check cannot leave an orphan behind. It carries
        # no name and no kind: naming is a curation act, and this pin's
        # label is the user's own text, which stays on the favourite
        # (SNOW-704).
        location = Location.objects.create(
            latitude=latitude,
            longitude=longitude,
            elevation_m=elevation,
        )
        favourite = Favourite.objects.create(
            user=user,
            name=name,
            location=location,
            latitude=latitude,
            longitude=longitude,
            elevation=elevation,
            region=region,
        )

    logger.info(
        "Favourite created: user=%s location=%s region=%s",
        user.pk,
        location.pk,
        region.region_id if region else None,
    )
    return favourite


def create_resort_favourite(user: "User", resort: "Resort") -> Favourite:
    """Create (or return the existing) Favourite for a user's resort pin.

    Unlike ``create_favourite``, ``region`` is taken authoritatively from
    ``resort.region`` — a resort's parent region is a maintained fixture
    fact, not a best-effort point match — and ``name`` snapshots
    ``resort.name`` so a favourite created this way never renders blank in
    the favourites list even after ``resort`` degrades to null (SET_NULL).

    Idempotent on an existing ``(user, resort)`` favourite: a pre-check
    returns the existing row without a second Open-Meteo call, and the
    ``IntegrityError`` from the partial-unique constraint is caught as the
    race backstop (mirrors the cap re-check pattern in ``create_favourite``).

    Args:
        user: The authenticated user favouriting the resort.
        resort: The Resort being favourited. Must be geocoded.

    Returns:
        The newly created (or pre-existing) Favourite.

    Raises:
        ResortNotGeocoded: When ``resort`` has no latitude/longitude.
        FavouriteLimitReached: When the user already holds
            ``settings.FAVOURITES_MAX_PER_USER`` favourites.

    """
    if resort.latitude is None or resort.longitude is None:
        raise ResortNotGeocoded(f"Resort {resort.pk} ({resort.name}) is not geocoded.")

    existing = Favourite.objects.filter(user=user, resort=resort).first()
    if existing is not None:
        return existing

    if Favourite.objects.for_user(user).count() >= settings.FAVOURITES_MAX_PER_USER:
        raise FavouriteLimitReached(
            f"User {user.pk} has reached the "
            f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
        )

    # External HTTP call (Open-Meteo elevation lookup) — kept outside any
    # transaction so a slow or failing request never holds a DB lock.
    elevation = fetch_elevation(resort.latitude, resort.longitude)

    try:
        with transaction.atomic():
            # Lock the user row before the cap re-check — see create_favourite
            # for the full race-condition rationale.
            get_user_model().objects.select_for_update().get(pk=user.pk)
            if (
                Favourite.objects.for_user(user).count()
                >= settings.FAVOURITES_MAX_PER_USER
            ):
                raise FavouriteLimitReached(
                    f"User {user.pk} has reached the "
                    f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
                )
            # See create_favourite: minted inside the transaction, unnamed.
            location = Location.objects.create(
                latitude=resort.latitude,
                longitude=resort.longitude,
                elevation_m=elevation,
            )
            favourite = Favourite.objects.create(
                user=user,
                name=resort.name,
                location=location,
                latitude=resort.latitude,
                longitude=resort.longitude,
                elevation=elevation,
                region=resort.region,
                resort=resort,
            )
    except IntegrityError:
        # Race backstop: another request created the same (user, resort)
        # favourite between the pre-check above and this write. The partial-
        # unique constraint rejects our insert; return the row that won.
        existing = Favourite.objects.filter(user=user, resort=resort).first()
        if existing is None:
            raise
        return existing

    logger.info(
        "Resort favourite created: user=%s resort=%s region=%s",
        user.pk,
        resort.pk,
        resort.region.region_id,
    )
    return favourite


def create_region_favourite(
    user: "User", region: "MicroRegion", *, enforce_cap: bool = True
) -> Favourite:
    """Create (or return the existing) region pin for ``user`` on ``region``.

    A region pin (SNOW-802) is a ``Favourite`` whose subject is the region
    itself: no ``Location`` is minted, no elevation is fetched, and the row
    carries no coordinate — it is a bookmark on the region's bulletin, the
    thing a ``Subscription`` row always was. ``name`` snapshots the region's
    name so the row reads sensibly wherever it is listed.

    Idempotent on an existing ``(user, region)`` region pin: a pre-check
    returns the existing row, and the ``IntegrityError`` from the partial
    unique constraint is caught as the race backstop.

    Args:
        user: The authenticated user pinning the region.
        region: The region being pinned.
        enforce_cap: Whether ``settings.FAVOURITES_MAX_PER_USER`` applies.
            ``False`` for the one-time ``Subscription`` backfill — a user's
            existing regions must not be dropped on the floor because they
            also hold many placed pins.

    Returns:
        The newly created (or pre-existing) region pin.

    Raises:
        FavouriteLimitReached: When ``enforce_cap`` is set and the user
            already holds ``settings.FAVOURITES_MAX_PER_USER`` favourites.

    """
    existing = Favourite.objects.for_user(user).region_pins().filter(region=region)
    if (found := existing.first()) is not None:
        return found

    def _over_cap() -> bool:
        return (
            enforce_cap
            and Favourite.objects.for_user(user).count()
            >= settings.FAVOURITES_MAX_PER_USER
        )

    if _over_cap():
        raise FavouriteLimitReached(
            f"User {user.pk} has reached the "
            f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
        )

    try:
        with transaction.atomic():
            # Lock the user row before the cap re-check — see create_favourite
            # for the full race-condition rationale.
            get_user_model().objects.select_for_update().get(pk=user.pk)
            if _over_cap():
                raise FavouriteLimitReached(
                    f"User {user.pk} has reached the "
                    f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
                )
            favourite = Favourite.objects.create(
                user=user,
                name=region.name,
                region=region,
            )
    except IntegrityError:
        # The partial-unique constraint fired: a concurrent request pinned
        # the same region first. Return that row.
        logger.info(
            "Region pin race: user=%s region=%s already pinned; returning existing",
            user.pk,
            region.region_id,
        )
        return Favourite.objects.for_user(user).region_pins().get(region=region)

    logger.info("Region pin created: user=%s region=%s", user.pk, region.region_id)
    return favourite


def delete_region_favourite(user: "User", region: "MicroRegion") -> bool:
    """Delete ``user``'s region pin on ``region``, if there is one.

    Owner-checked by construction — only ``user``'s own row is addressed.
    No ``Location`` to sweep: a region pin never minted one.

    Args:
        user: The user whose pin is being removed.
        region: The pinned region.

    Returns:
        ``True`` when a pin was deleted, ``False`` when there was none.

    """
    deleted, _ = (
        Favourite.objects.for_user(user).region_pins().filter(region=region).delete()
    )
    if deleted:
        logger.info("Region pin deleted: user=%s region=%s", user.pk, region.region_id)
    return bool(deleted)


def delete_favourite(user: "User", uuid: UUID) -> None:
    """Delete the given user's favourite by uuid.

    Owner-checked: only deletes a row belonging to ``user``.

    The favourite's **anonymous** ``Location`` is deleted with it, once
    nothing else references it — leaving it behind would accumulate
    orphan rows nothing can reach. A **named** location is curated data
    and is never deleted here, whatever referenced it.

    Args:
        user: The authenticated user requesting the deletion.
        uuid: The Favourite's uuid.

    Raises:
        Favourite.DoesNotExist: When no matching favourite is owned by
            this user.

    """
    favourite = Favourite.objects.for_user(user).get(uuid=uuid)
    location = favourite.location
    with transaction.atomic():
        favourite.delete()
        if location is not None:
            _delete_location_if_orphaned(location)
    logger.info("Favourite deleted: user=%s uuid=%s", user.pk, uuid)


def delete_favourites_for_user(user: "User") -> int:
    """Delete every favourite owned by ``user``, minted Locations included.

    The whole-roster counterpart to ``delete_favourite``, for account
    erasure. ``Favourite.user`` is ``CASCADE``, so deleting the user removes
    the favourite rows in bulk — but a bulk cascade runs no Python, so the
    anonymous ``Location`` each favourite minted would survive with its real
    coordinates and elevation, referenced by nothing. Deleting the rows one
    at a time here, before the user goes, is what puts the orphan sweep back
    on the erasure path.

    Deleting the favourite first and asking about its location second means
    two favourites sharing one location still resolve correctly: the first
    pass leaves the location alone (still referenced), the last pass takes
    it.

    Args:
        user: The user whose favourites are being removed.

    Returns:
        The number of favourites deleted.

    """
    deleted = 0
    with transaction.atomic():
        for favourite in Favourite.objects.for_user(user).select_related("location"):
            location = favourite.location
            favourite.delete()
            if location is not None:
                _delete_location_if_orphaned(location)
            deleted += 1
    if deleted:
        logger.info("Favourites deleted for user=%s: %s row(s)", user.pk, deleted)
    return deleted


def _delete_location_if_orphaned(location: Location) -> None:
    """Delete an anonymous Location that nothing references any more.

    Called after the row that referenced it has gone. Inline at the delete
    site rather than via a signal, per the project's no-signals-for-side-
    effects rule.

    Args:
        location: The location whose last known referent was just removed.

    """
    if location.name:
        # Curated data. A user deleting a favourite that happened to sit on
        # Mont Fort must not delete Mont Fort.
        return
    try:
        # Ask the database rather than enumerating reverse relations. Every
        # referent of Location is PROTECT, so a ProtectedError *is* the
        # answer "something still references this" — and it stays correct as
        # SNOW-709 and SNOW-696 add referents this function has never heard
        # of. A savepoint keeps the failed delete from poisoning the
        # surrounding atomic block.
        with transaction.atomic():
            location.delete()
    except ProtectedError:
        logger.debug("Location id=%s still referenced; left in place.", location.pk)
