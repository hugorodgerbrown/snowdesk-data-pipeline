---
name: weather-surfaces
description: Weather UI — _weather_panel.html, _forecast_panel.html, build_weather_display, is_day, /api/weather.geojson, map_weather_core.js
status: current
last-reviewed: 2026-08-30
---

# Weather surfaces

Everything a user can see of the Open-Meteo domain. The data behind it —
one `Weather` row per `(location, observed_on)`, with the forward days in
its `forecast` column — is
[`docs/decisions/weather-is-one-immutable-location-row.md`](decisions/weather-is-one-immutable-location-row.md);
this document is the four places that row is read.

This file replaces `docs/weather-header.md`, which described a surface that
no longer exists. That document was about a bucket-coloured band across the
top of the bulletin page whose hue and icon shifted between a day and a
night palette; SNOW-762 removed it along with the rest of the estate, and
SNOW-761 rebuilt weather on a different shape. Nothing below is a
restoration.

## The surfaces

| Surface | Anchor | Which day |
|---------|--------|-----------|
| Bulletin masthead ([`templates/includes/bulletin_header.html`](../templates/includes/bulletin_header.html)) | `MicroRegion.centroid_location` | the page's date |
| Resort page ([`apps/public/templates/public/resort.html`](../apps/public/templates/public/resort.html)) | every `ResortLocation.location` | today |
| Favourite card ([`apps/favourites/templates/favourites/partials/_favourite_card.html`](../apps/favourites/templates/favourites/partials/_favourite_card.html)) | `Favourite.location` | today |
| Location forecast page ([`apps/public/templates/public/location_weather.html`](../apps/public/templates/public/location_weather.html)) | one `Location` | `?d=` or today |
| Map overlay ([`static/js/map.js`](../static/js/map.js)) | every `Location.objects.public()` | the scrubbed date |

The first four are server-rendered; the map overlay is a GeoJSON feed and a
MapLibre symbol layer, plus a tap sheet
([`apps/public/templates/public/partials/_weather_detail.html`](../apps/public/templates/public/partials/_weather_detail.html))
that hands off to the location forecast page.

**Each component appears once (SNOW-782).** The one-day row
(`_weather_panel.html`) goes wherever a location is named; the week
(`_forecast_panel.html`, `_forecast_chart.html`) and the day
(`_hourly_chart.html`) belong to the location forecast page alone. A
surface that names a location shows the day and links to the rest — the
resort page and the favourite card both drew the whole week inline until
SNOW-783, the resort page once per curated altitude.

## The read path

Always:

```python
Weather.objects.for_location(location).on_date(observed_on).first()
```

**Never `.order_by("-fetched_at").first()`.** Today's row is updated in
place rather than appended, so ordering by fetch time returns the same row
while implying there were several — and a reader who believes that will
eventually write a query that picks the wrong one.

`None` is an ordinary answer, not an error:

* a region with no `centroid_location` has nowhere to read from;
* a **historical date has no row at all**. Rows begin on the day the estate
  was first fetched, and SNOW-731's backfill is deferred. Every surface
  must degrade to *no panel* — not an empty shell, not a "no data" notice,
  and certainly not an exception.

## The display service

[`apps/weather/services/weather_display.py`](../apps/weather/services/weather_display.py).
Takes one `Weather | None` and reads its fields directly. (The pre-SNOW-762
version took a `WeatherSnapshot | ForecastCellWeather` union and hedged
every read behind `getattr(..., None)` because the two models disagreed on
what they carried. There is one model now.)

| Function | Returns | Read by |
|----------|---------|---------|
| `build_weather_display(weather, now)` | `WeatherDisplay \| None` | `_weather_panel.html` |
| `build_point_forecast_panel(weather, now)` | `ForecastPanel \| None` | `_forecast_panel.html` |
| `build_point_weather_days(weather)` | `{date: {code, tmax}}` | `/api/weather.geojson` |

`build_point_forecast_panel` builds the whole outlook from **one row**: its
own `observed_on` leads, and the rest are the entries in its `forecast`
column, in the provider's own `daily.time` order.

