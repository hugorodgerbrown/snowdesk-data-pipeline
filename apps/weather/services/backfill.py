"""
apps/weather/services/backfill.py — Fill a location's missing weather days.

``fetch_weather`` only ever writes **today**, so ``Weather`` holds nothing
before the day a location was first fetched, and every historical date is
blank on every surface that reads a row. This module is the fill (SNOW-731).

Four functions, one operation:

  missing_dates(location, floor, until)
      The days in ``[floor, until]`` that have no row yet.

  gap_windows(dates)
      Those days grouped into contiguous runs — one upstream request each,
      split at ``MAX_RANGE_DAYS``.

  backfill_location(location, *, floor, commit, base_url)
      One location, end to end. Returns a ``BackfillResult``.

  backfill_locations(locations, *, ...)
      The walk, catching per-location failures the way
      ``fetch.fetch_all_locations`` does, with a throttle between locations.
      The LocationAdmin action's path: whole window, a handful of rows.

  sweep(locations, *, budget, ...)
      ``backfill_weather``'s path, and the hourly job's (SNOW-1245): the
      window in fortnight blocks, newest first, each block across every
      selected location before the next, inside a weighted-call budget that
      ``sweep_budget`` derives from what ``fetch_weather`` leaves of
      Open-Meteo's daily quota.

**The caller diffs; ``upsert_weather`` is unchanged.** The write rule is
that a past row is never rewritten — ``upsert_weather`` *raises* on one, it
does not skip. So a second run over an already-filled range would raise on
its first day unless the caller asks only for what is missing. That is what
``missing_dates`` is for: idempotence by construction of the caller, not by
an exception to the immutability rule.

**Never today.** ``until`` is yesterday, always. Today is ``fetch_weather``'s
row, and ``upsert_weather`` *updates* rather than refuses for today — so a
backfill that reached it would silently overwrite the live forecast with a
stitched historical timeline. Two guards, because this is the one genuinely
destructive failure mode here: the window ends at yesterday, and the write
loop skips any provider date that was not in the set we asked for.

**``forecast`` stays null on a backfilled row, deliberately.** The upstream
serves a continuous stitched timeline — one value per day, each as issued
near that day. That is not the same object as ``forecast[]``, which records
what the following week looked like *on one particular morning*. Writing a
stitched timeline into that column would be a quiet lie, and the surfaces
that read it would present it as one. The visible consequence is the
location forecast page's SECOND SHAPE (SNOW-789): with one day and no
forward days there is nothing to pick between, so the page renders the
masthead, that day's line, its meteogram and the provenance — and no day
picker at all. The week is what a backfilled day costs, and only the week.
See docs/decisions/weather-backfill-is-an-admin-action.md for why this
column stays null, and
docs/decisions/weather-day-picker-is-a-selector-not-navigation.md for why
the missing picker is permanent rather than a gap waiting to be filled.

**The upstream is the historical forecast API, not ERA5.** Probed
2026-09-01: ERA5 returns ``freezing_level_height`` as a key whose values are
null throughout, and freezing level is the figure that decides rain against
snow. Host in ``settings.OPEN_METEO_HISTORY_BASE_URL``.

**Nothing is reimplemented.** The response shape is identical to the live
forecast endpoint's, so the parsing helpers in ``services/fetch.py`` are
imported rather than copied — what differs is the host, and that one
response yields N rows instead of one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Count, QuerySet
from django.utils import timezone

from apps.locations.models import Location
from apps.locations.services import open_meteo
from apps.weather.models import Weather
from apps.weather.services.fetch import (
    DAILY_VARIABLES,
    FORECAST,
    FORECAST_DAYS,
    HOURLY_VARIABLES,
    _daily_dates,
    _daily_fields,
    _daily_max_freezing_level,
    _day_is_complete,
    _get_forecast,
    _hourly_rows_for_day,
)
from apps.weather.services.upsert import upsert_weather

logger = logging.getLogger(__name__)

# The longest span one upstream request may cover. A whole 2025/26 season
# (203 days, 4,872 hours, all 24 variables) came back in a single 287 KB
# response when probed, so this is a safety rail against an accidental
# multi-year window rather than a tuned chunk size — splitting a season into
# fortnights would lower the weighted call cost but multiply the wall time
# per location, which is what the admin action's request budget is spent on.
MAX_RANGE_DAYS = 366

# How many locations one admin action may process. The action runs inline in
# the request (see the ADR), and Render starts gunicorn with no --timeout in
# render.yaml, so the default 30s applies unless the Render env group
# overrides GUNICORN_CMD_ARGS.
#
# Measured 2026-09-01: a cold location with a whole 2025/26 season missing
# (one request, 304 rows written) took **3.55s** end to end against local
# SQLite. Three locations plus two throttle waits is ~12.6s, which leaves
# real headroom for production Postgres over a network; five would be ~22s,
# which does not. The operator stages the rest from the changelist, and the
# action says what it skipped.
ADMIN_MAX_LOCATIONS = 3

# Seconds to wait between locations. Open-Meteo's terms reserve the right to
# block an IP that misuses the service "without prior notice", and the IP is
# production's — so the walk is paced rather than fired as fast as the loop
# can issue it.
INTER_LOCATION_DELAY = 1.0


# ---------------------------------------------------------------------------
# Open-Meteo's weighted call count (SNOW-1245)
# ---------------------------------------------------------------------------
# The free tier counts *weighted* calls: a request is one call up to ten
# variables and fourteen days, and scales linearly past either — so
# 24 variables over a fortnight is 2.4 calls, and the same variables over a
# whole season is ~63. A full-window walk of 590 locations is therefore
# ~37,000 calls against a quota of 10,000 a day, which is how the first
# unattended production run came to be throttled after 19 minutes.

# Variables in one request — daily and hourly both count.
REQUEST_VARIABLES = len(DAILY_VARIABLES.split(",")) + len(HOURLY_VARIABLES.split(","))

# The days one weighted call covers, and so the sweep's block length: a
# request for fewer days costs the same as one for fourteen.
BLOCK_DAYS = 14

# The free tier's per-IP daily quota, shared by every Open-Meteo host this
# project calls — the live forecast, the history host and the elevation API.
FREE_TIER_DAILY_CALLS = 10_000

# The share of that quota the two weather jobs may plan to spend. The rest
# is left for elevation lookups when a location is minted and for the
# LocationAdmin backfill action, neither of which runs on a timetable.
QUOTA_HEADROOM = 0.9

# How many times a day fetch_weather runs. Must match its CronTrigger in
# schedule.py; tests/weather/services/test_backfill.py asserts that it does.
LIVE_FETCHES_PER_DAY = 4

# How many sweeps run a day: backfill_weather is scheduled hourly.
SWEEPS_PER_DAY = 24


def request_weight(days: int) -> float:
    """
    Return the weighted Open-Meteo calls one backfill request costs.

    Args:
        days: The days the request spans, inclusive.

    Returns:
        ``max(1, variables / 10) × max(1, days / 14)``.

    """
    return max(1.0, REQUEST_VARIABLES / 10) * max(1.0, days / BLOCK_DAYS)


def sweep_budget(active_locations: int) -> float:
    """
    Return the weighted calls one hourly sweep may spend.

    What is left of the daily quota once ``fetch_weather`` has had its four
    runs over the active estate, divided across the day's sweeps. Derived
    rather than fixed, so the sweep shrinks as the estate grows instead of
    tipping the live fetch into a throttle — and stops altogether, rather
    than going negative, once the live fetch needs the whole quota.

    Args:
        active_locations: ``Location.objects.active().count()``.

    Returns:
        The per-run budget, never negative.

    """
    live = active_locations * request_weight(FORECAST_DAYS) * LIVE_FETCHES_PER_DAY
    spare = FREE_TIER_DAILY_CALLS * QUOTA_HEADROOM - live
    return max(0.0, spare / SWEEPS_PER_DAY)


@dataclass
class BackfillResult:
    """What one location's backfill did.

    Attributes:
        filled: Days written that had no row before.
        already_present: Days in the window that already had a row, and so
            were never requested.
        unresolved: Days the upstream returned without a usable
            weather_code/sunrise/sunset triple, and which were dropped.
        requests: Upstream requests issued — one per contiguous gap.
        windows: The ``(start, end)`` spans requested, for the log and for
            the admin message.

    """

    filled: int = 0
    already_present: int = 0
    unresolved: int = 0
    requests: int = 0
    windows: list[tuple[date, date]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Window arithmetic
# ---------------------------------------------------------------------------


def backfill_until() -> date:
    """
    Return the last day a backfill may write — yesterday.

    Today belongs to ``fetch_weather``, which rewrites it on each of its
    four daily runs, and ``upsert_weather`` permits that rewrite. A backfill
    that reached today would therefore overwrite the live row rather than
    being refused by the immutability guard.

    Returns:
        Yesterday, in the project's local timezone.

    """
    return timezone.localdate() - timedelta(days=1)


def backfill_floor() -> date:
    """
    Return the earliest day a backfill may request.

    Returns:
        ``settings.WEATHER_BACKFILL_FLOOR``.

    """
    floor: date = settings.WEATHER_BACKFILL_FLOOR
    return floor


def expected_days(floor: date | None = None, until: date | None = None) -> int:
    """
    Return how many days a complete location holds over the backfill window.

    Used by the admin's coverage column, so the number a curator reads is
    derived from the same bounds the action works to.

    Args:
        floor: Window start. Defaults to None, meaning ``backfill_floor()``.
        until: Window end, inclusive. Defaults to None, meaning
            ``backfill_until()``.

    Returns:
        The day count, or 0 when the window is empty.

    """
    floor = backfill_floor() if floor is None else floor
    until = backfill_until() if until is None else until
    return max(0, (until - floor).days + 1)


def missing_dates(location: Location, floor: date, until: date) -> list[date]:
    """
    Return the days in ``[floor, until]`` this location has no row for.

    Reading the existing set and subtracting it is what makes a re-run safe:
    ``upsert_weather`` raises on an existing past row rather than skipping
    it, so a range that is already filled must never be requested again.

    Args:
        location: The location to diff.
        floor: Window start.
        until: Window end, inclusive.

    Returns:
        The missing days, ascending. Empty when the location is complete or
        the window is empty.

    """
    if until < floor:
        return []
    present = set(
        Weather.objects.filter(
            location=location,
            observed_on__gte=floor,
            observed_on__lte=until,
        ).values_list("observed_on", flat=True)
    )
    span = (until - floor).days + 1
    return [
        day
        for offset in range(span)
        if (day := floor + timedelta(days=offset)) not in present
    ]


def gap_windows(dates: Iterable[date]) -> list[tuple[date, date]]:
    """
    Group ascending dates into contiguous ``(start, end)`` windows.

    One window is one upstream request. A run longer than ``MAX_RANGE_DAYS``
    is split, so a mis-set floor cannot ask for a decade in one call.

    Args:
        dates: The missing days, ascending.

    Returns:
        The windows, in order. Empty when ``dates`` is.

    """
    windows: list[tuple[date, date]] = []
    start: date | None = None
    previous: date | None = None

    for day in dates:
        if start is None or previous is None:
            start, previous = day, day
            continue
        contiguous = day == previous + timedelta(days=1)
        within_cap = (day - start).days + 1 <= MAX_RANGE_DAYS
        if contiguous and within_cap:
            previous = day
            continue
        windows.append((start, previous))
        start, previous = day, day

    if start is not None and previous is not None:
        windows.append((start, previous))
    return windows


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _request_window(
    location: Location,
    start: date,
    end: date,
    *,
    base_url: str | None,
) -> dict[str, Any]:
    """
    Issue one historical request covering ``[start, end]`` for one location.

    Args:
        location: The location to fetch for.
        start: First day of the window.
        end: Last day of the window, inclusive.
        base_url: Overrides the configured historical host. Defaults to
            None, which uses ``settings.OPEN_METEO_HISTORY_BASE_URL``.

    Returns:
        The parsed JSON body.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.

    """
    if base_url is None:
        base_url = settings.OPEN_METEO_HISTORY_BASE_URL
    url = open_meteo.request_url(FORECAST, base_url)

    params: dict[str, str] = {
        "latitude": str(location.latitude),
        "longitude": str(location.longitude),
        "daily": DAILY_VARIABLES,
        "hourly": HOURLY_VARIABLES,
        "timezone": "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if location.elevation_m is not None:
        # Statistically downscales the day to the point's own altitude
        # rather than the model cell's mean terrain, exactly as the live
        # fetch does — so a backfilled day and a live day are comparable.
        params["elevation"] = str(location.elevation_m)

    logger.debug(
        "Backfilling weather for location=%s start=%s end=%s url=%s",
        location.pk,
        start,
        end,
        url,
    )
    return _get_forecast(url, open_meteo.with_api_key(params, url))


def _write_window(
    location: Location,
    data: dict[str, Any],
    wanted: set[date],
    result: BackfillResult,
    *,
    present: frozenset[date] = frozenset(),
) -> None:
    """
    Write one response's storable days as ``Weather`` rows.

    Args:
        location: The location the rows belong to.
        data: The parsed response body.
        wanted: The days this request was issued for. A provider date
            outside it is skipped — the second half of the never-write-today
            guard, and the thing that stops a shifted response reaching a row
            we never asked to write.
        result: Counters, mutated in place.
        present: Days inside the requested span that already have a row.
            The sweep asks for one span per block rather than one per gap,
            because Open-Meteo charges a fortnight whether it is used or
            not, so the days it already holds come back too. They are
            skipped silently; anything else outside ``wanted`` still warns.
            Defaults to empty.

    Raises:
        ValueError: The response's daily arrays are misaligned (SNOW-466).
        KeyError: A required array is absent from the response.

    """
    daily = data["daily"]
    hourly: dict[str, Any] | None = data.get("hourly")
    dates = _daily_dates(daily, location.pk)

    # One transaction for the whole window — the unit of work for this
    # request, so a malformed day partway through cannot leave a half-filled
    # gap behind while the batch counts the location as failed (SNOW-546).
    with transaction.atomic():
        for idx, day in enumerate(dates):
            if day in present:
                continue
            if day not in wanted:
                logger.warning(
                    "Backfill for location=%s: upstream returned %s, which "
                    "was not requested — skipping rather than writing it",
                    location.pk,
                    day,
                )
                continue
            # A day past the backing model's horizon comes back with a null
            # weather_code while sunrise/sunset stay populated. Drop it
            # rather than raising, so one unusable day cannot roll back the
            # ones that did resolve (SNOW-628).
            if not _day_is_complete(daily, idx):
                result.unresolved += 1
                continue

            fields = _daily_fields(daily, idx)
            fields["freezing_level_height"] = _daily_max_freezing_level(hourly, day)
            fields["hourly"] = _hourly_rows_for_day(hourly, day) or None
            # Null, not []: see the module docstring. A stitched timeline is
            # not the outlook as issued on this day, and this column means
            # the latter.
            fields["forecast"] = None

            upsert_weather(location, day, **fields)
            result.filled += 1


def backfill_location(
    location: Location,
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
    base_url: str | None = None,
) -> BackfillResult:
    """
    Fill every missing day for one location.

    Diffs the location's existing days against ``[floor, until]``, groups
    what is missing into contiguous windows, and issues one request per
    window. A location with no gaps issues no request at all.

    Args:
        location: The location to backfill.
        floor: Window start. Defaults to None, meaning ``backfill_floor()``.
        until: Window end, inclusive. Defaults to None, meaning
            ``backfill_until()`` — yesterday. Passing a date is for tests and
            for a narrowed operator run; it is still clamped to yesterday,
            because today is ``fetch_weather``'s row.
        commit: When False the requests still execute — they are real API
            probes — but nothing is written.
        base_url: Overrides the configured historical host.

    Returns:
        The counters for this location.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.
        ValueError: The response's daily arrays are misaligned (SNOW-466).
        KeyError: A required array is absent from the response.

    """
    floor = backfill_floor() if floor is None else floor
    # Clamped, not merely defaulted: a caller passing today (or later) must
    # not be able to reach the live row through this path.
    until = min(backfill_until() if until is None else until, backfill_until())

    result = BackfillResult()
    missing = missing_dates(location, floor, until)
    result.already_present = expected_days(floor, until) - len(missing)

    if not missing:
        logger.debug(
            "Backfill for location=%s: no gaps between %s and %s",
            location.pk,
            floor,
            until,
        )
        return result

    for start, end in gap_windows(missing):
        result.windows.append((start, end))
        result.requests += 1
        data = _request_window(location, start, end, base_url=base_url)
        if not commit:
            continue
        wanted = {day for day in missing if start <= day <= end}
        _write_window(location, data, wanted, result)

    logger.info(
        "Backfill for location=%s: %d day(s) written, %d already present, "
        "%d unresolved, %d request(s), commit=%s",
        location.pk,
        result.filled,
        result.already_present,
        result.unresolved,
        result.requests,
        commit,
    )
    return result


def backfill_locations(
    locations: Iterable[Location],
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
    base_url: str | None = None,
    delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_location: Callable[[Location], None] | None = None,
    on_result: Callable[[Location, BackfillResult], None] | None = None,
) -> dict[str, int]:
    """
    Backfill every location in ``locations``, one after another.

    A per-location failure is caught, logged and counted; it never aborts
    the walk, so one location whose coordinates Open-Meteo dislikes cannot
    cost every other location its history.

    Args:
        locations: The locations to walk.
        floor: Window start. Defaults to None, meaning ``backfill_floor()``.
        until: Window end. Defaults to None, meaning yesterday.
        commit: When False the requests still execute but nothing is written.
        base_url: Overrides the configured historical host.
        delay: Seconds to wait between locations. Defaults to None, which
            resolves to ``INTER_LOCATION_DELAY`` at call time rather than at
            import — so a caller (or a test) can move the constant and have
            it take effect. 0 disables the throttle.
        sleep: The sleep callable, injected so tests do not wait. Defaults
            to ``time.sleep``.
        on_location: Called once per location before its requests, for
            progress reporting. Defaults to None.
        on_result: Called once per location with its result, for per-row
            reporting. Not called for a location that raised. Defaults to
            None.

    Returns:
        A mapping with integer counters: ``locations`` (walked), ``filled``,
        ``already_present``, ``unresolved``, ``requests`` and ``failed``.

    """
    delay = INTER_LOCATION_DELAY if delay is None else delay
    counts = {
        "locations": 0,
        "filled": 0,
        "already_present": 0,
        "unresolved": 0,
        "requests": 0,
        "failed": 0,
    }

    for index, location in enumerate(locations):
        if index and delay:
            sleep(delay)
        if on_location is not None:
            on_location(location)
        counts["locations"] += 1
        try:
            result = backfill_location(
                location,
                floor=floor,
                until=until,
                commit=commit,
                base_url=base_url,
            )
        except Exception:  # noqa: BLE001 — broad catch intentional: one location must not abort the walk
            logger.exception("Failed to backfill weather for location=%s", location.pk)
            counts["failed"] += 1
            continue
        counts["filled"] += result.filled
        counts["already_present"] += result.already_present
        counts["unresolved"] += result.unresolved
        counts["requests"] += result.requests
        if on_result is not None:
            on_result(location, result)

    logger.info(
        "backfill_locations done: locations=%d filled=%d already_present=%d "
        "unresolved=%d requests=%d failed=%d commit=%s",
        counts["locations"],
        counts["filled"],
        counts["already_present"],
        counts["unresolved"],
        counts["requests"],
        counts["failed"],
        commit,
    )
    return counts


# ---------------------------------------------------------------------------
# The sweep (SNOW-1245)
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    """What one budgeted sweep did.

    Attributes:
        locations: Location-blocks a request was issued for.
        filled: Days written that had no row before.
        unresolved: Days the upstream returned without a usable
            weather_code/sunrise/sunset triple, and which were dropped.
        requests: Upstream requests issued.
        weight: Weighted Open-Meteo calls those requests cost.
        failed: Location-blocks whose request or write raised.
        blocks: The ``(start, end)`` blocks the run worked in, newest first.
        blocks_finished: How many of ``blocks`` the run walked to the end.
        stopped: ``"budget"`` when the next request would have overspent,
            ``"throttled"`` when the upstream answered 429, otherwise empty.

    """

    locations: int = 0
    filled: int = 0
    unresolved: int = 0
    requests: int = 0
    weight: float = 0.0
    failed: int = 0
    blocks: list[tuple[date, date]] = field(default_factory=list)
    blocks_finished: int = 0
    stopped: str = ""


def fortnight_blocks(floor: date, until: date) -> list[tuple[date, date]]:
    """
    Split ``[floor, until]`` into ``BLOCK_DAYS`` blocks, newest first.

    Aligned to ``floor`` rather than to ``until``, so a block keeps its
    boundaries from one day to the next and a run that stops partway through
    one resumes the same block. The newest block is the short one.

    Args:
        floor: Window start.
        until: Window end, inclusive.

    Returns:
        The blocks, newest first. Empty when the window is.

    """
    blocks: list[tuple[date, date]] = []
    start = floor
    while start <= until:
        end = min(start + timedelta(days=BLOCK_DAYS - 1), until)
        blocks.append((start, end))
        start = end + timedelta(days=1)
    blocks.reverse()
    return blocks


def _complete_in(start: date, end: date) -> QuerySet[Any, Any]:
    """
    Return the ids of locations holding every day of ``[start, end]``.

    A subquery, so the walk selects the locations with a gap in the block
    without reading one row per location first.

    Args:
        start: Block start.
        end: Block end, inclusive.

    Returns:
        A ``values("location")`` queryset to exclude on.

    """
    span = (end - start).days + 1
    return (
        Weather.objects.filter(observed_on__gte=start, observed_on__lte=end)
        .values("location")
        .annotate(days=Count("id"))
        .filter(days=span)
        .values("location")
    )


def _is_throttled(exc: requests.HTTPError) -> bool:
    """Return True when ``exc`` is Open-Meteo refusing for rate, not for input."""
    return exc.response is not None and exc.response.status_code == 429


def _newest_first(queryset: QuerySet[Location]) -> Iterable[Location]:
    """Stream ``queryset`` newest id first — the sweep's default order."""
    return queryset.order_by("-id").iterator()


