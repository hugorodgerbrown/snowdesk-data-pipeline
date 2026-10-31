---
name: weather-surfaces
description: Weather UI — _weather_detail, _weather_panel, _weather_day_picker, _weather_day_line, _resort_forecast, build_weather_display, geojson
status: current
last-reviewed: 2026-10-31
---

# Weather surfaces

Everything a user can see of the Open-Meteo domain. The data behind it —
one `Weather` row per `(location, observed_on)`, with the forward days in
its `forecast` column — is
[`docs/decisions/weather-is-one-immutable-location-row.md`](decisions/weather-is-one-immutable-location-row.md);
this document is the places that row is read.

This file replaces `docs/weather-header.md`, which described a surface that
no longer exists. That document was about a bucket-coloured band across the
top of the bulletin page whose hue and icon shifted between a day and a
night palette; SNOW-762 removed it along with the rest of the estate, and
SNOW-761 rebuilt weather on a different shape. Nothing below is a
restoration.

## The surfaces

| Surface | Anchor | Which day |
|---------|--------|-----------|
| Favourite card ([`apps/favourites/templates/favourites/partials/_favourite_card.html`](../apps/favourites/templates/favourites/partials/_favourite_card.html)) and a pin's place sheet ([`apps/public/templates/public/partials/_place_sheet_body.html`](../apps/public/templates/public/partials/_place_sheet_body.html)) | the nearest `public()` location with a row for the day, at any distance, named under the weather (`nearest_forecast`) | today, or the map's day on the sheet |
| Resort forecast strip ([`apps/public/templates/public/partials/_resort_forecast.html`](../apps/public/templates/public/partials/_resort_forecast.html)) | the resort's BASE / MID / TOP `ResortLocation`, the pin standing in for Base | today and the six days after it |
| Location forecast page ([`apps/public/templates/public/location_weather.html`](../apps/public/templates/public/location_weather.html)) | one `Location` | `?date=` or today |
| Map overlay ([`static/js/map.js`](../static/js/map.js)) | every `Location.objects.public()` | the scrubbed date |
| Plan page day tiles ([`apps/trips/templates/trips/partials/_trip_when.html`](../apps/trips/templates/trips/partials/_trip_when.html)) | the nearest `public()` location with a row today, at any distance from the route's start (`nearest_weather`) | each tile's day, today and the four after it |
| Trip page forecast line ([`apps/trips/templates/trips/partials/_trip_weather.html`](../apps/trips/templates/trips/partials/_trip_weather.html)) | the nearest `public()` location to `Trip.meeting_point` with a row for the day, at any distance (`meeting_point_weather`) | the trip's day |

The first three are server-rendered; the map overlay is a GeoJSON feed and a
MapLibre symbol layer, plus a tap sheet
([`apps/public/templates/public/partials/_weather_detail.html`](../apps/public/templates/public/partials/_weather_detail.html))
that hands off to the location forecast page.

**The resort page draws a week per point (SNOW-1157).** It is still a
router to the two documents (SNOW-807), but its Forecast section is now
the design system's forecast strip: one chip per point — Base, Mid,
Summit, each with its elevation — over that point's seven days. The
points are the resort's `ResortLocation` links by role (primary first
within a role); the resort's pin stands in for Base when no link has
that role, a role with no location gets no chip, and a link with a blank
role is no point at all. `_resort_forecast_points` reads every point's
row in one batched `Weather.objects.on_date(timezone.localdate())` query
and builds each week with `build_point_forecast_panel` — the row's own
day, then its `forecast[]` — capped at `FORECAST_DAYS`. Today's tile
leads, wider, with a Max / Min / New snow list; the six after it carry a
weekday, an icon, high, low and new snow, and zero new snow reads "0 cm".
Each pane closes with `_weather_provenance.html` in its linked shape and
"Read the full 7-day forecast →" to that point's own page; a point with
no row today says "No forecast yet." and keeps the link.

The chips are radios and the reveal is CSS-only, like the day picker:
the radios come first in the card, so `:checked ~` reaches both the chip
and the pane, and the rules in [`src/css/main.css`](../src/css/main.css)
("Resort forecast points") pair a `data-point` index on both ends. The
tiles are **not** the day picker's cells, which are labels for radios
with a selection marker: these are inert summaries, each a
`role="group"` with a spoken label.

The list it replaced — SNOW-1125's link per location with
`_weather_panel.html` `brief=True` beside it — is gone, along with
`_resort_location_links`. `brief` has no caller left.

