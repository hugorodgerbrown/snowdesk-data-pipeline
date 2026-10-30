"""
apps/bulletins/services/selection.py — which issue a day shows.

Up to three SLF issues can touch one calendar day — the previous
evening's, the morning update, and that evening's — and picking between
them is a rule rather than a query. It lived in ``apps/public/views.py``
as three private helpers until SNOW-839 needed the same answer on a
surface that is not the bulletin page.

**THE RULE HAS TO BE SHARED, NOT COPIED.** A trip page that reimplemented
it would eventually show a different issue from the bulletin page for the
same region on the same day, and neither would be wrong on its own terms
— which is the worst kind of disagreement, because nothing fails. Same
move ``track/slope_wire.py`` made one ticket earlier, for
the same reason: a second caller in another app is what promotes a
private helper to a service.

SNOW-1207 made the rule the one the map already used: a bulletin belongs
to the day it forecasts (``Bulletin.target_date``), and the day's latest
such issue is the one shown, whatever the time of day.
"""

from __future__ import annotations

import datetime

from apps.bulletins.models import Bulletin
from apps.regions.models import MicroRegion


def issues_for_date(
    region: MicroRegion,
    target_date: datetime.date,
) -> list[Bulletin]:
    """
    Return the bulletins that forecast a calendar day.

    **A bulletin belongs to the day it forecasts** (SNOW-1207) — its
    stored ``target_date``, set at ingest by ``target_day_for_valid_from``.
    Up to two SLF issues forecast day D:

    * the previous evening's issue (valid ``D-1 17:00 → D 17:00``),
    * the same-day morning update  (valid ``D 08:00  → D 17:00``).

    D's own evening issue (valid ``D 17:00 → D+1 17:00``) forecasts D+1
    and belongs to D+1, although its window touches D. This is the rule
    the map fill and the calendar already read (``recompute_region_day``
    filters on the same column), so every surface agrees on the day.
    Until SNOW-1207 this asked for any window that *touched* D, and after
    17:00 on today the panels showed tomorrow's issue over a map still
    painted with today's.

    The result is sorted by ``valid_from`` ascending, earlier issue first.

    Args:
        region: The MicroRegion to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        A chronologically-sorted list of Bulletins (possibly empty).

    """
    return list(
        Bulletin.objects.for_target_date(target_date)
        .filter(regions=region)
        .order_by("valid_from")
    )


def select_default_issue(
    issues: list[Bulletin],
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Pick the default bulletin from a day's issues: the latest one.

    ``issues`` holds only the issues that forecast ``target_date`` (see
    :func:`issues_for_date`), so the latest ``valid_from`` is the morning
    update when there is one and the previous evening's issue otherwise —
    the same pick ``recompute_region_day`` makes for the map. The choice
    does not depend on the time of day, so a day's bulletin never changes
    under the reader at 17:00 (SNOW-1207 retired the "in force now" pivot
    for today and the 10:00 UTC pivot for other days).

    Args:
        issues: Day's issues, chronologically sorted.
        target_date: Calendar date identifying the day being displayed;
            kept for callers' signatures, the pick no longer reads it.

    Returns:
        The default Bulletin to render, or ``None`` when no issues exist.

    """
    if not issues:
        return None
    return max(issues, key=lambda b: b.valid_from)


def select_bulletin_for_date(
    region: MicroRegion,
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Return the default bulletin to display for a region on a given date.

    Thin wrapper over :func:`issues_for_date` +
    :func:`select_default_issue`.  Exposed as a named helper because
    other views (``examples_random``) depend on picking a single
    default without knowing about the full issue list.

    Args:
        region: The MicroRegion to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        The default Bulletin for the day, or ``None`` if no bulletins exist.

    """
    return select_default_issue(issues_for_date(region, target_date), target_date)