### Two bucket maps

Both key off the WMO weather interpretation code (0–99), and both fall back
to `cloudy` for an unrecognised code — a neutral default, so one rogue code
can never take a page out.

* `WEATHER_BUCKETS` (7) — the coarse grouping the `--color-weather-*`
  design tokens are named for.
* `WEATHER_ICON_BUCKETS` (12) — the finer grouping that picks a Meteocons
  SVG. Rain splits into drizzle / light / moderate / heavy, snow into
  light / moderate / heavy.

Every bucket but `cloudy` ships a day and a night variant;
`weather_icon_filename` picks between them.

### `is_day`

Compares **time-of-day only**, never full instants. The reader's current
wall-clock time is projected onto the row's day, in the tzinfo the row's
`sunrise` carries. At 11:00 local every date renders as daytime; at 23:00
every date renders as night.

That is deliberate and it is about a calendar dominated by historical
pages: the sun rose and set on those days too, and the visual should track
the time the reader is *looking* at the page, not the instant the row was
written. Daylight is **sunrise-inclusive and sunset-exclusive**, so only
the sunset boundary lands in night.

The logic is unchanged from the version that shipped. SNOW-761 narrowed its
signature and nothing else.

> **Any test asserting an icon filename needs `@freeze_time` at midday.**
> The day/night suffix follows the current clock, so an unfrozen assertion
> passes locally and fails in CI after sunset.

## The two partials

### `includes/_weather_panel.html`

One day at one location: icon, condition label, hi/lo, snowfall, freezing
level, sunrise–sunset. Renders **nothing at all** when `weather_display` is
falsy.

It carries no colour of its own and no chrome — a bare row that drops into
whichever card includes it. That is the difference from the partial it
replaces, which owned the bulletin masthead's `<h1>`, eyebrow and share
button as well; that coupling is why the strip took the masthead with it.

Each measurement group renders independently, so a partially-populated row
shows what it has. Open-Meteo drops variables depending on which model
backs the coordinates, so a null is ordinary. Snowfall and freezing level
are tested `is not None` rather than for truthiness: 0 cm of new snow is a
statement on an avalanche page, and a freezing level of 0 m means freezing
at the valley floor.

**The `--color-weather-*` tokens render nothing.** They are still
registered in the component library and still defined in
[`src/css/main.css`](../src/css/main.css); SNOW-761 chose not to bring the
coloured band back, because three surfaces now include this partial and
each owns its own chrome. The panel still emits `data-weather-bucket` and
`data-time-of-day`, so a later ticket that wants the band has the hook and
the palette in place.

### `includes/_forecast_panel.html`

The week ahead: a scrolling day strip, then one
`includes/_collapsible_panel.html` per day that carries an hourly series,
bodied by `includes/_forecast_hourly_body.html`.

**`hourly` is optional per forward day.** Only the first few entries carry
one (`HOURLY_DAYS` in
[`apps/weather/services/fetch.py`](../apps/weather/services/fetch.py));
beyond that the key is **absent**, not null. `ForecastDay.hourly` is
`NotRequired` for exactly that reason, and both the service and the
template test for presence rather than assuming.

## The map overlay

### `GET /api/weather.geojson`

One Location-anchored feed. It replaces two — a resort-anchored
`forecast-weather.geojson` and a region-anchored `region-weather.geojson` —
because a region centroid is now a `Location` like any other, so there is
no tier to choose between and no zoom threshold to choose it at.

```json
{
  "type": "Feature",
  "geometry": {"type": "Point", "coordinates": [7.5, 46.1]},
  "properties": {
    "location_id": 42,
    "name": "Mont Fort",
    "elevation_m": 3328.0,
    "days": {"2026-08-30": {"code": 71, "tmax": 4.0}}
  }
}
```

Four properties and no more. No `kind`, no `resort_id`, no `region_id` —
nothing on the map reads them, and a field every visitor downloads and no
visitor sees is payload for nothing.

