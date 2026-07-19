---
name: weather-header
description: Weather bulletin header and ForecastPanel — WMO buckets via build_weather_display, is_day, Meteocons icons, build_point_forecast_panel
status: current
last-reviewed: 2026-07-19
---

# Weather-driven bulletin header

The bulletin detail page renders a unified header panel whose appearance varies with the current weather conditions and the time of day at the bulletin region. Data lives in `WeatherSnapshot` (see [`bulletins/models.py`](../bulletins/models.py)); display logic lives in [`bulletins/services/weather_display.py`](../bulletins/services/weather_display.py); markup lives in [`templates/includes/bulletin_header.html`](../templates/includes/bulletin_header.html); CSS tokens live in [`src/css/main.css`](../src/css/main.css) under the **Weather header** section.

The bulletin page always renders `templates/includes/bulletin_header.html` — the unified panel (region wayfinding + date + weather hero icon + condition label + sunrise/sunset). There is no feature flag controlling template selection; the `weather_header` flag, the legacy `bulletin_masthead.html` partial, and the pre-SNOW-100 band partial (`bulletin_weather_header.html`) were all removed when the unified header shipped (SNOW-100). The only flag in the inventory is `edit_map` — see [`docs/feature-flags.md`](feature-flags.md).

## Data flow

```
WeatherSnapshot         build_weather_display(...)        bulletin_header.html
(weather_code,          ┌─ bucket: clear|partly_cloudy    ┌─ data-weather-bucket="…"
 sunrise, sunset)  ───▶ │  cloudy|fog|rain|snow|thunder ─▶│  data-time-of-day="day|night"
                        ├─ is_day: bool                   │  data-weather-code="<int>"
                        ├─ icon_bucket: (12 values)       └─ hero <img> + condition label
                        ├─ condition_label: str               + sunrise/sunset strip
                        ├─ icon_filename: str
                        └─ time_of_day: "day"|"night"
```

`bulletin_detail` in [`public/views.py`](../public/views.py) fetches the snapshot via `WeatherSnapshot.objects.for_date(target_date).filter(region=region).first()` and passes the `WeatherDisplay` dict (or `None`) into the template context as `weather_display`. When `weather_display` is `None` the unified header still renders — the hero icon is omitted, weather/sunrise lines are dropped from the metadata strip, and `data-weather-bucket="none"` triggers a neutral dark fallback colour via `--color-weather-fallback`.

## Bucket map

The 30-odd WMO weather interpretation codes Open-Meteo emits collapse to seven display buckets:

| Bucket | WMO codes |
|---|---|
| `clear` | 0 |
| `partly_cloudy` | 1, 2 |
| `cloudy` | 3 |
| `fog` | 45, 48 |
| `rain` | 51–57, 61–67, 80–82 |
| `snow` | 71–77, 85, 86 |
| `thunder` | 95, 96, 99 |

Codes outside the table fall back to `cloudy` (a neutral-looking band). There is no "unknown" visual state — the page must always render.

## is_day — wall-clock projection (key design decision)

`is_day(weather, now)` does **not** compare full instants. It compares **time-of-day only**, projecting the user's current wall-clock onto the snapshot's day window:

```python
local_now = now.astimezone(weather.sunrise.tzinfo)
return weather.sunrise.time() <= local_now.time() < weather.sunset.time()
```

### Why

A naive `weather.sunrise <= now < weather.sunset` would always trail past every historical sunset (any past date's sunset is hours-or-days before *today's* `now`), so every historical-date page would render in the night theme. That is wrong for the calendar-dominated traffic pattern of this app: the user navigating to "yesterday at 11:09 my time" expects the page to look like daytime, because the sun was up then.

Projecting the time-of-day instead means:

* At 11:09 wall-clock, every date the user navigates to renders as **day**.
* At 23:09 wall-clock, every date renders as **night**.
* The visual tracks the time the user is *looking* at the page, not the real-world instant the snapshot was taken.

### Timezone handling

Open-Meteo is queried with `timezone=auto`, so each snapshot's `sunrise`/`sunset` carry the bulletin region's local offset (e.g. `+02:00` for Switzerland in summer). The function converts `now` to that offset before extracting `.time()`, so a viewer in Tokyo or San Francisco still sees a visual that lines up with daylight in *the bulletin region*, not their local night-time. The bulletin is about Swiss snow; the visual should follow Swiss daylight.

### Boundary semantics

Sunrise is **inclusive**, sunset is **exclusive** — the boundary instants land in night only on the sunset side.

## CSS tokens

14 placeholder tokens live in `@theme`, two per bucket (`--color-weather-{bucket}-day` / `--color-weather-{bucket}-night`). Tokens are intentionally **not mirrored under `.dark {}`** — the day/night split is driven by sunrise/sunset, not by the site theme, and the EAWS-style convention of theme-invariant saturated colours applies here too.

