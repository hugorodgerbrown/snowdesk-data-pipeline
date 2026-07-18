"""
favourites/services.py — Business logic for creating and deleting Favourites.

Provides ``create_favourite`` and ``delete_favourite``, the two mutating
entry points used by ``favourites/views.py``.

Coordinate-argument convention: every function in this module takes
latitude/longitude in that order — ``(latitude, longitude)`` — matching
``bulletins.services.forecast_points.resolve_forecast_point`` and
``regions.services.point_match.region_for_point`` (both lat-first since
SNOW-426).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import transaction

from bulletins.services.forecast_points import resolve_forecast_point
from favourites.models import Favourite
from regions.services.point_match import region_for_point

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


class FavouriteLimitReached(Exception):
    """Raised when a user has reached ``settings.FAVOURITES_MAX_PER_USER``."""


def create_favourite(
    user: "User",
    latitude: float,
    longitude: float,
    name: str = "",
) -> Favourite:
    """Create a Favourite for the given user at the given location.

    Resolves the pin to a shared ``ForecastPoint`` (an Open-Meteo HTTP call,
    kept outside any transaction) and a best-effort ``MicroRegion`` before
    writing the row. The per-user cap is checked once up front — to avoid
    an unnecessary Open-Meteo call when already over the limit — and again
    inside the transaction to narrow the race window.

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
    forecast_point = resolve_forecast_point(latitude, longitude)

    # Best-effort — may be None when the pin falls outside every known
    # boundary. region_for_point is lat-first (matching this module's
    # convention) since SNOW-426.
    region = region_for_point(latitude, longitude)

    with transaction.atomic():
        # Re-check the cap inside the transaction to narrow the race window
        # between the first check and this write.
        if Favourite.objects.for_user(user).count() >= settings.FAVOURITES_MAX_PER_USER:
            raise FavouriteLimitReached(
                f"User {user.pk} has reached the "
                f"{settings.FAVOURITES_MAX_PER_USER}-favourite limit."
            )
        favourite = Favourite.objects.create(
            user=user,
            name=name,
            latitude=latitude,
            longitude=longitude,
            elevation=forecast_point.elevation,
            forecast_point=forecast_point,
            region=region,
        )

    logger.info(
        "Favourite created: user=%s forecast_point=%s region=%s",
        user.pk,
        forecast_point.pk,
        region.region_id if region else None,
    )
    return favourite


def delete_favourite(user: "User", uuid: UUID) -> None:
    """Delete the given user's favourite by uuid.

    Owner-checked: only deletes a row belonging to ``user``. The linked
    ``ForecastPoint`` is never touched — ``on_delete=PROTECT`` means it
    survives regardless (it may be shared by other favourites).

    Args:
        user: The authenticated user requesting the deletion.
        uuid: The Favourite's uuid.

    Raises:
        Favourite.DoesNotExist: When no matching favourite is owned by
            this user.

    """
    favourite = Favourite.objects.for_user(user).get(uuid=uuid)
    favourite.delete()
    logger.info("Favourite deleted: user=%s uuid=%s", user.pk, uuid)
