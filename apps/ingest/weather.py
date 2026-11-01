"""
apps/ingest/weather.py — The service's weather: its places, its fetch, its rows.

SNOW-1253 moved the weather fetch into ingest. Ingest keeps its own list of
reference places (``SourceLocation``) and its own account of each day
(``SourceWeather``); every instance, production included, applies those rows
into its own ``Weather`` table through ``Location.source_uuid``.

  forecast_rows(latitude, longitude, elevation_m, observed_on, *, label, base_url)
      One Open-Meteo forecast request for one coordinate, parsed into the
      rows it describes: one ``(day, fields)`` pair per day of the window,
      each with its own hourly series (SNOW-1337). The parsing rules live
      here so the service and the app's own fetch cannot drift apart.

  store_source_weather(location, observed_on, **fields)
      The write rule — create when absent, refine today or later, refuse a
      past day — on ingest's own table.

  sync_source_locations(records)
      Make the live ``SourceLocation`` set equal ``records``: create, update
      by uuid, tombstone what is absent, revive what came back.

  fetch_source_location / fetch_source_weather
      One place, and the walk over every live place, catching per-place
      failures so one bad coordinate cannot cost the rest their row.

**Three behaviours carried across from the app's fetch**, each a bug found
the hard way: rows key off the provider's own ``daily.time`` (SNOW-466); a
day past the model's horizon is dropped before the write rather than raised
on (SNOW-628); each place's write is one transaction (SNOW-546).
"""

from __future__ import annotations

import logging
import uuid as uuid_module
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.ingest.models import (
    ImmutableSourceWeatherError,
    SourceLocation,
    SourceWeather,
)
from apps.ingest.normalise.weather import (
    daily_dates,
    daily_fields,
    daily_max_freezing_level,
    day_is_complete,
    hourly_rows_for_day,
)
from apps.ingest.sources.open_meteo import fetch_forecast

logger = logging.getLogger(__name__)

# How many days each request covers: today and the six days after it. Each
# day becomes its own row (SNOW-1337), created when it first enters the
# window at six days out and refined by every fetch until its day passes.
FORECAST_DAYS = 7

# How many days of the window carry an hourly series: every one (Hugo,
# 2026-11-01). Open-Meteo returns the whole window's hourly block in the
# one request, so this costs storage, not calls. Kept as a name because the
# readers that decide which days reveal an hourly drawing still ask it.
HOURLY_DAYS = FORECAST_DAYS


@dataclass(frozen=True)
class LocationRecord:
    """One reference place as a host hands it to ``sync_source_locations``.

    Attributes:
        uuid: The service's id for the place.
        name: Its curated name, or empty.
        latitude: Exact WGS-84 latitude.
        longitude: Exact WGS-84 longitude.
        elevation_m: Its height in metres, or None.
        document: What else the host curates about the place, stored as
            given and never read by the service (SNOW-1271).

    """

    uuid: uuid_module.UUID
    name: str
    latitude: float
    longitude: float
    elevation_m: float | None
    document: dict[str, Any] = field(default_factory=dict)


def forecast_rows(
    latitude: float,
    longitude: float,
    elevation_m: float | None,
    observed_on: date,
    *,
    label: object,
    base_url: str | None = None,
) -> list[tuple[date, dict[str, Any]]]:
    """Fetch one coordinate's forecast and parse it into one row per day.

    One request covers ``FORECAST_DAYS`` days from ``observed_on``. Every
    storable day becomes its own row (SNOW-1337): its daily scalars, its
    freezing level and its own hourly series, plus ``forecast_on`` — the
    day of this fetch, so a row's lead time is ``observed_on - forecast_on``.
    Days the backing model did not resolve are dropped first (SNOW-628), so
    a short window is normal, not a failure, and a provider date outside the
    window asked for is dropped too, so a shifted response can never reach a
    past day's row.

    Args:
        latitude: Exact latitude.
        longitude: Exact longitude.
        elevation_m: The point's height, for downscaling; None for the
            model cell's mean.
        observed_on: The first day of the window — today — and every row's
            ``forecast_on``.
        label: What to call the point in log lines and errors.
        base_url: Overrides the configured Open-Meteo host.

    Returns:
        ``(day, fields)`` pairs in date order, ``fields`` holding every
        column but ``fetched_at``.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.
        ValueError: The daily arrays are misaligned (SNOW-466), or no day
            in the window is storable (SNOW-628).
        KeyError: A required array is absent.

    """
    end_date = observed_on + timedelta(days=FORECAST_DAYS - 1)
    data = fetch_forecast(
        latitude, longitude, elevation_m, observed_on, end_date, base_url=base_url
    )

    daily = data["daily"]
    hourly: dict[str, Any] | None = data.get("hourly")

    dates = daily_dates(daily, label)
    storable = [
        idx
        for idx in range(len(dates))
        if observed_on <= dates[idx] <= end_date and day_is_complete(daily, idx)
    ]
    if not storable:
        raise ValueError(
            f"Open-Meteo forecast for location={label}: no day carries a "
            f"complete weather_code/sunrise/sunset — refusing to store."
        )
    if len(storable) < len(dates):
        logger.info(
            "Open-Meteo forecast for location=%s: %d of %d day(s) resolved — "
            "the rest fall beyond the model's horizon",
            label,
            len(storable),
            len(dates),
        )
    if storable[0] != 0:
        logger.warning(
            "Open-Meteo forecast for location=%s: day 0 (%s) carries no usable "
            "data — the window starts at %s",
            label,
            dates[0],
            dates[storable[0]],
        )

    rows: list[tuple[date, dict[str, Any]]] = []
    for idx in storable:
        day = dates[idx]
        fields = daily_fields(daily, idx)
        fields["freezing_level_height"] = daily_max_freezing_level(hourly, day)
        fields["hourly"] = hourly_rows_for_day(hourly, day) or None
        fields["forecast_on"] = observed_on
        rows.append((day, fields))
    return rows