def sweep(
    locations: QuerySet[Location],
    *,
    floor: date | None = None,
    until: date | None = None,
    commit: bool,
    budget: float | None,
    base_url: str | None = None,
    delay: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stream: Callable[[QuerySet[Location]], Iterable[Location]] | None = None,
    on_block: Callable[[date, date, int], None] | None = None,
) -> SweepResult:
    """
    Fill the newest gaps first, a fortnight block at a time, within a budget.

    Walks ``fortnight_blocks`` newest first. In each block it selects the
    locations missing any day of it and issues **one** request per location,
    spanning its first missing day to its last — a fortnight is the unit
    Open-Meteo charges for, so splitting a block at the days already held
    would cost more and save nothing. A block is finished across every
    selected location before the next one starts, so a run's work is one
    contiguous stretch of the calendar, and the estate's most recent
    history fills in evenly rather than one location at a time.

    The walk stops **before** a request that would take the run past
    ``budget``, and stops at once on a 429 — the throttle is the upstream
    saying the quota is spent, and every further request would only be
    refused too. The next run picks up the same block where this one left
    off, because the block's completed locations no longer select.

    A location whose request or write raises is counted and skipped; it
    never aborts the walk. One that keeps failing costs its request on every
    run and blocks nothing else.

    Args:
        locations: The locations to fill. Filter before passing.
        floor: Window start. Defaults to None, meaning ``backfill_floor()``.
        until: Window end, inclusive. Defaults to None, meaning yesterday;
            a later date is clamped to yesterday.
        commit: When False the requests still execute but nothing is written.
        budget: Weighted calls the run may spend; None for no limit.
        base_url: Overrides the configured historical host.
        delay: Seconds between requests. Defaults to None, meaning
            ``INTER_LOCATION_DELAY``. 0 disables the throttle.
        sleep: The sleep callable, injected so tests do not wait.
        stream: Turns a block's selected locations into an iterable — the
            command passes ``iterate_rows`` for its countdown. Defaults to
            None, meaning newest id first, streamed.
        on_block: Called with ``(start, end, locations)`` as each block with
            a gap begins. Defaults to None.

    Returns:
        The counters for the run.

    """
    floor = backfill_floor() if floor is None else floor
    until = min(backfill_until() if until is None else until, backfill_until())
    delay = INTER_LOCATION_DELAY if delay is None else delay
    stream = _newest_first if stream is None else stream

    result = SweepResult()
    for start, end in fortnight_blocks(floor, until):
        pending = locations.exclude(pk__in=_complete_in(start, end))
        count = pending.count()
        if not count:
            continue
        if on_block is not None:
            on_block(start, end, count)
        result.blocks.append((start, end))
        for location in stream(pending):
            _sweep_location(
                location,
                start,
                end,
                result,
                commit=commit,
                budget=budget,
                base_url=base_url,
                delay=delay,
                sleep=sleep,
            )
            if result.stopped:
                break
        if result.stopped:
            break
        result.blocks_finished += 1

    logger.info(
        "backfill sweep done: blocks=%d finished=%d locations=%d filled=%d "
        "unresolved=%d requests=%d weight=%.1f failed=%d stopped=%s commit=%s",
        len(result.blocks),
        result.blocks_finished,
        result.locations,
        result.filled,
        result.unresolved,
        result.requests,
        result.weight,
        result.failed,
        result.stopped or "-",
        commit,
    )
    return result


