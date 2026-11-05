---
name: the-region-colour-gives-way-by-zoom
description: Region fill fades out over z8.5–10; from z10 an inside edge band and level label (regions-edge, region-labels); the region chip (map-region-chip) was removed by SNOW-1422
status: current
last-reviewed: 2026-11-05
---

# The region colour gives way by zoom

**Decision (SNOW-1375, Hugo, 2 Nov 2026).** The danger fill is for reading
across regions. From z8.5 it fades, and by z10 it is a faint trace (at most
8%, `FILL_TRACE_OPACITY`). From z10 each region instead has:

- **an edge band**: `regions-edge`, a line in the region's danger colour,
  8 px wide, offset 4 px *inside* its own edge, so two neighbouring levels
  show as two bands side by side rather than one mixed line;
- **a level label**: `regions-level-label`, "3 · Martigny-Verbier", drawn
  from its own point source (`region-labels`), placed at each region's
  centroid Location.

The existing name label (`regions-label`) stops at z10, where the level
label takes over. A region whose slopes the problem-terrain layer paints
(feature-state `terrain`) draws no fill, band or label. The Bulletin
opacity step still sets the fill's strength up to z8.5, scales the trace
and band, and turns all three off at 0
([`bulletin-fill-is-a-user-choice.md`](bulletin-fill-is-a-user-choice.md)).

**Why.**

- At z10 one region fills most of a phone screen. A 50% fill over it hides
  the contours, labels and relief the reader zoomed in to read, and it says
  nothing a single label cannot.
- The fill cannot simply vanish. Below z12 there are no terrain tiles to
  paint slopes from, and outside Switzerland there are none at any zoom, so
  something has to keep the day's level on screen. The band and label do it
  without covering the ground.
- z10 is also where a tap stops selecting a region (`REGION_SELECT_MAXZOOM`),
  so the visual change and the tap rule move together.

**How it is built.**

- `regionFillOpacityExpr` (`static/js/choropleth_core.js`) puts the zoom
  `interpolate` outermost, as MapLibre requires for `['zoom']`, with a
  per-feature `case` at each stop.
- For polygon features MapLibre's `line-offset` is an inset when positive
  (the style spec), so the band is `+4` px (`EDGE_OFFSET_PX`). That reading
  depends on the rings sharing one winding, and the stored EAWS rings are
  mixed (746 outer rings clockwise, 50 counter-clockwise across the four
  fixtures), so `/api/regions.geojson` serves every geometry through
  `rfc7946_rewind` (`apps/regions/services/geometry.py`): RFC 7946 rings,
  counter-clockwise in lon/lat, which become the clockwise exterior rings
  of the vector-tile convention once tile y runs downward. The first build
  used `-4`, which put each band on its neighbour's ground (Codex on #1411).
- `text-field` cannot read feature-state, where the rating lives. Every
  rating paint is recorded in `REGION_RATINGS` (`map_shared.js`), and
  `refreshRegionLabels` (`map.js`) rebuilds the label source once per
  frame, and again when the painted-slopes set changes. The label point is
  `properties.label` on each region, its centroid Location.
- Both new layers are bulletin data: they take the provider filter and the
  unsupported-region rule (`BULLETIN_DATA_LAYER_IDS`,
  `UNSUPPORTED_REGION_LAYER_IDS`).

**The region chip (SNOW-1376), removed by SNOW-1422 (Hugo, 5 Nov 2026).**
The chip is gone, in both map modes: its files (`map_region_chip.js`,
`map_region_chip_core.js`), its markup and strings in `_map_embed.html`,
its CSS and the `frame: false` path of `snowdesk:region-open` that only it
used. A region past z10 is reached by zooming out in Conditions or by
search. What it was:

From z10 a chip in the bottom-left stack,
just above the date row (`#map-region-chip`, `map_region_chip.js` with its
rules in `map_region_chip_core.js`), names the region at the map's centre
and its level, so the level stays on screen when one region fills it. Its
second line, from z12, says what the slopes show: "slopes" where they are
painted, the reader's own Steep terrain filter when that is on, or "Slopes
are drawn in Switzerland only" outside the tiles. It hides with no region
or no rating, and while the route rail is open. A tap opens the region's
sheet through `snowdesk:region-open` with `frame: false`, so the camera
stays where the reader put it.

**Consequences.**

- A region with no centroid Location has no level label; its band still
  draws. `link_region_centroid_locations --commit` gives one to every region.
- The level is text, not a coloured badge, for now. A badge needs an icon
  image per level and is a follow-up.
