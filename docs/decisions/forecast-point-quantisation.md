---
name: forecast-point-quantisation
description: ForecastPoint grid cell (0.01 lat / 0.015 lon) and 200m elevation band sizing; reuse-nearest precedes cell creation
status: current
last-reviewed: 2026-07-23
---

# ForecastPoint grid and elevation-band sizing

**Decision.** Map pins snap to a shared `ForecastPoint` row keyed on a
quantised grid cell (`lat_cell = floor(lat / 0.01)`, `lon_cell = floor(lon
/ 0.015)`) plus an elevation band (`elevation_band = floor(elevation /
200)`). `resolve_forecast_point()` always checks for a reusable nearby
point (within 750m horizontally and 150m in elevation, searched across the
3x3x3 cell neighbourhood) *before* falling back to `get_or_create` on the
quantised key.

**Why.** 0.01 degrees latitude is ~1.1km; 0.015 degrees longitude gives a
roughly square cell at mid-European latitudes (~46N, where `cos(46) ~=
0.69`, so `0.015 * 0.69 ~= 0.0104` degrees of effective width) — the region
this pipeline actually serves. The 200m elevation band matches the
resolution at which mountain weather meaningfully differs (freezing level,
precipitation type). Reuse-first, rather than "quantise then always
`get_or_create`", is necessary because a pin near a cell edge can be
physically closer to a point in the *adjacent* cell than to anything in
its own — checking the neighbourhood before minting a new row avoids
needlessly fragmenting nearby pins into separate forecast fetches. The
750m / 150m thresholds are independent of cell size deliberately: they
bound how far a pin may drift from its assigned point, not the grid
geometry.

**Consequences.** `resolve_forecast_point()` mints a new row for every pin
that has no existing candidate within both thresholds, even for pins whose
cell matches an existing row's cell exactly (elevation can still exceed the
band tolerance). `_haversine_m()` is a private copy local to
`apps/weather/services/forecast_points.py`, mirroring
`apps/mcp_server/resolvers.py::_haversine_km`. Extracting a shared `core`
haversine helper is deferred — the `mcp_server` copy is private to that
app, and refactoring it as part of this ticket would be scope creep; a
future ticket touching either call site should do the extraction.
`ForecastPointQuerySet.active()` filters to points with at least one live
`Favourite` **or** `regions.Resort` (SNOW-503 widened it from
favourite-only) — the set `fetch_weather`'s point pass polls, so a resort
anchored to a point (via `manage.py link_resort_forecast_points`) is
polled exactly like a user favourite, and a point shared by both is
counted (and polled) once.
