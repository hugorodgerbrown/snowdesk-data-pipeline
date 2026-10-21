"""
apps/observations/services/observations.py — deleting field observations.

Provides ``delete_observation_instance`` (one row, already loaded),
``delete_observation`` (one of a user's own rows, by uuid) and
``delete_observations_for_user`` (the whole set, for account erasure).

Every delete path — the user's own delete view, account erasure and both
admin deletes — routes through here, because a report mints an anonymous
``Location`` holding the exact coordinate it was filed at.
``FieldObservation.location`` is ``PROTECT``, so a plain ``delete()`` or a
cascade from the user removes the report and strands that coordinate
indefinitely (SNOW-1170). Each delete removes the row, then sweeps its
location with ``delete_location_if_orphaned``, inline rather than via a
signal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import transaction

from apps.locations.services.orphans import delete_location_if_orphaned
from apps.observations.models import FieldObservation

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def delete_observation_instance(observation: FieldObservation) -> None:
    """Delete one field observation and sweep the location it minted.

    The row and the sweep share one atomic block, so a failed sweep leaves
    the report in place rather than deleting it and stranding its
    coordinate. A pre-SNOW-709 row with no location deletes cleanly.

    Args:
        observation: The observation to delete.

    """
    with transaction.atomic():
        location = observation.location
        observation.delete()
        if location is not None:
            delete_location_if_orphaned(location)


def delete_observation(user: "User", uuid: UUID) -> None:
    """Delete one of the user's own field observations, location included.

    Owner-scoped: the lookup filters on ``user``, so another user's uuid is
    not found rather than deleted.

    Args:
        user: The user whose observation to delete.
        uuid: The observation's uuid.

    Raises:
        FieldObservation.DoesNotExist: When the user owns no observation
            with that uuid.

    """
    observation = FieldObservation.objects.select_related("location").get(
        uuid=uuid, user=user
    )
    delete_observation_instance(observation)


def delete_observations_for_user(user: "User") -> int:
    """Delete every field observation the user filed, locations included.

    Account erasure calls this before deleting the user: the ``CASCADE`` on
    ``FieldObservation.user`` would delete the rows in bulk and leave every
    minted location behind. Deletes one row at a time so each location is
    swept as its last referent goes, mirroring
    ``apps.favourites.services.delete_favourites_for_user``.

    Args:
        user: The user whose observations to delete.

    Returns:
        The number of observations deleted.

    """
    deleted = 0
    with transaction.atomic():
        for observation in FieldObservation.objects.for_user(user).select_related(
            "location"
        ):
            delete_observation_instance(observation)
            deleted += 1
    if deleted:
        logger.info("Observations deleted for user=%s: %s row(s)", user.pk, deleted)
    return deleted
