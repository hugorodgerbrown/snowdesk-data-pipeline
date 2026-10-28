"""
apps/weather/services/fetch.py — Fetch Open-Meteo weather for the location estate.

Two functions, one walk:

  fetch_weather_for_location(location, observed_on, *, commit, base_url)
      One Open-Meteo forecast request for one location, covering
      ``FORECAST_DAYS`` days. Writes one ``Weather`` row — the day
      ``observed_on`` — carrying that day's scalars and hourly series, plus
      the days after it in the ``forecast`` column.

  fetch_all_locations(observed_on, *, commit, base_url, on_location)
      Streams ``Location.objects.active()`` and calls the above for each,
      catching per-location failures so one bad location cannot abort the
      batch. Returns counters.

**One pass over one anchor.** This replaces two passes over two anchors —
a region-centroid pass writing ``WeatherSnapshot`` and a quantised-cell pass
writing ``ForecastCellWeather``. Both anchors are now ``Location``, so there
is one variable set, one request shape and one row per location per run.

**No fetch-time dedup.** The quantisation the old ``ForecastCell`` grid did
was measured on 2026-08-29: 240 real places produced 240 distinct cells,
zero sharing, in a test rigged in its favour. It existed to keep a *table*
small, and there is no longer a table for it to keep small. If
``Location.objects.active()`` ever widens to every favourite again (it is
the curated estate only since 2026-10-28), the estate could reach
thousands with genuine clustering — re-measure then.

**Three behaviours carried across from the fetcher SNOW-762 stripped.**
Each is a bug that was found the hard way and each has its own test:

* **SNOW-466** — rows key off the provider's own ``daily.time`` array,
  never ``observed_on + idx``. A shifted or gapped response is rejected
  rather than silently attaching every day's weather to the wrong date.
* **SNOW-628** — a day past the backing model's horizon comes back with a
  null ``weather_code`` while ``sunrise``/``sunset`` (astronomical, not
  modelled) stay populated. Those days are **dropped before the write**,
  not raised on. Filtering first is what stops the unusable tail rolling
  back the near-term days a skier actually reads; point forecasts once
  wrote zero rows because it did not.
* **SNOW-546** — the write is wrapped in ``transaction.atomic()`` per
  location, so a malformed timestamp partway through cannot leave a
  half-written row behind while the batch counts the location as failed.

The request itself is ``fetch_forecast`` from ``apps.ingest.public``
(SNOW-1249), which knows a coordinate and a date range and never a
``Location``. This module keeps the walk over locations, the choice of
window and the write.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date

from django.db import transaction

from apps.ingest.public import (
    FORECAST_DAYS,
    HOURLY_DAYS,
    forecast_fields,
)
from apps.locations.models import Location
from apps.weather.models import Weather
from apps.weather.services.upsert import upsert_weather

logger = logging.getLogger(__name__)

# FORECAST_DAYS and HOURLY_DAYS are ingest's (SNOW-1253), re-exported here
# for the callers that read them from this module.
__all__ = [
    "FORECAST_DAYS",
    "HOURLY_DAYS",
    "fetch_all_locations",
    "fetch_weather_for_location",
]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_weather_for_location(
    location: Location,
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
) -> tuple[Weather, bool] | None:
    """
    Fetch and optionally persist one location's weather for one day.

    Makes one Open-Meteo forecast request covering ``FORECAST_DAYS`` days
    from ``observed_on``. The first storable day becomes the ``Weather``
    row; the days after it become its ``forecast`` column, with the first
    ``HOURLY_DAYS`` of the window also carrying an hourly series.

    ``location.elevation_m`` is passed to the API when set, so the forecast
    is statistically downscaled to the point's own altitude rather than the
    model cell's mean terrain. A location with no elevation is still
    fetched — the forecast is simply the cell's.

    Days the backing model did not resolve are dropped before anything is
    written (SNOW-628), so a short window is the normal outcome rather than
    a failure. The write is wrapped in one transaction (SNOW-546).

    Args:
        location: The location to fetch for.
        observed_on: The first day of the window, and the day the written
            row is of.
        commit: When False the request still executes — it is a real API
            probe — but nothing is written and None is returned.
        base_url: Overrides the configured Open-Meteo host. Defaults to
            None, which uses the configured host and sends the customer key.

    Returns:
        A ``(weather, created)`` tuple when ``commit`` is True, or None
        when it is False.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.
        ValueError: The response's daily arrays are misaligned (SNOW-466),
            or no day in the window is storable — which is a malformed
            payload rather than a short horizon (SNOW-628).
        KeyError: A required array is absent from the response.
        ImmutableWeatherRowError: A row already exists for this location and
            day, and that day has passed.

    """
    logger.debug(
        "Fetching weather for location=%s observed_on=%s commit=%s",
        location.pk,
        observed_on,
        commit,
    )

    # The request and its parsing are ingest's (SNOW-1253), shared with the
    # service's own fetch of its reference places so the two cannot drift.
    row_date, fields = forecast_fields(
        location.latitude,
        location.longitude,
        location.elevation_m,
        observed_on,
        label=location.pk,
        base_url=base_url,
    )

    if not commit:
        return None

    # One transaction for the row and nothing else — the whole unit of work
    # for this location, so a failure here cannot leave a half-written row
    # while the batch counts the location as failed (SNOW-546).
    with transaction.atomic():
        return upsert_weather(location, row_date, **fields)


def fetch_all_locations(
    observed_on: date,
    *,
    commit: bool,
    base_url: str | None = None,
    locations: Iterable[Location] | None = None,
    on_location: Callable[[Location], None] | None = None,
) -> dict[str, int]:
    """
    Fetch weather for every active location.

    Walks ``Location.objects.active()`` — a location reachable from a
    resort or a region centroid: the curated estate, and nothing a user
    placed (2026-10-28). A per-location failure is
    caught, logged and counted; it never aborts the batch, so one region
    whose coordinates Open-Meteo dislikes cannot cost every other location
    its daily row.

    Args:
        observed_on: The day to fetch, and the first day of each window.
        commit: When False, the requests still execute but nothing is
            written.
        base_url: Overrides the configured Open-Meteo host.
        locations: The locations to walk. Defaults to None, which resolves
            to ``Location.objects.active()``. The command passes its own
            streamed, countdown-printing iterable through here rather than
            materialising one.
        on_location: Called once per location before its request, for
            progress reporting. Defaults to None.

    Returns:
        A mapping with integer counters: ``created`` (new rows),
        ``updated`` (rows refined in place) and ``failed`` (locations whose
        fetch raised).

    """
    counts: dict[str, int] = {"created": 0, "updated": 0, "failed": 0}

    if locations is None:
        locations = Location.objects.active().iterator()

    for location in locations:
        if on_location is not None:
            on_location(location)
        try:
            result = fetch_weather_for_location(
                location,
                observed_on,
                commit=commit,
                base_url=base_url,
            )
        except Exception:  # noqa: BLE001 — broad catch intentional: one location must not abort the batch
            logger.exception(
                "Failed to fetch weather for location=%s observed_on=%s",
                location.pk,
                observed_on,
            )
            counts["failed"] += 1
            continue
        if result is not None:
            _, created = result
            counts["created" if created else "updated"] += 1

    logger.info(
        "fetch_all_locations done: observed_on=%s created=%d updated=%d failed=%d "
        "commit=%s",
        observed_on,
        counts["created"],
        counts["updated"],
        counts["failed"],
        commit,
    )
    return counts