**The bulletin masthead is not on this list, and that is the point
(SNOW-784).** It carried a row read off `MicroRegion.centroid_location`
until SNOW-784 removed it: a micro-region spans thousands of metres of
vertical, so one centroid point presented under a regional heading claims
more than it knows. The bulletin's own weather — `weatherReview`,
`weatherForecast`, `tendency` — is the forecaster's pan-regional prose and
is unaffected; see the closing note in this file.

**Each component appears once (SNOW-782).** The one-day row
(`_weather_panel.html`) goes wherever a location is named; the week as
navigation (`_weather_day_picker.html`) and the day
(`_weather_day_line.html`, `_hourly_chart.html`) belong to the location
forecast page alone. The favourite card shows the day and links to the
rest. The resort page drew the whole week inline until SNOW-783, once per
curated altitude, lost the day too in SNOW-807 and got a brief day back
in SNOW-1125; SNOW-1157 gave it the week again as a summary strip — no
day line, no meteogram, no picker — that links to the location page for
those.

**The map's weather sheet is the page (Hugo, 24 Oct 2026).** Under its
peek row — symbol, name and height, the map's day, that day's high/low,
new snow and wind — the sheet renders the location page's own body,
[`public/partials/_weather_body.html`](../apps/public/templates/public/partials/_weather_body.html):
the day picker opened on the map's day, the selected day's line, its
meteogram and the provenance line, from the page's own
`_location_forecast_context`. This reverses SNOW-1101's split, which kept
the week on the page behind a "This week →" link, and is the move
[`the-sheet-is-the-page`](decisions/the-sheet-is-the-page.md) made for the
region and resort sheets. On a phone HALF ends under the week
(`data-sheet-half` on the picker) and FULL is the selected day. The page's
masthead, with its onward links, stays in the page's frame. The figures
grid the card carried (freezing level, snowfall, wind, precipitation
chance) went with it: freezing level is in the day line, and the rest are
in the meteogram for the days that have one.

**The sheet draws a day as strips, not the meteogram (SNOW-1219).** One
difference from the page: under the picker, a day with an hourly series
opens [`includes/_day_strips.html`](../templates/includes/_day_strips.html)
(built by [`apps/weather/services/day_strips.py`](../apps/weather/services/day_strips.py)),
and a day past the hourly window opens
[`includes/_day_figures.html`](../templates/includes/_day_figures.html).
`_location_forecast_context(..., strips=True)` builds `day_strips` and
`day_figures` in place of `hourly_charts`, and the sheet's day line is
`brief` (it drops the freezing level, which the strips and figures
state; daylight stays on the line). Designed with Hugo on 24 Oct 2026, mobile
first; the page keeps the meteogram until the hourly follow-up.

- **Four strips on one time axis**: temperature; freezing level, split out
  of the temperature chart, with the location's height as a dotted rule
  labelled by its height alone; snow / rain as hourly bars coloured apart,
  the title its own key ("Snow ■ / Rain ■" in the bars' two fills);
  wind speed, gusts and one arrow per block, pointing at the source, the
  arrow row labelled with a compass in the gutter.
- **A row of figures under each strip, one per three-hour block**:
  temperature and freezing level as max over min (a mean is a reading
  nobody experiences), snow / rain as the block total in cm when most of
  it fell as snow and mm when not, wind as the strongest speed over the
  strongest gust. Dashed dividers carry the blocks down every strip.
- **One selector, one line**: the hour scale 00–24 in a pill track, the
  selected block a dark capsule naming both its ends. Pressing a block,
  or dragging along the timeline, moves the selection; the charts are
  not a control, because on a touch screen a touch there is a scroll and
  a chart can be on screen while the timeline is not
  ([`static/js/day_strips.js`](../static/js/day_strips.js), arithmetic in
  `day_strips_core.js`). Today opens on the current block, any other day
  on 09–12. `--block` on the section places the capsule and every band.
  Each strip has ONE band, a grid item spanning the plot and its figure
  row, so the highlight has no seam where the two met and moves in one
  transition with the capsule;
  `data-selected` marks the bold figures, the capsule's labels and the
  one visible reading in each header. Every reading is server-rendered,
  so the script writes no text.
- **The selector shows the night and every hour** (Hugo, 25 Oct 2026):
  a dot for each hour between the labels, and the hours before sunrise
  and after sunset on a darker ground inside the pill
  (`--color-strip-night`; labels and dots on it take
  `--color-strip-night-text`). The service emits `night_before` /
  `night_after` from the day's `sunrise_local` / `sunset_local` (via
  `hourly_chart._daylight`) and a `night` flag on each boundary and dot;
  a day with no readable pair loses the shading and keeps the dots. The
  dots are block-indexed, so the two inside the capsule invert with it.
  00 and 24 centre on their instant like every other label. The day line
  states the same two times, so the shading is never the only channel.
