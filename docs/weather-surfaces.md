---
name: weather-surfaces
description: Weather UI — _weather_panel, _weather_day_picker, _weather_day_line, _weather_masthead, build_weather_display, is_day, /api/weather.geojson
status: current
last-reviewed: 2026-09-01
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
| Resort page ([`apps/public/templates/public/resort.html`](../apps/public/templates/public/resort.html)) | every `ResortLocation.location` | today |
| Favourite card ([`apps/favourites/templates/favourites/partials/_favourite_card.html`](../apps/favourites/templates/favourites/partials/_favourite_card.html)) | `Favourite.location` | today |
| Location forecast page ([`apps/public/templates/public/location_weather.html`](../apps/public/templates/public/location_weather.html)) | one `Location` | `?d=` or today |
| Map overlay ([`static/js/map.js`](../static/js/map.js)) | every `Location.objects.public()` | the scrubbed date |

The first four are server-rendered; the map overlay is a GeoJSON feed and a
MapLibre symbol layer, plus a tap sheet
([`apps/public/templates/public/partials/_weather_detail.html`](../apps/public/templates/public/partials/_weather_detail.html))
that hands off to the location forecast page.

**The bulletin masthead is not on this list, and that is the point
(SNOW-784).** It carried a row read off `MicroRegion.centroid_location`
until SNOW-784 removed it: a micro-region spans thousands of metres of
vertical, so one centroid point presented under a regional heading claims
more than it knows. The bulletin's own weather — `weatherReview`,
`weatherForecast`, `tendency` — is the forecaster's pan-regional prose and
is unaffected; see the closing note in this file.

**Each component appears once (SNOW-782).** The one-day row
(`_weather_panel.html`) goes wherever a location is named; the week
(`_weather_day_picker.html`) and the day (`_weather_day_line.html`,
`_hourly_chart.html`) belong to the location forecast page alone. A
surface that names a location shows the day and links to the rest — the
resort page and the favourite card both drew the whole week inline until
SNOW-783, the resort page once per curated altitude.

`_forecast_panel.html` and `_forecast_chart.html` were the week's two
components until SNOW-789 and are **deleted**, not renamed. Historical
ADRs still name them; those are dated records of superseded states and are
left alone.

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
* a **historical date may have no row**. Rows begin on the day the estate
  was first fetched; earlier days exist only where the backfill has been
  run (see below). Every surface must degrade to *no panel* — not an empty
  shell, not a "no data" notice, and certainly not an exception.

### A backfilled row is one day, not a week

`backfill_weather` and the `LocationAdmin` "Backfill missing weather"
action (SNOW-731) fill missing past days. A row they write carries its
daily scalars and its 24 hourly readings, but its `forecast[]` is **null on
purpose** — the historical endpoint serves a stitched timeline, not the
outlook as issued that morning, and that column means the latter.

