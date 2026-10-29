---
name: a-sheet-names-its-day-in-its-section-headers
description: Map sheets date their sections (_dated_eyebrow.html, section_day, another_day); no "Data from" row or in-sheet Back to today
status: current
last-reviewed: 2026-10-29
---

# A map sheet names its day in its section headers

## Decision

The map's region, resort and weather sheets carry no full-width "Data from
29 Oct 2026" row (`includes/_sheet_date_row.html`, removed). Each DATED
section names its own day at the right of its eyebrow
(`includes/_dated_eyebrow.html`): a quiet "Today", or on another day the
date in the info colours ("Mon 27 Oct").

- Dated: the bulletin (the region sheet's first bulletin heading — Day Risk
  Profile when shown, else Avalanche Problems; the resort sheet's "Avalanche
  bulletin • SLF"), the weather (the resort's "Weather • Open-Meteo HH:MM"
  and the weather sheet's eyebrow of the same name over its day line) and
  "On the ground" today. Lists that are not about a day — the resorts in a
  region, "Where this is" — carry none.
- The sheet views pass `section_day` (with `another_day`); the bulletin and
  resort pages do not, so the shared bodies render plain eyebrows there and
  the pages keep their title card and another-day strip.
- No "Back to today" in a sheet. The date control's "Today"
  (`static/js/map_today.js`) is the way back.
- "On the ground" lists today's reports with no "Live today" over them.

## Why

Hugo, 29 Oct 2026: the row took about 56 px under every sheet's header and,
on today, said what the date control above the map already said. Three
options were mocked (row only on another day; "· Today" in the eyebrow; the
day at the eyebrow's right). The third was chosen, then the row was dropped
on other days too: "Date is everywhere on the page. Users aren't that
stupid." The date control, the blue tag on every dated heading and the
"Today" button already say it.

## Consequences

- This reverses the-sheet-is-the-page.md's "the body's eyebrows drop their
  dates in the map": in the map they now carry them.
- A new dated section in a sheet's body passes `day=section_day
  another_day=another_day` to `_dated_eyebrow.html`; an undated one uses
  `_eyebrow.html`.
- At its lowest, a phone sheet on today shows no date; the date control is
  the signal.