- **A later day keeps the rows**: temperature, freezing level (the day's
  highest), snow and wind, one figure each, and "Hour by hour
  from {weekday}" — the day it becomes tomorrow, `HOURLY_DAYS - 1` before.

**The picker fits its container.** A cell needs about 42px, so the picker
holds seven cells from 330px, six from 282px and five below that, dropping
from the end of the week and never dropping the checked cell — measured on
its own width (`@container`), since the docked desktop panel is narrower
than a phone ("Forecast day picker width" in
[`src/css/main.css`](../src/css/main.css)).

**A leader line ties the sheet to its symbol**
([`static/js/sheet_leader.js`](../static/js/sheet_leader.js), shared with
the place sheet, so a resort's panel points at its pin too): the
route panel's dashed bend (`route_leader_core.js`, with an `axis`), from
the tapped symbol sideways to a notch on the docked panel's right edge,
level with the place's name, or up to the phone sheet's top edge. map.js
hands the symbol's coordinate to `pwaWeatherDetail.open` (and a resort's
to `pwaPlaceSheet.open`) and publishes
`window.pwaMapProjection` to turn it into a screen point.

**A forward day is a forecast, and the sheet says so (SNOW-1105).** The
fetch writes no row ahead of today; the days after it live in today's row's
`forecast` column. So for a scrubbed date after today the sheet reads
today's row and the matching entry, opens its peek line with "Forecast",
and states at half when it was issued ("Forecast issued Sat 17 Oct, 12:20.
It may change before the day."). Today and the past carry no tag. A date
past the seven-day window reads "The forecast does not reach this day
yet", not "No weather recorded". The location page follows: a forward
`?date=` opens today's row with that day's picker cell checked, so the
sheet's "This week →" lands on the day the map was showing.

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
| `build_point_weather_days(weather)` | `{date: {code, icon, tmax}}` | `/api/weather.geojson` |

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
* `WEATHER_ICON_BUCKETS` (12) — the finer grouping that picks an SVG. Rain
  splits into drizzle / light / moderate / heavy, snow into light / moderate
  / heavy.

**The icon bucket is the weather service's reading, stored with the day**
(SNOW-1343). The code → bucket table, `WMO_CODE_TO_ICON_BUCKET`, and
`icon_bucket_for_code` live in `apps/ingest/normalise/weather_icons.py`
(exported through `apps.ingest.public`), and `forecast_entry` writes each
forward day's `icon_bucket` into the `forecast[]` entry it stores. Readers
prefer that stored bucket and fall back to deriving one from `weather_code`:
every day stored before SNOW-1343 lacks the key, and no past `Weather` or
`SourceWeather` row is rewritten to add it. The row's own day has no column
for a bucket and always derives (`weather_code_icon_bucket`, which delegates
to ingest). The background buckets and the condition labels stay in
`weather_display.py`. Which variant of a day/night pair a panel shows is
still decided at read time from the day's stored sunrise and sunset — the
hour the reader looks is not a property of the forecast.

**Which drawing of those twelve is served is a setting.** `WEATHER_ICON_SET`
defaults to `snowdesk` — our own, generated by `bin/build-weather-icons`
([why](decisions/weather-icons-are-drawn-in-house.md)). The other candidate
sets are kept for comparison at `/_icon-sets/`, and in DEBUG a `?icons=<name>`
parameter switches the served set for the session.

Only `clear` and `partly_cloudy` ship a day and a night variant
(`WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`); `weather_icon_filename` picks
between them and returns a bare `<bucket>.svg` for everything else. The set
draws a day/night pair only where a sun or a moon appears — weather in
front of a cloud is one drawing (SNOW-791), so twelve buckets resolve to
fourteen files. Some sets draw `drizzle` and `light_rain` alike — Yr
publishes no drizzle icon — and the two stay distinct in text regardless.

The set was Meteocons until SNOW-791, and the reason for replacing it is
**legibility, not ambiguity**. Its cloud bodies measured 1.07–1.16:1 against
the light card and its outline 2.56:1 — every one of those passes on a dark
card, which is what the set was drawn for. Fog was the worst of it: drawn as
a sun behind haze, it read as a sunrise. Measured at 27 px on white, the
Snowdesk set carries 29.4 mean ink against Meteocons' 7.9 and Yr's 23.1.

> **Rain versus snow was never the defect, and the metric that said it was
> is unsound.** A mean taken over the whole 27×27 tile measures how much ink
> an icon carries, not whether two icons differ: twenty strongly-differing
> pixels average away to nearly nothing against seven hundred identical ones.
> On the pixels that actually differ, both sets separate rain from snow
> about equally — Meteocons 24 px at mean ΔE 51, Yr 25 px at 53. Score a
> future candidate on the differing pixels (count and mean ΔE among them),
> and keep the whole-tile mean for the question it does answer, which is
> presence.

### The icon halo — only for the sets that need one

A pale cloud reads by its silhouette, and on a light surface that silhouette
needs an edge. Two paths can paint one:

* the three server-rendered `<img>`s carry `.weather-icon`
  (`src/css/main.css`), two chained zero-offset `drop-shadow`s reading
  `--color-weather-icon-halo`;
* the map dilates the same edge in canvas, two filtered `drawImage` passes
  under the same literal followed by one unfiltered pass to restore the
  interior.

**Both are applied per set**, from `SETS_NEEDING_HALO` in
`apps.weather.icon_sets`, and neither applies to the set that ships. The
edge is a *blur*, so it costs sharpness everywhere it lands: on a set whose
artwork already carries an edge it buys nothing and softens every mark it
passes over, which at 27 px turns a six-armed flake into a blob. Meteocons
(cloud 1.16:1 on the light card) and Yr (1.36:1) need it; the Snowdesk set
paints a fixed mid grey into the artwork instead, and MeteoSwiss and the BBC
ship their own contrast.

The token is `transparent` in dark mode, for the sets that do use it — a
pale cloud is already the lightest thing on `--color-card`, and an edge
there reads as grime rather than as a border.

`weather_icon_class` and `weather_icon_needs_halo`
(`apps/public/templatetags/weather_icons.py`) are what the templates and the
map element ask; nothing reads the set name directly.

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
coloured band back, because the surfaces that include this partial each
own their own chrome. The panel still emits `data-weather-bucket` and
`data-time-of-day`, so a later ticket that wants the band has the hook and
the palette in place.

## The location forecast page

`/weather/<short_id>/` **is the map's weather sheet** (Hugo, 25 Oct 2026:
"it exists for deep link / SEO, but it should be the same content"). Both
render the context `views._weather_point_context` builds, through the same
partials — the masthead, the day's summary line and
`public/partials/_weather_body.html` (the forecast note, the week, each
day's strips or figures, the provenance and the "Where this is" rows). The
page keeps only its frame: the meta tags and canonical link, the name as
its `<h1>`, and off today the "Showing … · Back to today" strip. A date
past the forecast window says "The forecast does not reach this day yet",
as the sheet does, rather than opening today's week. SNOW-789's question —
*which day are you planning for* — still shapes it: the week is
navigation, and everything below it describes the one selected day.

It is **document two** of the two-documents IA (SNOW-795,
[`two-documents-and-a-map`](decisions/two-documents-and-a-map.md)), keyed
on `Location.short_id` — eleven opaque characters from
`secrets.token_urlsafe(8)`, never the primary key
([`no-integer-pks-in-urls`](decisions/no-integer-pks-in-urls.md); the old
`/weather/<int>/` 301s). SNOW-799 promoted it: every *named* public
location is in `sitemap.xml`'s `locations` section, the page emits a
`<link rel="canonical">` — the bare URL when the page shows today, the
`?date=` URL for a past day, since `?date=` picks a row inside the page
rather than a different page — and the MCP server reaches it through
`list_locations_in_region` and `get_location_weather`
([`mcp-server.md`](mcp-server.md)).

| # | Region | Partial |
|---|--------|---------|
| 1 | Masthead — symbol, "Weather point", name, height | `_weather_masthead.html` |
| 2 | Summary — high/low · new snow · wind | `_weather_day_summary.html` |
| 3 | Day picker — one cell per day | `_weather_day_picker.html` |
| 4 | Selected day — date · condition, daylight | `_weather_day_line.html` (`brief`) |
| 5 | Day strips, or a day's figures past the hourly window | `_day_strips.html` / `_day_figures.html` |
| 6 | Provenance — updated · source | `_weather_provenance.html` |
| 7 | Where this is — the resort and the avalanche region | `_place_link_row.html` |

The meteogram (`_hourly_chart.html`) is on no page since then; the body
still draws it when a caller passes `hourly_charts`, and the component
library shows it.

The layout it replaces stated the same high and low three times — a
"Today" `_weather_panel`, the day cells, and an outlook chart drawing them
as shape — and named the location again in every sub-header.
`_weather_panel.html` is **not** deleted: it keeps its other consumers —
the favourite card and the help page's illustration
(`public/help/illustrations/_weather_panel.html`) — and is simply no longer
on this page.

### Three page shapes

Discriminated by `show_day_picker`, computed once in
`apps.public.views._location_forecast_context` as
`len(panel["days"]) > 1`. **Never by the date** — a same-day backfill and
a week-old one must render identically.

| Row | Picker | Day 1–2 selected | Day 3–7 selected |
|-----|--------|------------------|------------------|
| Live (today) | 7 cells | day line + strips | day line + figures |
| Backfilled (`forecast=None`) | none | day line + strips | n/a |
| No row at all | none | "No weather was recorded here for this day." | n/a |

### `includes/_weather_masthead.html`

The header both containers share: the day's symbol, "Weather point" above
the name, and the height beside it. On the page the name is the `<h1>`; in
the sheet (`in_map`) an `<h2>` that truncates, followed by the sheet's ×.
The "View resort →" / "View bulletin →" links it carried are the body's
"Where this is" rows now — both stay, because 461 of the 540 public
locations are region centroids whose only way on is the bulletin.

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

Selection has **two channels**: the border turns accent, and a marker bar
under the cell's temperatures goes from transparent to filled. The border
alone would be colour alone — the handoff argued `aria-current` was the
second channel, but that attribute means *the current date*, so it is
static on day 0 and says nothing about what is selected. The bar is always
rendered at a fixed size, so filling it cannot resize the cell. A ring or a
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

One line: who the row came from, and when it was last fetched —
"Open-Meteo · updated HH:MM", the order the resort bulletin card's
"SLF · updated 17:00" uses (SNOW-1157). Two shapes: the location page's
closing line, ruled off by a hairline, and — with `link_url` — a card
footer with no hairline and the onward link under the line, which is how
each resort forecast pane ends. In the linked shape `weather` may be
`None`, and the link renders alone. A **time**, not a
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

#### What the chart draws, and where (SNOW-790)

**Anything measured in metres is blue.** The freezing-level line, the metre
ticks in the right gutter, their `m` unit, and the location's own elevation
rule all take `accent`. The elevation rule was `border-strong` until
SNOW-790, which left the one mark drawn against the metre scale as the only
metre-scale mark not coloured like one. **The elevation rule is dotted**
(`1 3`, round caps): blue alone made a solid hairline the full width of the
plot read as a second series, and the pattern is what keeps it reference
furniture beside the freezing level's long dash — a fixed property of the
location rather than something the day is doing.

**The temperature plot carries left and right edges**, and it alone.
SNOW-723's night shading had been supplying them for free — a reader took
the plot's bounds from where the shaded blocks stopped — and removing the
wash left this chart's two vertical scales with nothing closing them off.
They are frame rather than data, so they sit at the plot's fixed x, 40 and
560. The precipitation and wind charts have no vertical scale and no gutter
labels, so an edge there would be a box drawn round nothing.

Note that 40 and 560 are also where the hour-0 and hour-24 ticks stand, so
a test that looks for a vertical line at either x proves nothing about the
edges. Match on the span: an edge runs most of the plot's height, a tick
runs seven units.

**Both vertical scales carry gutter ticks.** One short mark per label,
drawn outward from the plot's own edges. Without them the °C and metre
figures floated beside the drawing with nothing joining them to the height
they name. The tick sits at the value's own height (`AxisTick["y"]`, in
viewBox units), not at the label's `top`, which carries the offset that
centres a line of text on that value — using one for the other puts every
tick half a line out.