One day is the location forecast page's second shape — no day picker at
all; see [Three page shapes](#three-page-shapes) below, which states what
renders rather than repeating it here. The missing week is correct. Do not
close it by inventing forward days from the stitched timeline.

## The display service

[`apps/weather/services/weather_display.py`](../apps/weather/services/weather_display.py).
Takes one `Weather | None` and reads its fields directly. (The pre-SNOW-762
version took a `WeatherSnapshot | ForecastCellWeather` union and hedged
every read behind `getattr(..., None)` because the two models disagreed on
what they carried. There is one model now.)

| Function | Returns | Read by |
|----------|---------|---------|
| `build_weather_display(weather, now)` | `WeatherDisplay \| None` | `_weather_panel.html` |
| `build_point_forecast_panel(weather, now)` | `ForecastPanel \| None` | `_weather_day_picker.html`, `_weather_day_line.html` |
| `build_point_weather_days(weather)` | `{date: {code, tmax}}` | `/api/weather.geojson` |

`build_point_forecast_panel` builds the whole outlook from **one row**: its
own `observed_on` leads, and the rest are the entries in its `forecast`
column, in the provider's own `daily.time` order. The same list is drawn
twice on the location forecast page — once as the picker's cells, once as
the per-day lines the picker reveals — which is why every entry carries its
own `sunrise_local` / `sunset_local` pair rather than the page reading the
lead day's.

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

## The partials

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

## The location forecast page

`/weather/<location_id>/` is five regions, and SNOW-789 rebuilt it around
one question — *which day are you planning for*. The week is navigation;
everything below it describes the one selected day.

| # | Region | Partial |
|---|--------|---------|
| 1 | Masthead — name, elevation, onward links | `_weather_masthead.html` |
| 2 | Day picker — one cell per day | `_weather_day_picker.html` |
| 3 | Selected day — date · condition, freezing · daylight | `_weather_day_line.html` |
| 4 | Meteogram | `_hourly_chart.html` |
| 5 | Provenance — updated · source | `_weather_provenance.html` |

The layout it replaces stated the same high and low three times — a
"Today" `_weather_panel`, the day cells, and an outlook chart drawing them
as shape — and named the location again in every sub-header.
`_weather_panel.html` is **not** deleted: it keeps its other three
consumers, and is simply no longer on this page.

### Three page shapes

Discriminated by `show_day_picker`, computed once in
`apps.public.views._location_forecast_context` as
`len(panel["days"]) > 1`. **Never by the date** — a same-day backfill and
a week-old one must render identically.

| Row | Picker | Day 1–2 selected | Day 3–7 selected |
|-----|--------|------------------|------------------|
| Live (today) | 7 cells | day line + meteogram | day line only |
| Backfilled (`forecast=None`) | none | day line + meteogram | n/a |
| No row at all | none | "No weather was recorded here for this day." | n/a |

### `includes/_weather_masthead.html`

Name, elevation, and both onward links. The links live here rather than in
a bottom nav because 461 of the 540 public locations are region centroids
whose **only** way on is the bulletin — a masthead naming just the resort
would strand most of this page's visitors. They are accent text links, not
buttons: a button pair under the heading competes with the picker for the
reader's first action, and the first action here is choosing a day.

### `includes/_weather_day_picker.html`

The week as navigation: one cell per day carrying a weekday, a condition
icon and a high/low, and nothing else. Freezing level, wind and the
snowfall chip were in the strip this replaces and are gone — they are
per-day facts, so they belong to the selected day's own line rather than to
seven columns at once.

**Every cell is a radio**, which is the change SNOW-789 is for. The old
strip gave one only to the days carrying an hourly series, so five of seven
columns were inert markup. `ForecastPanelDay.selectable` survives, but it
now decides only whether the selected day reveals a meteogram — the page
reads it, the picker does not.

Selection changes the **border colour and nothing else**. A ring or a
weight change would shift a cell's contents by a pixel as the reader steps
along the week, which reads as the layout twitching rather than as a
selection moving.

Why it is a radio group and not seven links:
[`docs/decisions/weather-day-picker-is-a-selector-not-navigation.md`](decisions/weather-day-picker-is-a-selector-not-navigation.md).

### `includes/_weather_day_line.html`

The selected day, stated once: date and condition, then the two figures
that decide whether a plan holds — where it is freezing, and how long there
is light. **High and low are deliberately absent**; they are in the cell
the reader just pressed and in the meteogram below, and a third statement
is the duplication the redesign removed. Freezing level is tested
`is not None` rather than for truthiness, because 0 m means freezing at the
valley floor.

Visibility lives on this row's **own root element**, via an optional
`reveal_index`, rather than on a wrapper: the reveal rule resolves to
`display: flex` and this row IS the flex container, so a wrapper would
leave the row shrunk to its content with the hairline rule pulled in with
it. Omit `reveal_index` — which the one-day page does — and the row carries
neither the hiding class nor the index and simply renders.

### `includes/_weather_provenance.html`

One line: when the row was last fetched, and who from. A **time**, not a
date — today's row is rewritten in place across four fetches a day, so how
recent it is is the whole point, and the day it belongs to is already on the
day line above. It credits **Open-Meteo** rather than naming a forecast
model: Open-Meteo picks the backing model per coordinate and does not report
which, so a page naming one would be inventing it.

### The reveal is CSS-only and hand-written, in two places, for one reason

Tailwind's `peer-checked:` compiles to the *general* sibling combinator, so
a checked input matches every later sibling — a stack of panels would show
the selected one and every one after it, and a flat picker would highlight
the selected cell and every one after it. The fix is the same on both
sides: bound the `~`. Each input/label pair gets its own wrapper (marked
`data-day-cell`), and the cross-container reveals are written out per day
index in [`src/css/main.css`](../src/css/main.css) — once for
`.forecast-day-line`, once for `.forecast-hourly-panel`.
`tests/public/test_weather_surfaces.py` asserts both structures, because
pytest renders HTML and never evaluates the CSS that depends on it.

**Both defaults are `display: none`**, so an unmatched index shows nothing
rather than everything. That is also why the one-day page renders its day
line with no hiding class at all: it has no picker, so no radio, so nothing
could ever match it.

### `includes/_hourly_chart.html`

One day, hour by hour: three charts on one x-axis — temperature,
precipitation and wind — built by
[`apps/weather/services/hourly_chart.py`](../apps/weather/services/hourly_chart.py)
(SNOW-723, placed by SNOW-786). Temperature and precipitation are hourly;
wind is three-hourly, because a gust is a peak over a span and a bearing
is only meaningful averaged over one.

Its wind arrows point at the **source**, and since SNOW-785 so does
`_weather_panel.html` — the two share the location forecast page, and
`tests/public/test_weather_tags.py` asserts they cannot drift apart.

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
* [`docs/decisions/weather-backfill-is-an-admin-action.md`](decisions/weather-backfill-is-an-admin-action.md)
  — why history arrives through a capped admin action, and why a
  backfilled row carries no outlook.
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
