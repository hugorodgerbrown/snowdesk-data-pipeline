---
name: notes-about-the-day-sit-behind-a-bell
description: HISTORICAL — why facts about the map's day sat behind the date-row bell (map_day_notes.js, window.pwaDayNotes)
status: historical
last-reviewed: 2026-10-30
---

# Notes about the day sit behind a bell

> **Superseded by SNOW-1333** ([the bell lists your alerts](the-bell-lists-your-alerts.md)).
> The day's notes were dropped and the bell lists the account's bulletin
> alerts and nothing else. `map_day_notes.js` and `window.pwaDayNotes` are
> gone; the layers menu's Weather row keeps its "Forecast only" line.

**SNOW-1206.** Things that are true of the day on the map, rather than of
anything drawn on it, go in the day's notes: one bell in the date row
(`#map-day-notes`, `static/js/map_day_notes.js`) that shows only while
there is a note, carries a dot while one is unseen, and opens a card that
lists them. A module adds or withdraws a note with
`window.pwaDayNotes.set(id, note | null)`. Two write to it today:
`map_day_nav.js` (no bulletin covers the day, and a press to the nearest
that does) and `map_weather_window.js` (the weather forecast does not reach
the day).

## Why

SNOW-1195 and SNOW-1196 said each of these as a pill of its own beside the
date control, and gave the problem-terrain layer a chip there too. On an
iPad in portrait the three overran the row and ran the chip under the
bottom-right roundel. Hugo's reading: neither pill said clearly what it
meant, and date-dependent metadata like this belongs behind an ephemeral
notification icon rather than in standing chrome. A bell is a fixed 32px
however many notes there are, so the row's width no longer depends on the
day.

The problem-terrain chip was not a note and was removed outright. It named
a layer the layers menu and the legend already name, and the region
sheet's switch already turns off.

## Consequences

- A new fact about the day is a `pwaDayNotes.set` call with a `rank`, not
  a new element in the date row. Nothing standing goes in the row beside
  the date control except the date control's own parts and the stale-day
  notice (SNOW-920), which is about the page rather than the day.
- The dot means "changed since last opened", judged on the notes' ids,
  titles and details. Stepping to a day with the same notes brings no dot.
- The card opens with `spareObjectSheets: true`, as the calendar does, so
  reading a note never closes the sheet being read.
- The layers menu's Weather row keeps its own "Forecast only" line: that
  is where someone looking for the layer looks.