**The clock lives in the axis, not on the plots.** Sunrise, sunset and the
current time are drawn in the **temperature hour axis**, which thickens into
a bar: a night ground, a lit segment between sunrise and sunset, and a
pin crossing it at the time now. Before SNOW-790 the time was a
full-height hairline through all four SVGs, crossing every series — the one
mark on the drawing that was not data but was drawn like it.

The bar is on the temperature axis **alone**. SNOW-723 had shaded all three
plots and removed the wash on the grounds that a bar is a bar and a gust is
a gust whether the sun is up; that finding still holds for the plots. What
changed is where the light belongs — it answers *when*, which is the axis's
question, not the series'.

One mark is deliberately absent from the legend: the dashed **0 °C rule**,
still drawn on the temperature plot but with no key row, because it meets a
labelled °C axis at zero and says what it is. The key is for marks a reader
cannot place.

The **daylight bar** does get a row — last, and the only entry naming
something on the axis rather than a series — drawn with the bar's own two
fills so it is matchable by colour. The pin at the current time has no row
of its own: it is the one mark whose position is its meaning, and a reader
who has found the bar has found it.

The pin is **two tones, and that is not decoration**: no single flat colour
can clear 3:1 against both fills in light mode, which is arithmetic rather
than taste. Separating from slate-600 needs a relative luminance of at least
0.358; separating from amber-500 needs either 1.54 — brighter than white —
or at most 0.127. No number satisfies both. So it is a `text-1` core inside
a card-coloured outline, and whichever fill it crosses one of the two
carries it: 8.2:1 on the lit segment, 7.6:1 on the night ground, mirrored at
10.2:1 and 4.0:1 in dark. Change either fill and re-check both.

