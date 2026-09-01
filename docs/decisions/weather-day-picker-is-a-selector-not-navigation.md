---
name: weather-day-picker-is-a-selector-not-navigation
description: The weather day picker is a CSS radio group inside one Weather row's forecast window, not links pushing ?date= — no future row exists
status: current
last-reviewed: 2026-09-01
---

# The day picker selects inside a row; `?date=` selects the row

## Decision

The seven cells on `/weather/<location_id>/` are radio inputs in one
server-rendered `<fieldset>`, revealed by hand-written `:has()` rules in
[`src/css/main.css`](../../src/css/main.css). They are **not** links, and
pressing one does not change the URL.

The SNOW-789 design handoff specified each cell as an `<a>` pushing that
day's date into `?date=`. That is not implementable, and the reason is
about the data rather than about the markup.

## Why

**Six of the seven cells have no URL to point at.** `?date=` selects
*which `Weather` row* the page reads —
`Weather.objects.filter(location=…, observed_on=…)`. `fetch_weather` only
ever writes a row for the day it runs; a row for tomorrow does not exist,
so `?date=<tomorrow>` renders the "no weather was recorded here for this
day" message. The forward days are not rows at all: they are entries in
today's row's `forecast` column, which is the whole point of that column
(see
[`weather-is-one-immutable-location-row.md`](weather-is-one-immutable-location-row.md)).

So the two are **independent axes**, not two spellings of one:

| Control | Chooses | Range |
|---------|---------|-------|
| `?date=` | which row — which day the forecast was *made on* | today, and back to the day the estate was first fetched |
| the picker | which day *inside* that row's window | day 0 to day 6 of `forecast[]` |

A link-based picker would collapse them, and would 404-equivalent on six
cells out of seven.

**The CSS-only form costs nothing here.** The whole window is already in
the row the page fetched, so there is no request to make and nothing to
wait for; a link would turn a free swap into a round trip. JavaScript is
progressive enhancement in this codebase and this is core content, the
same call SNOW-723 made for the meteogram itself.

## Consequences

- **No deep link to a forward day.** Sharing the page always opens on
  today. Accepted: the forward days belong to a row that is rewritten four
  times a day, so a URL naming one would resolve to different weather
  depending on when it was opened.
- **Seven rules per reveal, written by hand.** `peer-checked:` compiles to
  the general sibling combinator, so a utility-based reveal shows the
  checked panel and every panel after it. The rules pair an explicit day
  index on both ends and are capped at `FORECAST_DAYS` (7); a longer
  window fails visibly rather than showing the wrong day.
- **A backfilled row has no picker, permanently.** Open-Meteo's historical
  API returns the day itself and no forward block, so a recovered row
  carries `forecast=None` and is one day. That page renders its day line
  and meteogram with **no** `.forecast-day-line` class — with no radio the
  `:has()` rules could never match, and a row that opted into the hiding
  class would be hidden forever. The missing week on a past page is a
  property of the data, not a gap waiting to be filled.
- **The page never branches on the calendar.** The discriminator is
  `show_day_picker` (`len(panel["days"]) > 1`), computed in
  `apps.public.views._location_forecast_context`. "Is this a past date" is
  never asked, so a same-day backfill and a week-old one render the same
  way.
