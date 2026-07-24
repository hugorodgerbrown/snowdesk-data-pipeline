---
name: resort-page-weather-shows-region-snapshot
description: Resort page renders the region's WeatherSnapshot via _weather_panel; no per-resort fetch; point weather stays favourite-only
status: current
last-reviewed: 2026-07-24
---

# Resort-page weather shows the region snapshot, not a per-resort forecast

**Decision.** The public resort page (`/resorts/<id>/<slug>/`,
`public.views.resort_detail`) renders the *parent region's* `WeatherSnapshot`
— the same one `bulletin_detail` shows on that region's bulletin page — via
the shared `templates/includes/_weather_panel.html` partial. It does not
issue a per-resort Open-Meteo fetch and does not read
`ForecastPointWeather`. Point-local weather (a favourited pin's own
multi-day forecast, `_favourite_forecast_panel.html`) remains a
favourite-page-only feature.

The weather markup itself was fully extracted out of the tuned bulletin
masthead (`templates/includes/bulletin_header.html`) into
`templates/includes/_weather_panel.html`, parameterised so it can render
with or without the region `<h1>`/subregion eyebrow/share button.
`bulletin_header.html` is now a thin `{% include %}` wrapper; the resort
page includes the same partial directly. The CSS bucket-colour hook moved
from `.bulletin-header[data-weather-bucket]…` to a shared
`.weather-bucket[data-weather-bucket]…` selector both consumers' root
elements carry.

**Why — region snapshot, not a per-resort fetch.** A resort's own
coordinates would need their own `WeatherSnapshot`/`ForecastPoint` row,
meaning either an on-demand Open-Meteo call per resort page view (the
per-favourite-pin cost model, SNOW-159/SNOW-417) or a batch job fetching
weather for every fixture-seeded resort on a schedule. Both add real
runtime or scheduling cost for a page whose visitors are, by definition,
already looking at a specific mountain inside a region whose weather is
already fetched once per bulletin cycle — the region-level snapshot is
already "close enough" for the resort page's purpose (an at-a-glance
condition check, not a precision forecast), and it is already paid for.
Reusing it costs one extra `WeatherSnapshot` query and zero new fetches.

**Why — full extraction into `_weather_panel.html`, not two copies of the
markup.** The bulletin masthead is not a self-contained "weather block" —
the bucket colour, gradient, and HTMX trigger sit on the header root, the
hero icon shares a flex row with the region `<h1>`, and the meta strip
weaves sunrise/sunset/condition inline with the date. Copy-pasting that
into `resort.html` would have produced two divergent implementations of
the same visual language on day one. Parameterising a single partial (with
`region_name`/`subregion_name`/`show_share` as opt-in extras) keeps the
weather chrome — icon buckets, day/night colours, layout-shift fix — in
exactly one place; a future bucket/icon change touches one file.

**Why — point weather stays favourite-only.** `ForecastPointWeather`
(SNOW-417) exists to serve a *placed pin's* multi-day forecast on the
favourite card, where the user has explicitly asked to track that exact
spot. A resort page has no such pin — showing per-resort point weather
would require minting a `ForecastPoint` for every resort (SNOW-159's
per-favourite cost model, multiplied by the fixture resort count) for a
feature nobody asked for on this page. The favourite flow remains the only
way to get point-local weather.

**Consequences.**

- `resort_detail` computes `weather_display` from
  `WeatherSnapshot.objects.for_date(today).filter(region=resort.region).first()`
  — identical lookup pattern to `_bulletin_detail_response`. No new model,
  no new fetcher.
- `fetch_weather_snippet` (the belt-and-braces HTMX retry endpoint) gained
  a `?variant=panel` branch so the resort page's retry re-renders the bare
  panel (`includes/_weather_panel.html`, no region `<h1>`/share button)
  instead of the full bulletin masthead. The bulletin page's retry omits
  the query param and is unaffected.
- `.bulletin-header` in `src/css/main.css` carries no CSS rules of its own
  any more — `.weather-bucket` is the styling hook both consumers'
  `data-weather-bucket`/`data-time-of-day` attributes target. Any future
  third consumer of the bucket-coloured panel should include
  `_weather_panel.html` rather than reintroducing bespoke markup.
- If a future ticket wants genuinely resort-specific weather (distinct
  from the region's), it needs its own `ForecastPoint`/fetch cost model —
  this decision does not preclude that, it just keeps the current page
  cheap.
