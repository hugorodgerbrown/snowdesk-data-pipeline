---
name: a-pinned-resort-is-found-in-search
description: Why a favourited resort is listed by search and drawn blue on the map, not in the Favourites panel (dropped_pins, buildPinnedResortEntries)
status: current
last-reviewed: 2026-10-25
---

# A pinned resort is found in search, and drawn as a blue resort dot

**SNOW-1229.** A favourited resort ("pinned resort") is still one
`Favourite` row with a `resort`, but it has moved surface twice over:

- **Listed by search, not the Favourites panel.** An empty search field
  lists *Pinned resorts* above *Pinned regions*; a row opens the resort.
  The Favourites panel (`favourite_list`) lists `dropped_pins()` only.
- **Drawn as a resort, not a favourite.** The resorts layer paints a pinned
  resort's dot in the favourites blue (`applyFavouritedResortColour`); the
  star layer draws dropped pins only (`DROPPED_PIN_FILTER`). It reverses
  SNOW-499, which hid a favourited resort's dot and drew a star instead.

## Why

Hugo, 25 Oct 2026, picking option C of three (A: in both search and the
panel; B: search lists everything saved, dropped pins too), "with one
amendment": a pinned resort looks different on the map, "same shape,
different colour".

C's cost in the mock-up was that the panel's "Show on the map" would hide
stars the panel no longer listed. The amendment removes it: a pinned resort
is a resort, so it is on the map exactly when the resorts are, and the
panel's switch governs only what the panel lists. Each saved thing now has
one list (pins → panel, resorts and regions → search) and one marker. It is
the move SNOW-814 made for region pins, for the same reason.

B was declined because typing can never find a dropped pin, so the empty
list and the typed results would describe different sets.

## Consequences

- `favourites.geojson` still carries resort favourites, now with
  `resort_name`, `resort_region_id` and `resort_region_name`, because it
  is the feed search reads them from (`buildPinnedResortEntries`). With
  the favourites layer off, search fetches it on the first empty focus.
- A row names the resort, not the user's label, and opens it by slug, so
  a resort named like its region (Adelboden) is reachable, which a typed
  search never offered.
- Unpinning is the star in the resort's own sheet; there is no rename for a
  pinned resort.
