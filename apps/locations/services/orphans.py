"""
apps/locations/services/orphans.py — the one rule for erasing a minted Location.

Provides ``delete_location_if_orphaned``: delete an anonymous ``Location``
once nothing references it any more. Favourites, trips and field
observations each mint an anonymous location holding a real coordinate,
and each must sweep it when the row that minted it goes — otherwise the
coordinate outlives the thing the person deleted (SNOW-1170).

One shared, public function rather than a private copy per app: the
erasure guarantee is the same everywhere, so it has one home.

Also ``prune_orphan_location_task``, ``prune_orphan_locations``' one task
per orphan (SNOW-1400): the sweep of anonymous rows left behind before the
per-delete rule above existed.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import ProtectedError
from django_tasks import task

from apps.core.command_tasks import BULK_TASK_PRIORITY
from apps.locations.models import Location

logger = logging.getLogger(__name__)


def delete_location_if_orphaned(location: Location) -> None:
    """Delete an anonymous Location that nothing references any more.

    Called after the row that referenced it has gone. Inline at the delete
    site rather than via a signal, per the project's no-signals-for-side-
    effects rule. A named location is curated data and is never deleted —
    a favourite, trip or report that happened to sit on Mont Fort must not
    delete Mont Fort.

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
        # later tickets add referents this function has never heard of. A
        # savepoint keeps the failed delete from poisoning the surrounding
        # atomic block.
        with transaction.atomic():
            location.delete()
    except ProtectedError:
        logger.debug("Location id=%s still referenced; left in place.", location.pk)


@task(priority=BULK_TASK_PRIORITY)
def prune_orphan_location_task(location_pk: int) -> bool:
    """Background worker: delete one orphaned location and its weather.

    ``prune_orphan_locations --commit``'s one task per orphan (SNOW-1400).
    Re-asks ``orphaned()`` for this pk under a row lock, so a location
    something has pointed at since it was enqueued, or one already gone,
    is left alone. Deleting it cascades to its ``Weather`` rows.

    Args:
        location_pk: The ``Location``'s primary key.

    Returns:
        Whether the location was deleted, kept on the task result.

    """
    with transaction.atomic():
        location = (
            Location.objects.select_for_update()
            .filter(pk=location_pk)
            .only("pk")
            .first()
        )
        if location is None or not (
            Location.objects.orphaned().filter(pk=location_pk).exists()
        ):
            logger.info("location pk=%s is not an orphan; left in place", location_pk)
            return False
        Location.objects.filter(pk=location_pk).delete()
    return True
