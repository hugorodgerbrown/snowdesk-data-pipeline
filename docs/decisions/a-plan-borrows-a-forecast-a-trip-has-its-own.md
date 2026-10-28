---
name: a-plan-borrows-a-forecast-a-trip-has-its-own
description: nearest_weather, meeting_point_weather, enqueue_meeting_weather, Location.active() upcoming trips; Plan tiles and trip page forecast
status: historical
last-reviewed: 2026-10-28
---

# A plan borrows a forecast; a trip has its own

> **Superseded on 2026-10-28** by
> [snowdesk-decides-where-a-forecast-is-fetched](snowdesk-decides-where-a-forecast-is-fetched.md).
> A trip no longer has its own forecast: its meeting point is not fetched,
> and the trip page borrows the nearest curated point, as the Plan page
> does. The Plan page's 15 km cap is gone too. Points 3 and 4 below still
> hold; points 1 and 2 are kept as the record of what SNOW-1217 built.

## Decision

SNOW-1217 put the weather forecast on the Plan page's day tiles and on the
trip page. The two pages read it from different places, and that was a
choice Hugo made (2026-10-24, "Nearest on Plan, exact on Trip").

1. **The Plan page borrows the nearest forecast point.** A route has no
   `Location` and nothing fetches its start. The tiles read the nearest
   `Location.objects.public()` point that has a `Weather` row today, within
   `NEAREST_RADIUS_KM` (15 km) of the route's start (`nearest_weather`).
   The page names the point and how far away it is, because it is not the
   start. No upstream call is made for a route being planned. A favourite
   is never borrowed: it is somebody's private pin.
2. **The trip page reads its meeting point.** A trip's meeting point is
   already a `Location`. `Location.objects.active()` includes it while the
   trip is dated today or later, so `fetch_weather` refreshes it four times
   a day. Saving a trip, or moving its pin, fetches it once off the request
   (`enqueue_meeting_weather`), so the page has a forecast straight away.
3. **A past trip keeps the forecast it had.** `meeting_point_weather`
   reads the newest row on or before the trip's day. Once the day has
   passed, that row is the record of what the forecast said that morning,
   and it is never rewritten
   ([weather-is-one-immutable-location-row](weather-is-one-immutable-location-row.md)).
4. **The danger level stays.** It shows beside the forecast, but only on
   the days a bulletin rated: today, and tomorrow after the evening issue.

## Why

A bulletin covers a day or two, but the forecast covers a week, so on most
tiles the forecast is the only thing there is to show. Fetching every
route's start would mean an Open-Meteo call per route per run for routes
nobody is going on. A trip is different: it is a plan someone is acting
on, so its exact place is worth the call.

## Consequences

- One more Open-Meteo call per upcoming trip, per scheduled run. Past
  trips drop out of `active()`, so the cost does not grow over time.
- A trip saved for a day more than `FORECAST_DAYS` out has no forecast
  until the scheduled run brings that day into range.
- Under `ImmediateBackend` (dev, test and staging), the fetch on save runs
  inline. Tests replace the fetch with a refused one
  (`tests/conftest.py`), so it fails fast and is logged.
- The meeting point carries no elevation, so its forecast is for the model
  cell's mean height.
