---
name: the-map-opens-a-resort-in-the-sheet
description: Inside the map a resort opens in the place sheet, on every width; outside it, the page; one formatter for resort facts
status: current
last-reviewed: 2026-10-20
---

# The map opens a resort in the sheet

## Decision

**If the map is on screen, a resort opens in the place sheet. If it isn't,
it opens the resort page.** One labelled link crosses each way: the
sheet's "View resort →" and the page's "See on the map", which flies to
the pin and opens the sheet (SNOW-1161, Hugo, 20 Oct 2026).

Every way into a resort from inside the map lands on the same sheet: a pin
tap, a search pick, the region sheet's "Resorts in this region" (its links
keep the page as their `href`, for a new tab or no JS) and a
`/map/?resort=<slug>` arrival. On a desktop the sheet is the inset panel;
the MapLibre resort popup and `api:resort_popup` are gone. A saved pin
keeps its desktop popup.

The two surfaces answer different questions, so they own different things:

| | Place sheet | Resort page |
|---|---|---|
| Question | this place, on the map's day | this resort, today |
| Danger | the map's day: tile, level, problems | today's one-card summary |
| Weather | the day at the pin, then "Full forecast →" | Base / Mid / Summit, the week |
| Facts | one line: elevation · lifts · piste | the full facts card |
| Why it matters | only when written | tagline, with its blank states |
| Season calendar | — (the scrubber is the map's season) | yes |

Both read one formatter for a resort's facts, `apps.public.resort_facts`.
No public surface draws a curation placeholder; staff curate in
`/map/?edit=resorts`.

## Why

**Three surfaces described one resort three ways.** The page's facts card
and the map's meta rows (`_resort_meta_rows.html`) formatted the same
columns differently ("Piste" / "Piste length", "1,500–3,330 m" /
"1500–3330 m") and handled a blank differently (omitted / a dashed "—"
box shown to the public). The desktop popup had no danger and no weather,
and a third Save control.

**Some taps left the map.** The region sheet's resort links navigated to
the page, so a reader working the map was taken off it by a list inside
it. A search pick of a resort selected its region, so the word they typed
was never what opened. "See on the map" flew to an unnamed pin.

**The sheet follows the scrubber; the page is always today.** A week inside
the sheet read as a second date control, and the full facts list, the
season and the blank-state prompts are reference, which the page already
does better.

## Consequences

- A new way to reach a resort inside the map calls map.js's `focusResort`
  (or dispatches `snowdesk:resort-open` with a slug). It does not link to
  the page.
- A new resort fact is added to `resort_facts()`, never formatted in a
  template; the sheet's line and the page's card then cannot disagree.
- This amends [`resorts-are-search-rows`](resorts-are-search-rows.md) (a
  resort row opens the resort) and leaves
  [`sheet-detents-are-phone-only`](sheet-detents-are-phone-only.md) as it
  is: the desktop panel has no detents.
- The page stays the landing page of
  [`a-resort-page-answers-the-query`](a-resort-page-answers-the-query.md);
  nothing here moves content onto it.
