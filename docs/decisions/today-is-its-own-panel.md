---
name: today-is-its-own-panel
description: Why Today is its own panel behind the sun (favourites:today, build_today, today_panel.js); bell retired; Saved → Places in the app
status: current
last-reviewed: 2026-11-09
---

# Today is its own panel

**SNOW-1366.** A sun roundel in the map's date row, after "Today"
(`#map-today-panel-toggle`), opens the Today panel (`#today-sheet`,
`static/js/today_panel.js`). It lists the regions the reader pinned, and
the regions their pinned resorts are in, each with today's bulletin danger.
The pinned resorts sit nested under
their region, each with today's weather. It always shows today, whatever
day the map is on, and it is drawn only for a signed-in reader.

## Decision

- **Regions and resorts only.** A dropped pin is the reader's own spot,
  not a place Snowdesk publishes a reading for, so it is not listed. The
  Favourites panel lists dropped pins and nothing else.
- **Nested, not grouped.** A resort has no danger of its own
  ([a-resort-has-no-avalanche-danger](a-resort-has-no-avalanche-danger.md)).
  The region row carries the danger and the resort row under it carries
  the weather, so neither row repeats the other. A pinned resort brings in
  its region, and the panel does not say the region itself is unpinned.
- **Alphabetical.** Regions are listed by name, and so are the resorts in
  each. The list is never sorted by danger.
- **A snapshot, nothing more** (Hugo, 3 Nov 2026). Each region is one
  compact group: its name is the heading, with today's level on the right,
  and its resorts are bare rows under it, with their weather on the right.
  Nothing else appears: no subtext under the region, no trend since
  yesterday, no "not pinned", and no kind tile on either row. A region with
  no bulletin shows the muted no-rating tile where the level would be. The
  rows are the panel's own markup, not `_place_link_row.html`, and they
  keep that partial's `data-panel-open` contract.
- **A row opens its place.** Rows carry `data-panel-open`, so
  `map_panel_trail.js` opens the region or resort sheet with a back pill
  naming Today, which reopens through `window.pwaTodayPanel`. When the map
  is on another day, following a row first moves the map to today
  (`snowdesk:scrub-to`). Otherwise the sheet would contradict the row:
  a level of 3 on the row, and no bulletin on the sheet it opened.
- **Search carries no ratings.** Region rows in search get a neutral map
  glyph instead of a danger tile.
- **The bell is retired.** SNOW-1333's bell, `#map-alerts` and
  `map_alerts.js`, are removed. The sun carries the bell's red dot, read
  from `/account/alerts/recent/` under the same localStorage key, and
  opening Today clears it. Pushes and emails are unchanged.

## Why

Hugo, 2 Nov 2026. SNOW-1359 laid today's readings over the Favourites
panel and search, which duplicated favourites and mixed pins with
regions. "Let's make today a first-class surface citizen."

On the ordering, he said: "Deterministic is better for users." A list
that reorders itself by danger moves each place every morning.

On the compact layout (3 Nov 2026) he said: "Region level on the right.
Remove the subtext on the region. Keep it clean - it's a snapshot of
today." Pinning is a setting, not a condition, so the panel doesn't show it.

On the bell, he asked: "Why have notifications if the information is
right there?" An alert is a push about a place's day, and Today now
shows that same day for those same places.

## Consequences

- **In the installed phone app Today is Saved → Places (SNOW-1479).** The
  sun roundel is hidden there and Today has no tab. Places
  (`favourites:places`, `build_places`) lists the same regions and
  resorts, by the same rules, and adds the dropped pins under the region
  they fall in, with their height; pins outside every region sit under
  "Elsewhere". So "dropped pins only on the Favourites panel" holds on the
  web, not in the app. `?panel=today` opens Places, and the alert dot sits
  on the Saved tab. The web keeps this panel unchanged.
- The panel is online only. A failed load says so, rather than showing
  the empty state.
- `build_today` runs a fixed number of queries however many places there
  are (`tests/favourites/test_today.py::TestQueries`).
- Supersedes [the-bell-lists-your-alerts](the-bell-lists-your-alerts.md).