def _sweep_location(
    location: Location,
    start: date,
    end: date,
    result: SweepResult,
    *,
    commit: bool,
    budget: float | None,
    base_url: str | None,
    delay: float,
    sleep: Callable[[float], None],
) -> None:
    """
    Fill one location's gap in one block, or set ``result.stopped``.

    Args:
        location: The location to fill.
        start: Block start.
        end: Block end, inclusive.
        result: The run's counters, mutated in place.
        commit: When False the request executes but nothing is written.
        budget: Weighted calls the run may spend; None for no limit.
        base_url: Overrides the configured historical host.
        delay: Seconds to wait before this request when one has gone before.
        sleep: The sleep callable.

    """
    missing = missing_dates(location, start, end)
    if not missing:
        return
    span_start, span_end = missing[0], missing[-1]
    weight = request_weight((span_end - span_start).days + 1)
    if budget is not None and result.weight + weight > budget:
        result.stopped = "budget"
        return

    if result.requests and delay:
        sleep(delay)
    result.locations += 1
    data = _sweep_request(location, span_start, span_end, weight, result, base_url)
    if data is None or not commit:
        return

    wanted = set(missing)
    span = (span_end - span_start).days + 1
    present = frozenset(
        day
        for offset in range(span)
        if (day := span_start + timedelta(days=offset)) not in wanted
    )
    written = BackfillResult()
    try:
        _write_window(location, data, wanted, written, present=present)
    except Exception:  # noqa: BLE001 — broad catch intentional: one location must not abort the sweep
        logger.exception(
            "Failed to write backfilled weather for location=%s", location.pk
        )
        result.failed += 1
        return
    result.filled += written.filled
    result.unresolved += written.unresolved


