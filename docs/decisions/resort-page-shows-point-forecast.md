---
name: resort-page-shows-point-forecast
description: Resort page shows the resort's own ForecastPointWeather via _forecast_panel.html below the region snapshot header
status: historical
last-reviewed: 2026-08-09
---

# Resort page shows the resort's own point forecast (SNOW-572) — supersedes resort-page-weather-shows-region-snapshot

> **Superseded by
> [`resort-page-shows-location-forecasts`](resort-page-shows-location-forecasts.md)**
> (SNOW-702). The page now renders one labelled forecast per linked
> `Location` and feeds the hero band from the primary location's day-0 row.
> What carries forward: the `_forecast_panel.html` reuse rationale, the
> `testid_prefix` idiom, the `?variant=panel` HTMX retry, and the rule that
> no resort loses weather. What is withdrawn: the region `WeatherSnapshot`
> as the unconditional header, which put region-centroid numbers directly
> above point numbers for the same day.

**Decision.** The public resort page (`/resorts/<id>/<slug>/`,
`apps.public.views.resort_detail`) renders two weather layers. The parent
region's `WeatherSnapshot` remains the page header and the fallback for
every resort — unconditional, unchanged since SNOW-509, via the shared
`templates/includes/_weather_panel.html` partial. Below it, when
`resort.forecast_point` is set and its forward `ForecastPointWeather`
window has rows, the resort's own multi-day point forecast renders too, via
`templates/includes/_forecast_panel.html` (promoted from the favourite-only
`_favourite_forecast_panel.html`, SNOW-572). There is no `{% else %}`
branch: a resort with no linked point, or a linked point with no rows yet,
renders exactly what it rendered before this decision — no resort loses
weather.

**Why this reverses the prior decision.** `resort-page-weather-shows-region-snapshot`
(SNOW-509) was correct when written: no per-resort `ForecastPoint` existed,
so showing point weather here would have meant minting one per resort and
paying its fetch cost specifically for this page. SNOW-503 anchored a
`weather.ForecastPoint` to every geocoded resort
(`Resort.forecast_point`) and folded those points into the existing
`fetch_weather` point pass — the cost SNOW-509 was avoiding is already
paid, on a schedule, for every linked resort. The richest weather data in
the system (temperatures, snowfall, wind, freezing level, two days of
hourly detail) was being fetched, stored, and never displayed, while the
page it belongs to showed only a three-field regional approximation.
SNOW-503 falsified SNOW-509's premise; this decision restores the resort
page to using what the pipeline now actually produces for it.

**Why the region snapshot stays, rather than being replaced.** Only
resorts linked by `manage.py link_resort_forecast_points --commit` have a
`forecast_point` at all, and a linked point can still have an empty
forward window (fetch lag, a newly-linked resort). Keeping the region
panel as the unconditional header means coverage gaps are invisible to the
visitor — the page never regresses to no weather at all — while the point
forecast is additive, appearing only when it has something real to show.

**Why one query, not a shared helper.** `resort_detail` queries
`ForecastPointWeather.objects.forecast_for_point(resort.forecast_point,
today)[:POINT_FORECAST_DAYS]` and calls `build_point_forecast_panel`
directly — the same two calls `apps.favourites.views._point_forecast_panel`
makes, not a call to that function. `_point_forecast_panel` also computes
`latest_fetched_at` for the favourite card's freshness stamp, which the
resort page has no use for; bending one signature across two needs was
judged worse than two call sites of roughly six lines each ("simple over
complex"; "no abstractions until needed by two callers" — the two callers
exist, but their needs already diverge on the first field).

**Why `includes/_forecast_panel.html`, not two copies of the markup
(carried forward from the superseded decision — still the live rationale
for this partial).** The favourite card already had a working multi-day
forecast panel (`_favourite_forecast_panel.html` + `_favourite_forecast_hourly_body.html`,
SNOW-417): a compact day strip plus an expandable near-term hourly detail
via `_collapsible_panel.html`. Design-system rule 1 is reuse-then-extract;
a public page reaching into `apps/favourites/templates/` for markup is the
thing to avoid, and inlining a second copy on the resort page was not an
option. The two templates moved to `templates/includes/` unchanged in
behaviour — every `data-testid` gained a `testid_prefix` parameter
(`{{ testid_prefix|default:'favourite-forecast' }}`, the same idiom
`_weather_panel.html` already used for `panel_testid`/`testid_prefix`),
defaulting to `favourite-forecast` so the favourite card's rendered output,
and `tests/favourites/test_views.py`'s existing panel assertions, are
byte-for-byte unchanged by the move. The resort page passes
`testid_prefix="resort-forecast"`.

**Consequences.**

- `resort_detail` adds `forecast_point` to the existing `select_related` on
  its `get_object_or_404`, so the FK access costs no extra query, then (only
  when `resort.forecast_point is not None`) issues one more query for the
  forward window. No new model, no new fetcher — the same window
  `fetch_weather`'s point pass already writes.
- The component-library registry entry renamed from
  `favourite-forecast-panel` (label "Favourite forecast panel") to
  `forecast-panel` (label "Forecast panel"); its description now names both
  consumers. `FAVOURITE_FORECAST_PANEL_VARIANTS` renamed to
  `FORECAST_PANEL_VARIANTS` in `apps/public/_component_fixtures.py`. The
  slug is a staff-only `/_components/` anchor — nothing else references it.
- Coverage is silent by design: an unlinked resort, or a linked one with no
  rows yet, shows only the region panel, with no visible indicator that a
  richer forecast could exist. Worth checking coverage in production around
  a deploy that changes the linked-resort count — an ops question, not a
  code change.
- **`?variant=panel` HTMX retry (carried forward from the superseded
  decision — still live, untouched by SNOW-572).** `fetch_weather_snippet`
  (the belt-and-braces retry endpoint for the *region* panel) has a
  `?variant=panel` branch so the resort page's retry re-renders the bare
  `includes/_weather_panel.html` (no region `<h1>`/share button) instead of
  the full bulletin masthead. The bulletin page's retry omits the query
  param and is unaffected. This is a property of the region panel only —
  the point-forecast section has no HTMX retry of its own; it is either
  present at render time or omitted, and a resort whose window fills in
  later shows it on the next page load.
- If a future ticket wants the point forecast's own belt-and-braces retry,
  or a "forecast coming soon" empty state to match the favourite card's,
  that is a new decision, not implied by this one — SNOW-572 deliberately
  keeps the resort page's empty case silent (identical to pre-SNOW-572
  behaviour) rather than introducing new copy for a coverage gap that is
  expected to close as `link_resort_forecast_points` runs.