The selectors in the **Weather header** CSS section apply tokens via `[data-weather-bucket][data-time-of-day]` attribute matchers. To swap the visual design, change the token values (and optionally the rules); the markup contract stays put.

## Icon scheme

The unified header carries a hero-sized **icon + condition label** alongside the region name and date. The icon set is [Meteocons](https://github.com/basmilius/meteocons) (Bas Milius, MIT) — the static "fill" variant from the `@meteocons/svg-static` npm package (`package/fill/`). A `bg-black/35` overlay sits between the bucket-colour background and the white text/icon so contrast is maintained across all bucket × time-of-day combinations without per-bucket text colour rules.

### Icon bucket scheme (12 buckets)

The icon mapping is intentionally finer than the background-colour mapping: rain splits into `drizzle / light / moderate / heavy` and snow splits into `light / moderate / heavy`, so the icon tells the reader more than the colour band alone.

| Icon bucket | WMO codes |
|---|---|
| `clear` | 0 |
| `partly_cloudy` | 1, 2 |
| `cloudy` | 3 |
| `fog` | 45, 48 |
| `drizzle` | 51, 53, 55, 56, 57 |
| `light_rain` | 61, 66, 80 |
| `moderate_rain` | 63, 81 |
| `heavy_rain` | 65, 67, 82 |
| `light_snow` | 71, 77, 85 |
| `moderate_snow` | 73 |
| `heavy_snow` | 75, 86 |
| `thunder` | 95, 96, 99 |

Unknown codes fall back to `cloudy` — same posture as the background bucket map.

### Day/night suffix

Every bucket except `cloudy` has separate `-day` and `-night` SVGs, picked using the same `is_day` projection that drives the background. `cloudy` reads the same regardless of light, so it ships as a single `cloudy.svg`.

The filename resolver is a string concatenation: `f"{icon_bucket}-{time_of_day}.svg"` for buckets in `WEATHER_ICON_BUCKETS_WITH_DAY_NIGHT`, otherwise `f"{icon_bucket}.svg"`.

### Licence

Meteocons is MIT-licensed. The full licence text and provenance note live in [`static/icons/weather/LICENSE.md`](../static/icons/weather/LICENSE.md).

## Failure modes

* **No snapshot for (region, date)**: `weather_display` is `None`; the unified header still renders with `data-weather-bucket="none"` (neutral dark via `--color-weather-fallback`) — region name, date, and calendar trigger are present but the hero icon and weather metadata strip are omitted.
* **Snapshot for a different region**: filtered out by the `.filter(region=region)` clause; cannot leak into another region's page.
* **Unknown WMO code**: falls back to `cloudy` rather than raising. A warning would be over-alert: the data set already includes long-tail codes Open-Meteo occasionally adds, and a single rogue value should not 500 the page.

## ForecastPanel — multi-day point forecast (SNOW-417)

`build_weather_display` also accepts a `ForecastPointWeather` row (a
favourited pin's per-day forecast) alongside `WeatherSnapshot` — both expose
the same `weather_code`/`sunrise`/`sunset` trio, so the single-day builder is
shared unchanged.

For the favourite detail card's multi-day panel, `build_point_forecast_panel(snapshots, now)` in the same module wraps `build_weather_display` per day and layers on the fields a compact day strip + expandable hourly detail need:

```
[ForecastPointWeather, ...]     build_point_forecast_panel(...)      _favourite_forecast_panel.html
(7-day window, ascending  ───▶  ┌─ days: [ForecastPanelDay, ...]  ─▶  day strip (weekday, icon,
 via forecast_for_point())      │    each reusing build_weather_        hi/lo temp, snowfall chip)
                                │    display's icon_bucket/            + expandable hourly detail
                                │    icon_filename/condition_label      for the near-term days
                                │  temp_max/temp_min/snowfall_sum/
                                │  freezing_level_height/hourly
                                └─ None when snapshots is empty
```

`favourites.views.favourite_card` queries
`ForecastPointWeather.objects.forecast_for_point(favourite.forecast_point, timezone.localdate())` (ascending order — the model's default ordering is
`-valid_for_date`, the opposite of what a forward-looking panel wants),
slices to `POINT_FORECAST_DAYS`, and passes the panel or `None` into
`_favourite_card.html` as `forecast_panel`. `None` renders the existing
"Point forecast coming soon" empty state.

`hourly` on each `ForecastPanelDay` is that row's `hourly_series` (or `[]`)
— populated for only the first `POINT_HOURLY_DAYS` rows of the fetcher's
window (see [`docs/management-commands.md`](management-commands.md)), so
days beyond that render the compact strip only, with no expandable detail.
