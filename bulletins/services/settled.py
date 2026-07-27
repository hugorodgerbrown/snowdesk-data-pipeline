"""
bulletins/services/settled.py — Settled/unsettled boundary for cacheable dates.

A forecast day's ``BulletinGrouping`` geometry is immutable once no future
ingest run can still land a bulletin that targets it. This module derives
that boundary from the same ``BulletinSource`` registry
(``bulletins.services.slf_fetcher.get_sources``) that
``fetch_bulletins.Command._default_start_date`` uses to pick its default
``--start-date`` — deliberately the same call, so there is no second
constant that can drift out of step with the fetcher's actual resume point.

``public/api.py`` calls ``is_settled`` to decide the ``Cache-Control`` header
on ``bulletin_groupings_geojson``; the service worker never re-derives the
date arithmetic itself — see ``docs/decisions/date-aware-cache-policy.md``.
"""

from datetime import date

from django.conf import settings
from django.utils import timezone

from bulletins.services.slf_fetcher import get_sources


def earliest_mutable_date() -> date:
    """
    Return the earliest forecast day a future ingest run could still change.

    Iterates ``get_sources().values()`` and calls each entry's
    ``latest_date_fn()`` — the same registry and call
    ``fetch_bulletins.Command._default_start_date`` uses to derive its
    default ``--start-date``. Each source's next run re-fetches from its
    ``latest_date_fn()`` onward, so the earliest date any source could still
    gain or change a bulletin is the minimum across sources. Sources with no
    rows yet (``None``) contribute no constraint; if every source is empty,
    the whole season is still mutable, so the season backstop is returned.

    The result is capped at today — a future date is never settled.

    Returns:
        The earliest date that could still be rewritten by a future ingest.

    """
    today = timezone.localdate()
    latest_dates = [
        latest
        for source in get_sources().values()
        if (latest := source.latest_date_fn()) is not None
    ]
    season_start: date = settings.SEASON_START_DATE
    if not latest_dates:
        return min(season_start, today)
    return min(min(latest_dates), today)


def is_settled(target: date) -> bool:
    """
    Return whether ``target`` is strictly before the earliest mutable date.

    Args:
        target: The forecast day to test.

    Returns:
        ``True`` when no future ingest run could still change ``target``'s
        ``BulletinGrouping`` geometry.

    """
    return target < earliest_mutable_date()