def _sweep_request(
    location: Location,
    start: date,
    end: date,
    weight: float,
    result: SweepResult,
    base_url: str | None,
) -> dict[str, Any] | None:
    """
    Issue one sweep request, counting it, or record why there is no payload.

    Args:
        location: The location to fetch for.
        start: First day of the span.
        end: Last day of the span, inclusive.
        weight: The request's weighted cost, added once it is spent.
        result: The run's counters, mutated in place.
        base_url: Overrides the configured historical host.

    Returns:
        The parsed body, or None when the request failed or was throttled —
        a throttle also sets ``result.stopped``.

    """
    try:
        data = _request_window(location, start, end, base_url=base_url)
    except requests.HTTPError as exc:
        if _is_throttled(exc):
            logger.warning(
                "Backfill sweep throttled at location=%s span=%s..%s; stopping",
                location.pk,
                start,
                end,
            )
            result.stopped = "throttled"
            return None
        # Refused for its input, so the upstream counted it.
        logger.exception("Failed to backfill weather for location=%s", location.pk)
        result.failed += 1
        result.requests += 1
        result.weight += weight
        return None
    except Exception:  # noqa: BLE001 — broad catch intentional: one location must not abort the sweep
        logger.exception("Failed to backfill weather for location=%s", location.pk)
        result.failed += 1
        return None
    result.requests += 1
    result.weight += weight
    return data
