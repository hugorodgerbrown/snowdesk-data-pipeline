---
name: resorts-appear-from-z8
description: Resort dots (resorts-pin) start at z8 via RESORT_PIN_MIN_ZOOM, resortPinFilter; pinned resorts at every zoom; reverses SNOW-543
status: current
last-reviewed: 2026-11-10
---

# Resort dots start at z8

**Decision (Hugo, 10 Nov 2026).** The resorts layer (`resorts-pin`) draws
nothing below z8 (`RESORT_PIN_MIN_ZOOM` in `static/js/map.js`). A pinned
resort, the blue dot (SNOW-1229), is the exception and draws at every zoom.
Dropped pins are a different layer and are unchanged. Labels already start
at z10.

**Why.**

- At z5 on a phone, 157 resorts (40 Core, 93 Standard, 24 Minor) cover
  Switzerland in black dots. Hugo called the view "useless". Zoomed out, the
  map's job is reading danger across regions, and the dots add nothing to it.
- Search finds any resort by name, so hiding the dots hides nothing a reader
  cannot reach.
- The weather symbols already wait for z7 (`WEATHER_MIN_ZOOM`), so a layer
  that waits for a zoom is already the map's pattern.

**What it reverses.** SNOW-543 drew every tier at every zoom. Its point,
that small high resorts must not be hidden before big ones, still holds:
no tier hides before another, so all resorts appear together at z8.

**Options not taken** (mockups in the project's `map-declutter/` folder):

- *Grow in by tier* (Core first, Standard at z7, Minor at z9). It was still
  busy in Valais, and it is the tier suppression SNOW-543 rejected.
- *Clusters with counts*, like the field reports. A "102" over Valais tells
  a reader nothing they can act on.

**How it is built.**

- `resortPinFilter()` is the layer's filter:
  `['>=', ['zoom'], 8]`, or `['any', <pinned slugs>, <that>]` when the user
  has pinned resorts. `applyFavouritedResortColour` rebuilds it with the
  colour. It is a filter rather than a layer `minzoom` because the pinned
  dots share the layer. It is not a zero opacity, because a dot you cannot
  see must not take a tap.
- `resortsDrawnNow()` narrows the resorts that weather symbols step aside
  for (`markResortPins`) to the ones drawn, so between z7 and z8 a symbol
  does not dodge a dot that is not there.
- The exploded-layers demo (`static/js/map_exploded.js`) frames all of
  Switzerland below z8, so it lifts the filter for its resorts sheet and puts
  it back afterwards.