def store_source_weather(
    location: SourceLocation, observed_on: date, **fields: Any
) -> tuple[SourceWeather, bool]:
    """Create or refine one place's row for one day.

    The same three outcomes as the app's ``upsert_weather``: absent is
    created whatever the date; present and today or later is updated in
    place; present and past is refused.

    Args:
        location: The place.
        observed_on: The day the row is of.
        **fields: Column values. ``fetched_at`` defaults to now, and
            ``forecast_on`` to today (the day itself for a past day).

    Returns:
        ``(row, created)``.

    Raises:
        ImmutableSourceWeatherError: The row exists and its day has passed.

    """
    fields.setdefault("fetched_at", timezone.now())
    fields.setdefault("forecast_on", min(observed_on, timezone.localdate()))
    existing = SourceWeather.objects.filter(
        location=location, observed_on=observed_on
    ).first()
    if existing is None:
        row = SourceWeather.objects.create(
            location=location, observed_on=observed_on, **fields
        )
        return row, True
    if observed_on < timezone.localdate():
        raise ImmutableSourceWeatherError(
            f"SourceWeather({location.pk}, {observed_on}) is past and cannot "
            "be rewritten."
        )
    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save()
    return existing, False


def sync_source_locations(records: Iterable[LocationRecord]) -> dict[str, int]:
    """Make the live ``SourceLocation`` set equal ``records``.

    A record whose uuid is new is created; a known one is updated when any
    field differs (so ``updated_at`` moves only on a real change); a live
    row absent from ``records`` is tombstoned; a tombstoned one that is
    back is revived. Nothing is ever deleted.

    Args:
        records: Every reference place, as the host holds them now.

    Returns:
        Counts: ``created``, ``updated``, ``deleted``, ``unchanged``.

    """
    counts = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    seen: set[uuid_module.UUID] = set()
    existing = {row.uuid: row for row in SourceLocation.objects.all()}

    with transaction.atomic():
        for record in records:
            seen.add(record.uuid)
            values = {
                "name": record.name,
                "latitude": record.latitude,
                "longitude": record.longitude,
                "elevation_m": record.elevation_m,
                "document": record.document,
                "deleted_at": None,
            }
            row = existing.get(record.uuid)
            if row is None:
                SourceLocation.objects.create(uuid=record.uuid, **values)
                counts["created"] += 1
                continue
            if all(getattr(row, name) == value for name, value in values.items()):
                counts["unchanged"] += 1
                continue
            for name, value in values.items():
                setattr(row, name, value)
            row.save()
            counts["updated"] += 1

        gone = [
            row
            for key, row in existing.items()
            if key not in seen and row.deleted_at is None
        ]
        now = timezone.now()
        for row in gone:
            row.deleted_at = now
            row.save(update_fields=["deleted_at", "updated_at"])
        counts["deleted"] = len(gone)

    logger.info("sync_source_locations: %s", counts)
    return counts


def fetch_source_location(
    location: SourceLocation,
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
) -> list[tuple[SourceWeather, bool]] | None:
    """Fetch and, with ``commit``, store one place's week as one row per day.

    Args:
        location: The place.
        observed_on: The first day of the window — today.
        commit: When False the request still runs (a real probe) but
            nothing is written.
        base_url: Overrides the configured Open-Meteo host.

    Returns:
        One ``(row, created)`` pair per day written when committed, else
        None.

    """
    rows = forecast_rows(
        location.latitude,
        location.longitude,
        location.elevation_m,
        observed_on,
        label=location.uuid,
        base_url=base_url,
    )
    if not commit:
        return None
    # One transaction for the place's whole week (SNOW-546): a failure on
    # day four cannot leave days one to three refined and the rest stale
    # while the batch counts the place as failed.
    with transaction.atomic():
        return [store_source_weather(location, day, **fields) for day, fields in rows]


def fetch_source_weather(
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
    locations: Iterable[SourceLocation] | None = None,
    on_location: Callable[[SourceLocation], None] | None = None,
    on_row: Callable[[SourceWeather], object] | None = None,
) -> dict[str, int]:
    """Fetch every live place, catching each failure so it fails alone.

    Args:
        observed_on: The day to fetch, the first day of each window.
        commit: When False the requests run but nothing is written.
        base_url: Overrides the configured Open-Meteo host.
        locations: The places to walk; defaults to every live one. A host
            command passes its own countdown-printing iterable here.
        on_location: Called before each place's request.
        on_row: Called with each row written — the host applies it.

    Returns:
        Counts: ``created`` and ``updated`` rows (up to ``FORECAST_DAYS`` a
        place), and ``failed`` places.

    """
    counts = {"created": 0, "updated": 0, "failed": 0}
    if locations is None:
        locations = SourceLocation.objects.live().iterator()

    for location in locations:
        if on_location is not None:
            on_location(location)
        try:
            result = fetch_source_location(
                location, observed_on, commit=commit, base_url=base_url
            )
            if result is not None and on_row is not None:
                for row, _ in result:
                    on_row(row)
        except Exception:  # noqa: BLE001 — broad catch intentional: one place must not abort the batch
            logger.exception(
                "Failed to fetch weather for source location=%s observed_on=%s",
                location.uuid,
                observed_on,
            )
            counts["failed"] += 1
            continue
        for _, created in result or []:
            counts["created" if created else "updated"] += 1

    logger.info(
        "fetch_source_weather done: observed_on=%s %s commit=%s",
        observed_on,
        counts,
        commit,
    )
    return counts