**The privacy contract is the load-bearing part.** The feed is filtered by
`Location.objects.public()` — the curated estate, reachable from a
`ResortLocation` or a `MicroRegion.centroid_location`. Never `active()`,
which also reaches every location a `Favourite` points at: that is the set
worth paying Open-Meteo for, not the set anyone may see. Serving `active()`
here would publish a stranger's private pin and its coordinates.
`tests/public/test_weather_geojson_api.py` asserts a favourite-only
location is absent, because a comment would not have caught it.

A location with no row for today still gets a feature, with an empty
`days`. The map filters it out at draw time, and an absent feature would be
indistinguishable from a location we do not know about.

### `static/js/map_weather_core.js`

Every pure part of the overlay, so all of it is Vitest-covered
(`tests/js/test_map_weather_core.js`). Three decisions live here:

**The icon is derived client-side.** The feed carries the WMO code, so
`iconForCode` mirrors `_WMO_CODE_TO_ICON_BUCKET`. The two tables are held
together by `tests/weather/services/test_icon_table_parity.py`, which
parses the JavaScript and asserts equality — a mirror with no guard is
drift waiting to happen. That test lives in pytest because the assertion is
about a Python constant, which Vitest cannot see.

The map always draws the **day** variant. A map symbol summarises a whole
calendar day, so switching every station to night icons because the reader
is up late says something about the reader rather than about the day. The
server-rendered panels, which show one place at a time, still call `is_day`.

**The label carries the altitude**, under the temperature. Two degrees at
1500 m and two degrees at 3000 m describe different weeks, and on a map
showing both at once the number alone is misleading. A station with no
resolved `elevation_m` falls back to the temperature alone.

**Clustering collapses to the lowest station**, and it is a pure transform
here rather than MapLibre `clusterProperties`. MapLibre's cluster
accumulators do `min`/`max` on numbers but have **no argmin**, so "the icon
belonging to the lowest member" is not expressible as a cluster property at
all. Lowest rather than nearest-to-centre because the valley station is the
one a reader can place, and its freezing level is what decides whether it
is raining where they parked. A station with no resolved elevation sorts as
infinitely high, so it only ever wins a cluster it is alone in.

### The MapLibre half

In [`static/js/map.js`](../static/js/map.js), around `installWeatherLayer`:

* **Icons are rasterised, not SDF.** Meteocons are multi-path and
  gradient-filled; an SDF registration keeps only the alpha mask and would
  discard the colour. They are decoded through an `<img>` and a 2D canvas
  into raw `ImageData` and registered with `map.addImage`, memoised per
  filename so the re-register after a basemap `setStyle` is synchronous.
  This is the one thing jsdom cannot check, and the one Playwright test
  (`tests/e2e/test_map_weather_overlay.py`) exists for it.
* **A date change re-projects, it does not re-fetch.** The payload carries
  the whole forecast window.
* **A zoom change re-collapses**, on `moveend` rather than `zoom` — the
  collapse walks every feature, and running it per pinch frame would do
  that work sixty times a second for a picture nobody sees until the
  gesture ends.
* Below `WEATHER_MIN_ZOOM` (7) the layer does not draw at all. A condition
  icon per station across a whole country is a texture, not information.

## Related

* [`docs/decisions/weather-is-one-immutable-location-row.md`](decisions/weather-is-one-immutable-location-row.md)
  — the model.
* [`docs/decisions/location-is-the-primitive.md`](decisions/location-is-the-primitive.md)
  — why everything anchors on `Location`.
* [`docs/map-and-api.md`](map-and-api.md) — the endpoint table.
* [`docs/i18n.md`](i18n.md) — why the label's strings live in JavaScript
  and what that costs.

**The bulletin's own weather prose is not this.** `weather_forecast`,
`weather_review` and `tendency` are the forecaster's text, parsed from
CAAML and rendered in the bulletin page's "Snowpack & Weather" panels. They
have nothing to do with Open-Meteo, and a grep-and-delete on "weather"
breaks both.
