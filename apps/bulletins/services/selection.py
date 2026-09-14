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
move ``apps/routes/services/slope_wire.py`` made one ticket earlier, for
the same reason: a second caller in another app is what promotes a
private helper to a service.

The functions are unchanged from the originals; only their home and the
leading underscore are.
"""

from __future__ import annotations

import datetime

from django.utils import timezone

from apps.bulletins.models import Bulletin
from apps.regions.models import MicroRegion


def issues_for_date(
    region: MicroRegion,
    target_date: datetime.date,
) -> list[Bulletin]:
    """
    Return all bulletins whose validity window overlaps a calendar day.

    Up to three SLF issues can touch a single day:

    * the previous-day evening issue (valid ``D-1 17:00 → D 17:00``),
    * the same-day morning update  (valid ``D 08:00  → D 17:00``),
    * the same-day evening issue    (valid ``D 17:00 → D+1 17:00``).

    The query captures all three by asking for windows that *intersect*
    day D: ``valid_from.date() <= D AND valid_to.date() >= D``.

    The result is sorted by ``valid_from`` ascending so that rendering
    the list chronologically matches the mental model of earlier → later
    issue times on the day.

    Args:
        region: The MicroRegion to look up.
        target_date: Calendar date identifying the day to display.

    Returns:
        A chronologically-sorted list of Bulletins (possibly empty).

    """
    return list(
        Bulletin.objects.filter(
            regions=region,
            valid_from__date__lte=target_date,
            valid_to__date__gte=target_date,
        ).order_by("valid_from")
    )


def select_default_issue(
    issues: list[Bulletin],
    target_date: datetime.date,
) -> Bulletin | None:
    """
    Pick the default bulletin from a day's issues.

    * For **today**, prefer the issue whose window contains *now* — the
      bulletin being live-published to the public right this moment.
    * For any other day (past or future), prefer the issue whose window
      contains **10:00 UTC** on that calendar day.  10:00 sits after the
      08:00 morning update but before the 17:00 evening rollover, so it
      picks the morning update when it exists and falls back to the
      previous day's evening issue (which is also valid at 10:00) when
      it doesn't — matching SLF's "what did the current day-time
      forecast say?" convention.

    Falls back to the last issue in the list (the latest by
    ``valid_from``) when nothing spans the pivot moment.  Returns
    ``None`` when ``issues`` is empty.

    Args:
        issues: Day's issues, chronologically sorted.
        target_date: Calendar date identifying the day being displayed.

    Returns:
        The default Bulletin to render, or ``None`` when no issues exist.

    """
    if not issues:
        return None

    now = timezone.now()
    today = now.date()
    if target_date == today:
        pivot = now
    else:
        pivot = datetime.datetime.combine(
            target_date, datetime.time(10, 0), tzinfo=datetime.UTC
        )

    # Iterate newest-first so that when both the previous-day evening
    # issue AND the current-day morning update span the pivot, the
    # morning update wins — its later ``valid_from`` marks it as the
    # authoritative refresh of the earlier forecast.
    for b in reversed(issues):
        if b.valid_from <= pivot <= b.valid_to:
            return b

    # No issue spans the pivot — fall back to the most recently-issued one.
    return issues[-1]


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
