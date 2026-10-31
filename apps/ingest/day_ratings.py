"""
apps/ingest/day_ratings.py — The service's day ratings, from its own bulletins.

``SourceDayRating`` (SNOW-1270) is the service's ``RegionDayRating``: for
each (region code, day), the danger range the governing bulletin gives
that day. It is computed here from ``SourceBulletin`` rows alone, by the
same rules an instance uses — the latest-``valid_from`` bulletin whose
target day is the day and whose render model is current
(``target_day_for_valid_from``, ``rate_render_model``) — so every instance
can copy the service's numbers instead of computing its own.

  index_bulletin(row)
      Fill a ``SourceBulletin``'s ``valid_from``, ``target_date`` and
      ``region_codes`` from its CAAML. Called on every write.

  day_pairs(row)
      The (region code, day) pairs a bulletin can govern.

  refresh_source_day_ratings(pairs)
      Recompute those pairs, writing a row only when its value changes.
      ``store_bulletin`` calls it with the pairs a write touched, before
      and after, so a correction that drops a region re-rates that region
      too (the rule SNOW-1168 set for the app).

  lock_source_day_ratings(pairs)
      Lock those pairs' rows, creating any missing first, in sorted
      (region code, day) order. A caller recomputing the pairs from a
      background task holds the locks for its transaction, so two sibling
      bulletins' tasks on one pair recompute one after the other and the
      second reads the first's committed write (SNOW-1346).

The regions themselves are code-held fixtures every side loads; the
service needs only their codes, which every bulletin carries.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from django.db import IntegrityError, models

from apps.ingest.models import SourceBulletin, SourceDayRating
from apps.ingest.normalise.day_rating import (
    DAY_RATING_VERSION,
    DayRating,
    rate_render_model,
    target_day_for_valid_from,
    unrated_day,
)
from apps.ingest.normalise.render_model import RENDER_MODEL_VERSION
from apps.ingest.normalise.slf import parse_dt

logger = logging.getLogger(__name__)

# The SourceDayRating columns a refresh decides.
DAY_RATING_FIELDS: tuple[str, ...] = (
    "bulletin_id",
    "min_rating",
    "min_subdivision",
    "max_rating",
    "max_subdivision",
    "am_rating",
    "am_subdivision",
    "pm_rating",
    "pm_subdivision",
    "source",
    "bands",
    "version",
)

# Sorts before every real valid_from.
_EARLIEST = datetime.min.replace(tzinfo=UTC)

# The SourceBulletin columns index_bulletin fills.
INDEX_FIELDS: tuple[str, ...] = ("valid_from", "target_date", "region_codes")


def index_bulletin(row: SourceBulletin) -> None:
    """Fill ``row``'s day-rating index from its CAAML, without saving.

    A CAAML with no parseable ``validTime.startTime`` gets no target day,
    and so governs no day.

    Args:
        row: The bulletin, with ``caaml`` loaded.

    """
    caaml = row.caaml or {}
    start = (caaml.get("validTime") or {}).get("startTime")
    try:
        row.valid_from = parse_dt(start) if start else None
    except ValueError:
        row.valid_from = None
    row.target_date = (
        target_day_for_valid_from(row.valid_from) if row.valid_from else None
    )
    row.region_codes = sorted(
        {
            str(region["regionID"])
            for region in caaml.get("regions") or []
            if isinstance(region, dict) and region.get("regionID")
        }
    )


def day_pairs(row: SourceBulletin) -> set[tuple[str, date]]:
    """Return the (region code, day) pairs ``row`` can govern.

    Args:
        row: The bulletin, with ``target_date`` and ``region_codes`` loaded.

    Returns:
        One pair per region code on its target day; empty with no day.

    """
    if row.target_date is None:
        return set()
    return {(code, row.target_date) for code in row.region_codes or []}


def _values(rating: DayRating, bulletin_id: str) -> dict[str, Any]:
    """Return the ``SourceDayRating`` column values for one rating."""
    return {
        "bulletin_id": bulletin_id if rating["rated"] else "",
        "min_rating": rating["min_rating"],
        "min_subdivision": rating["min_subdivision"],
        "max_rating": rating["max_rating"],
        "max_subdivision": rating["max_subdivision"],
        "am_rating": rating["am_rating"],
        "am_subdivision": rating["am_subdivision"],
        "pm_rating": rating["pm_rating"],
        "pm_subdivision": rating["pm_subdivision"],
        "source": rating["source"],
        "bands": rating["bands"],
        "version": DAY_RATING_VERSION,
    }


def refresh_source_day_ratings(
    pairs: Iterable[tuple[str, date]], *, commit: bool = True
) -> int:
    """Recompute the service's rating for each (region code, day).

    One query per day for the candidates, one per governing bulletin for
    its render model, and one per day for the rows already held. A pair no
    bulletin governs is written as ``no_rating``, as the app writes it.

    Args:
        pairs: The (region code, day) pairs to recompute.
        commit: Write; otherwise only count.

    Returns:
        How many rows were (or would be) written: new, or changed.

    """
    by_day: dict[date, set[str]] = defaultdict(set)
    for code, day in pairs:
        by_day[day].add(code)

    written = 0
    for day, codes in sorted(by_day.items()):
        candidates = list(
            SourceBulletin.objects.filter(
                target_date=day, render_model_version__gte=RENDER_MODEL_VERSION
            ).only("pk", "bulletin_id", "valid_from", "region_codes")
        )
        held = {
            row.region_code: row
            for row in SourceDayRating.objects.filter(date=day, region_code__in=codes)
        }
        models: dict[int, dict[str, Any]] = {}
        for code in sorted(codes):
            chosen = max(
                (c for c in candidates if code in (c.region_codes or [])),
                # A row with a target day has a valid_from; the fallback
                # only satisfies the type.
                key=lambda c: c.valid_from or _EARLIEST,
                default=None,
            )
            if chosen is None:
                values = _values(unrated_day(), "")
            else:
                if chosen.pk not in models:
                    models[chosen.pk] = (
                        SourceBulletin.objects.filter(pk=chosen.pk)
                        .values_list("render_model", flat=True)
                        .get()
                    )
                values = _values(
                    rate_render_model(models[chosen.pk], chosen.bulletin_id),
                    chosen.bulletin_id,
                )
            row = held.get(code)
            if row is not None and all(
                getattr(row, name) == value for name, value in values.items()
            ):
                continue
            written += 1
            if commit:
                SourceDayRating.objects.update_or_create(
                    region_code=code, date=day, defaults=values
                )
    logger.debug(
        "refresh_source_day_ratings: %d pair(s) over %d day(s), %d written",
        sum(len(codes) for codes in by_day.values()),
        len(by_day),
        written,
    )
    return written


def lock_source_day_ratings(pairs: Iterable[tuple[str, date]]) -> None:
    """Lock each (region code, day)'s ``SourceDayRating`` row until commit.

    A missing row is created first — as the unrated day, which is what a
    refresh writes for a pair no bulletin governs — so there is a row to
    lock; a concurrent create of the same row is retried once as a read.
    The rows are then locked in one ``SELECT … FOR UPDATE`` ordered by
    (region code, day), so two callers locking overlapping pairs take
    them in the same order and cannot deadlock. Must be called inside
    ``transaction.atomic()``; a backend without row locks (SQLite) skips
    the lock and keeps the create.

    Args:
        pairs: The (region code, day) pairs about to be recomputed.

    """
    keys = sorted(set(pairs))
    if not keys:
        return
    placeholder = _values(unrated_day(), "")
    match = models.Q()
    for code, day in keys:
        try:
            SourceDayRating.objects.get_or_create(
                region_code=code, date=day, defaults=placeholder
            )
        except IntegrityError:
            # Another transaction created it between our read and insert.
            SourceDayRating.objects.get_or_create(
                region_code=code, date=day, defaults=placeholder
            )
        match |= models.Q(region_code=code, date=day)
    list(
        SourceDayRating.objects.select_for_update()
        .filter(match)
        .order_by("region_code", "date")
        .values_list("pk", flat=True)
    )
