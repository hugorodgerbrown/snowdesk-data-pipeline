"""
apps/weather/services/upsert.py — The one sanctioned way to write a Weather row.

Contains a single function:

  upsert_weather(location, observed_on, **fields)
      Create the row when absent, update it in place when its day is today
      or later, and raise ``ImmutableWeatherRowError`` when it exists and
      its day has passed.

**Why a service rather than ``update_or_create`` at each call site.** The
rule is not "write the row"; it is "a day's account stands once the day is
over". That is a domain rule with one correct answer, and the three
behaviours it has to distinguish — create, refine, refuse — are not visible
in an ``update_or_create`` call. Business logic belongs in ``services/``
(CLAUDE.md); this is that logic.

**It raises rather than skipping.** The bug this model replaces (SNOW-628)
wrote zero rows for months without anyone noticing, because the write path
degraded quietly. A caller trying to rewrite a past day has a bug, and the
cost of surfacing it is one traceback against the cost of silently serving
a rewritten history.

``Weather.save()`` re-checks the same rule against the database as a
backstop for writes that never come through here — an admin form, a shell
session, a migration script.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.utils import timezone

from apps.locations.models import Location
from apps.weather.exceptions import ImmutableWeatherRowError
from apps.weather.models import Weather

logger = logging.getLogger(__name__)


def upsert_weather(
    location: Location,
    observed_on: date,
    **fields: Any,
) -> tuple[Weather, bool]:
    """
    Create or refine the ``Weather`` row for one location and one day.

    Three outcomes, decided by whether the row exists and whether its day
    has passed:

    * **absent** — created, whatever the date. Recording a day that was
      never recorded is not a rewrite, which is what lets a historical
      backfill (SNOW-731) use this same entry point rather than needing an
      exception to the rule.
    * **present, ``observed_on`` today or later** — updated in place. This
      is the scheduled fetch refining today's row on each of its four
      daily runs. In place, not appended: read paths key off the unique
      constraint and must stay a ``.first()``.
    * **present, ``observed_on`` past** — refused.

    Args:
        location: The location the row describes.
        observed_on: The calendar day the row is of.
        **fields: Column values to write. ``fetched_at`` is set here when
            the caller does not supply it, since it means "when this row
            was last written" and that is now.

    Returns:
        A ``(weather, created)`` tuple, where ``created`` is True for a new
        row and False for a refinement of an existing one.

    Raises:
        ImmutableWeatherRowError: The row exists and its day has passed.

    """
    fields.setdefault("fetched_at", timezone.now())

    existing = Weather.objects.filter(
        location=location, observed_on=observed_on
    ).first()

    if existing is None:
        weather = Weather.objects.create(
            location=location,
            observed_on=observed_on,
            **fields,
        )
        logger.debug(
            "upsert_weather: created location=%s observed_on=%s",
            location.pk,
            observed_on,
        )
        return weather, True

    if observed_on < timezone.localdate():
        raise ImmutableWeatherRowError(
            f"Weather(location={location.pk}, observed_on={observed_on}) is "
            f"past and cannot be rewritten. A row records what was known on "
            f"its day; write today's row instead."
        )

    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save()
    logger.debug(
        "upsert_weather: updated location=%s observed_on=%s",
        location.pk,
        observed_on,
    )
    return existing, False