It also **crosses** the bar rather than sitting inside it, which is why it
is a sibling of the track element and measured against the drawing, while
the lit segment is a child measured against the track — the track's
`overflow-hidden` would clip the pin's ends. The times themselves are not in the key;
the bar is `aria-hidden`, so the pair is spoken in the chart's own
`aria_label` — not in the legend's "About this forecast" block, which is
supplied by the component library alone and never renders on the page that
ships the chart.

The sunrise/sunset pair is read by `_daylight`, which accepts both shapes
its callers hold: `sunrise_local`/`sunset_local` as `"HH:MM"` (what
`build_point_forecast_panel` puts on a `ForecastPanelDay`) and
`sunrise`/`sunset` as an ISO string or a `datetime` (the committed sample
days, and a `Weather` row). Both are read as **times of day** — see the
function's docstring for the latitude limit that implies.

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
    "short_id": "Ab3dE_fGh1J",
    "name": "Mont Fort",
    "elevation_m": 3328.0,
    "days": {"2026-08-30": {"code": 71, "icon": "light_snow.svg", "tmax": 4.0}}
  }
}
```

Four properties and no more. No `kind`, no `resort_id`, no `region_id` —
nothing on the map reads them, and a field every visitor downloads and no
visitor sees is payload for nothing.

**The privacy contract is the load-bearing part.** The feed is filtered by
`Location.objects.public()` — the curated estate, reachable from a
`ResortLocation` or a `MicroRegion.centroid_location`. Never `active()`:
that is the set worth paying Open-Meteo for, not the set anyone may see.
The two hold the same rows since 2026-10-28, when favourites and trip
meeting points stopped buying a fetch
([why](decisions/snowdesk-decides-where-a-forecast-is-fetched.md)), but
by policy rather than by definition — a paid fetch for a private pin would
widen `active()`, and serving it here would publish a stranger's pin and
its coordinates.
`tests/public/test_weather_geojson_api.py` asserts a favourite-only
location is absent, because a comment would not have caught it.

A location with no row for today still gets a feature, with an empty
`days`. The map filters it out at draw time, and an absent feature would be
indistinguishable from a location we do not know about.

### `static/js/map_weather_core.js`

Every pure part of the overlay, so all of it is Vitest-covered
(`tests/js/test_map_weather_core.js`). Three decisions live here:

**The icon is served, not derived** (SNOW-1343). Each day in the feed
carries its `icon` filename, built by `build_point_weather_days` from the
day's stored `icon_bucket` (or its `weather_code`, for a day stored before
the bucket was), and the projection draws it as it arrives — a day with no
`icon` falls back to `cloudy.svg`. The module used to carry `iconForCode`,
a mirror of the Python table held to it by a parity test; with one table
there is nothing to mirror, and both went. `code` stays on the wire for any
reader of the raw condition. The feed reads its rows with `hourly`
deferred, and its cache key is `weather-geojson:v3:<day>` — a v2 entry
cached across the deploy would carry no `icon`.

The map always draws the **day** variant — the server serves it. A map symbol summarises a whole
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

* **Icons are rasterised, not SDF.** The condition SVGs are multi-path and
  coloured (the Yr comparison set goes further and embeds a base64 PNG for
  cloud shading in every file but the clear-sky pair); an SDF registration
  keeps only the alpha mask and would discard the colour. They are decoded through an `<img>` and a 2D canvas
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
