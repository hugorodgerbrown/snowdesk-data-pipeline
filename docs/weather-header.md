# Weather-driven bulletin header

The bulletin detail page renders a graphic header band whose appearance varies with the current weather conditions and the time of day at the bulletin region. Data lives in `WeatherSnapshot` (see [`bulletins/models.py`](../bulletins/models.py)); display logic lives in [`bulletins/services/weather_display.py`](../bulletins/services/weather_display.py); markup lives in [`templates/includes/bulletin_weather_header.html`](../templates/includes/bulletin_weather_header.html); CSS tokens live in [`src/css/main.css`](../src/css/main.css) under the **Weather header** section.

## Data flow

```
WeatherSnapshot         build_weather_display(...)        bulletin_weather_header.html
(weather_code,          ┌─ bucket: clear|partly_cloudy    ┌─ data-weather-bucket="…"
 sunrise, sunset)  ───▶ │  cloudy|fog|rain|snow|thunder ─▶│  data-time-of-day="day|night"
                        ├─ is_day: bool                   └─ data-weather-code="<int>"
                        └─ time_of_day: "day"|"night"
```

`bulletin_detail` in [`public/views.py`](../public/views.py) fetches the snapshot via `WeatherSnapshot.objects.for_date(target_date).filter(region=region).first()` and passes the `WeatherDisplay` dict (or `None`) into the template context as `weather_display`. The partial short-circuits to a no-op when `weather_display is None`, so callers can include it unconditionally.

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

## Failure modes

* **No snapshot for (region, date)**: `weather_display` is `None`; partial renders nothing.
* **Snapshot for a different region**: filtered out by the `.filter(region=region)` clause; cannot leak into another region's page.
* **Unknown WMO code**: falls back to `cloudy` rather than raising. A warning would be over-alert: the data set already includes long-tail codes Open-Meteo occasionally adds, and a single rogue value should not 500 the page.
