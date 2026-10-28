---
name: a-pinned-resort-is-found-in-search
description: Why pinned resorts and dropped pins are found in search, resorts drawn blue (buildPinnedResortEntries, buildPinEntries, showFavourite)
status: current
last-reviewed: 2026-10-28
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
list and the typed results would describe different sets. **SNOW-1264
reversed that half** — see below.

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

## SNOW-1264: dropped pins are searchable places too

Hugo, 28 Oct 2026: "a favourite should be a searchable place … it's
basically a shortcut to get somewhere on the map". So option B, declined
three days earlier, is now the rule for dropped pins:

- **An empty field lists them first**, under *Pinned places*, above
  *Pinned resorts* and *Pinned regions*. Named pins by name, then unnamed
  ones (as "Unnamed pin") newest first.
- **Typing finds a pin by its name**, ranked with regions and resorts and
  grouped under the same *Pinned places* heading. Its region is shown
  ("in …") but not matched on, so typing a region's name still leads with
  the region.
- **Picking one flies to the pin and opens what a tap on it opens** —
  `showFavourite(uuid)`, the path the `/map/?favourite=<uuid>` deep link
  already took, so the two cannot drift. It switches the favourites layer
  on if it was off, as the deep link does. The row previews no region
  outline: it goes to a point, not to the region the point sits in.

The objection that stopped B still holds for one case and is accepted
rather than worked round: **an unnamed pin cannot be found by typing.**
Snowdesk does not name a pin for the user — no nearest-place lookup, no
prompt on drop. Hugo: "Don't do anything clever — this is UGC — they
decide." An unnamed pin is in the empty list and nowhere else, and naming
it (the pins panel's Rename) is what makes it typeable.

The Favourites roundel and its pins panel stay, for managing the list
(rename, share, remove). Search is how you get to a pin; the panel is how
you look after them.

`favourites.geojson` gained `region_id` and `region_name` — the
favourite's own region, null for a pin outside every region — for the
row's second line.
