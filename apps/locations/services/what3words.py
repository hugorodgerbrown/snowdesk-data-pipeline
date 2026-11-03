"""
apps/locations/services/what3words.py — a Location's three word address.

Contains:

  fill_what3words(location)
      Returns a ``Location``'s stored address, converting and storing it
      first if there is not one yet.

  fill_what3words_task(location_pk, delay_s)
      The ``fill_what3words`` command's one task per unaddressed location
      (SNOW-1398, through ``apps.core.command_tasks.enqueue_each``). It
      re-reads the row, skips it once addressed, takes its turn on the
      ``WHAT3WORDS`` slot, and on a failed conversion opens that breaker
      for ``W3W_BACK_OFF_S`` — a failure is nearly always the upstream or
      the key, so the tasks behind it leave their rows for the next run
      rather than each failing in turn.

  what3words_map_url(words)
      Returns where one address links to on what3words' own map. Lives
      here rather than in a view because two apps render an address
      (SNOW-882).

The conversion itself — the HTTP call, the key, the offline fake — is
``convert_to_3wa`` in ``apps.ingest`` (SNOW-1249); see
``apps/ingest/sources/what3words.py`` for the cost, licence and language
rules. This module owns what needs a row or a page.

An address we derived from our own coordinate may be stored INDEFINITELY;
the 30-day cache ceiling in the terms governs the other direction of
travel. See docs/decisions/what3words-addresses-are-stored-indefinitely.md.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import timezone
from django_tasks import task

from apps.core.command_tasks import (
    BULK_TASK_PRIORITY,
    back_off,
    backing_off,
    calls_outside,
    wait_for_slot,
)
from apps.ingest.public import convert_to_3wa
from apps.locations.models import Location

logger = logging.getLogger(__name__)

# The slot and breaker name for the what3words API, and how long a failed
# conversion keeps every later task off it.
WHAT3WORDS = "what3words"
W3W_BACK_OFF_S = 300.0


class What3wordsUnavailable(RuntimeError):
    """A conversion came back empty, so the task fails and the row waits."""


def what3words_map_url(words: str | None) -> str | None:
    """Return the what3words map URL for one address, or None.

    SNOW-840, moved here from ``apps.trips.views`` by SNOW-882 when a
    second app needed it. ``{settings.WHAT3WORDS_MAP_BASE_URL}/{words}`` —
    their own map is the only place the 3m square can actually be SEEN, and
    a reader deciding whether they can find the spot needs to see it rather
    than take three words on trust.

    Built in Python rather than in the template on the codebase's usual
    rule: a URL is not presentation, and a template that concatenated a
    setting onto a variable would be the one place a trailing slash in the
    environment turned into a broken link. The base is stripped of one for
    that reason.

    Args:
        words: The address without its ``///`` prefix, or None when there
            is no address — flag off, no key, upstream down, not yet
            resolved.

    Returns:
        The absolute URL, or None when there is nothing to link to.

    """
    if not words:
        return None
    return f"{settings.WHAT3WORDS_MAP_BASE_URL.rstrip('/')}/{words}"


def fill_what3words(location: Location) -> str | None:
    """Return a location's three word address, converting it if need be.

    The single-row filler every path goes through — the ``fill_what3words``
    management command, the services that mint a location, and the trip
    view. Returns the stored address when there is one, and otherwise
    spends one conversion and writes the result back.

    A STORED ADDRESS IS NEVER RE-CONVERTED. It encodes a fixed 3m square,
    so it cannot go stale; the only thing that invalidates one is the pin
    moving, which clears the column at the point of the move rather than
    here.

    IDEMPOTENT, and safe to run concurrently. Two callers that both find
    the column empty will both convert and both write; they write the same
    words, and the second save is a no-op in effect. Locking to save a
    duplicate call would cost more than the call.

    Writes with ``update_fields`` so a fill touches the two columns and
    nothing else — it cannot clobber a concurrent edit of the location's
    coordinates.

    Args:
        location: The location to resolve. Saved in place when a
            conversion succeeds.

    Returns:
        The three word address, or None when there is none stored and the
        conversion did not succeed.

    """
    stored = location.three_word_address
    if stored is not None:
        return stored

    words = convert_to_3wa(location.latitude, location.longitude)
    if words is None:
        # By ROW ID, never by coordinate (SNOW-718) — this is the level
        # that has an id to name, which is why ``convert_to_3wa`` logs no
        # identity at all. The id is also the more useful of the two: it
        # is what you would look the row up by.
        logger.warning("what3words: no address for location id=%s", location.pk)
        # Deliberately no negative caching. A failure is nearly always the
        # upstream or the key rather than the square, so stamping "we
        # tried" would suppress the retry that fixes itself.
        return None

    location.what3words = words
    location.what3words_fetched_at = timezone.now()
    location.save(update_fields=["what3words", "what3words_fetched_at", "updated_at"])
    return words


@task(priority=BULK_TASK_PRIORITY)
def fill_what3words_task(location_pk: int, delay_s: float = 0.0) -> str | None:
    """Background worker: address one location for the backfill (SNOW-1398).

    Args:
        location_pk: Primary key of the ``Location`` to address.
        delay_s: Seconds between one conversion's start and the next.

    Returns:
        The three word address, kept on the task result, or None when the
        location is gone or addressed already, or the breaker is open.

    Raises:
        What3wordsUnavailable: The conversion failed. The breaker is open
            by then, and the row stays a candidate.

    """
    if backing_off(WHAT3WORDS):
        logger.info(
            "what3words: backing off; location id=%s left for the next run",
            location_pk,
        )
        return None
    location = Location.objects.unaddressed().filter(pk=location_pk).first()
    if location is None:
        return None
    wait_for_slot(WHAT3WORDS, delay_s)
    words = fill_what3words(location)
    if words is None:
        back_off(WHAT3WORDS, W3W_BACK_OFF_S)
        raise What3wordsUnavailable(f"no address for location id={location_pk}")
    return words


# Each task calls the what3words API, so enqueue_each refuses it unpaced.
calls_outside(fill_what3words_task)
