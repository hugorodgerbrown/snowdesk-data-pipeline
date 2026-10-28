---
name: snowdesk-decides-where-a-forecast-is-fetched
description: Location.active() is the curated estate only; pins and trips borrow the nearest forecast (nearest_forecast, _forecast_source)
status: current
last-reviewed: 2026-10-28
---

# Snowdesk decides where a forecast is fetched

## Decision

Hugo, 2026-10-28: "We decide on locations — that also tracks with the fact
that we pay for the forecast — so we should be in control."

1. **Only the curated estate is fetched.** `Location.objects.active()` —
   the set `fetch_weather` walks four times a day, one Open-Meteo call per
   row — is the resort points and the region centroids, the same rows as
   `Location.objects.public()`. A `Favourite` no longer puts its location
   in it, and nor does a trip's meeting point (which SNOW-1217 added).
   The methods stay two: `public()` answers "what may anyone see",
   `active()` "what do we pay to fetch". They coincide by policy.
2. **Everything else borrows the nearest curated forecast.** A saved
   pin's sheet and card, the trip page and the Plan page read the nearest
   `public()` point that has a row for the day
   (`apps.weather.services.nearest.nearest_forecast`). **There is no
   distance cap**: "as long as we are open about where the nearest point
   is, the distance doesn't matter. If it turns out it does matter, we add
   more points." The 15 km `NEAREST_RADIUS_KM` the Plan page had is gone.
3. **Every borrowing surface says where it is from**, in one sentence
   (`includes/_forecast_source.html`): the point's name, its height and
   how far away it is — "Forecast for Mont Fort (3329 m), 2.1 km from
   this pin." A place within `SAME_PLACE_KM` (100 m) of the point, such as
   a saved resort's own pin, drops the distance.
4. **The nearest point is chosen by distance alone.** Its height is
   stated, not used to choose: a resort's peak and its village can be a
   thousand metres apart, and the reader is better placed to judge which
   one stands for their line than a rule would be.
5. **A past trip reads the borrowed point's row for its day**, which is
   never rewritten
   ([weather-is-one-immutable-location-row](weather-is-one-immutable-location-row.md)).
   A meeting point's own rows, fetched before this change, stay in the
   table and are no longer read.

## Why

The bill grew with the users. Any signed-in user could add a fetched
point by dropping a pin or planning a trip, at four calls a day each, for
as long as the pin existed. The curated estate's cost is fixed by what we
curate. A pin's forecast was also never better than its model cell: the
nearest curated point is usually a few kilometres away, and naming it is
more honest than a forecast labelled "this spot" that came from the same
grid.

Hugo also floated a metered "Fetch forecast" button that buys one live
fetch for a private place. It is not built. If it is, it widens
`active()` for that place and must never widen `public()`.

## Consequences

- `apps/trips/services/meeting_weather.py` (`enqueue_meeting_weather`)
  is deleted: saving or editing a trip makes no upstream call.
- `enqueue_location_prime` still resolves a pin's height, but its
  forecast step does nothing for a pin, because a pin is not `active()`.
- A pin's own forecast page (`/weather/<short_id>/`, reachable by its
  owner through `visible_to()`) keeps the rows recorded before this change
  and gains no new ones. Every link on the pin's sheet and card now opens
  the borrowed point's page instead.
- If a region turns out to be badly served (a long valley with one
  centroid), the answer is another curated point, made in the location
  editor, not a fetch for the user's pin.
- Supersedes the trip half of
  [a-plan-borrows-a-forecast-a-trip-has-its-own](a-plan-borrows-a-forecast-a-trip-has-its-own.md).
