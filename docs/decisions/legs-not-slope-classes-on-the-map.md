---
name: legs-not-slope-classes-on-the-map
description: route_legs_core.js, routes-leg-climb/-descent, routes-slope-line, ROUTE_SLOPE_MINZOOM — a route is its legs, in slope classes from z14
status: current
last-reviewed: 2026-10-02
---

# A saved route on the map is drawn as its legs, not in slope classes

## Decision

SNOW-1017. On the home map an owned route is one line per leg
(`detect_legs`): a climb dashed, a descent solid, in the rail's two
colours (`--color-route-rail-climb`, `--color-route-rail-descent`) —
since SNOW-1019 a slate climb and a fuchsia descent, swapped to match the
rail mockup, with the dash still marking the climb. A
numbered marker sits at each transition from z11, legs − 1 of them.
Opening a leg on the rail dims every other leg on the map. The six
slope-class colours SNOW-910 painted per 25 m segment left the map; the
no-fall passages, crux rings and fall-line arrows stayed, with the
passage edge now in its leg's colour. A pending share is unchanged: the
teal dashed line, with no legs and no markers. The trip page
(`trip_map.js`) still draws the slope-coloured line.

**2026-09-24 (SNOW-1019).** The crux rings and fall-line arrows are off
the map now, and off the trip map too, with their legend rows. The crux
marks are deferred to a later ticket; the server's crux probe
(`apps/routes/services/cruxes.py`) and the `cruxes` key on the slope
record are unchanged. The bank ribbon on rail two replaced the arrows
(rail two was retired by SNOW-1065). The no-fall passages remain the one terrain mark on the line.

**2026-10-02 (SNOW-1066).** The deferred crux is not coming back.
Snowdesk keeps no route-level crux: `apps/routes/services/cruxes.py` is
deleted, sampling no longer writes `cruxes` or a per-segment `crux` flag,
the slope wire no longer sends `cruxes`, and the detail sheet's
key-passage count is gone. Where the dangerous ground is will be the
daily avalanche terrain layer's job (SNOW-979). Old records keep their
keys, unread.

**2026-09-24, later (SNOW-1019).** The passage split line is off both
maps too, with its legend row. The line on the map is now its legs, the
numbered transitions and the start and end markers, and nothing else.
The passages were shown on rail two only, as bars under the bank ribbon
(retired by SNOW-1065, so no surface draws them now);
`passages` still travels on the slope record and the detail sheet still
names them.

**2026-09-28 (SNOW-1046).** One terrain mark returns to the line: a
steep-ground shadow. Wherever the ground under the route is 40° or
steeper, a second, darker line (`routes-steep-shadow`, source
`routes-steep`, built by `steepShadowCollection` in
`route_legs_core.js`) runs 4.5 px to the downhill side of it. A run is
consecutive 25 m segments at 40° or more on one side; it ends where the
bank changes sign, so a shadow never crosses the line, where the bank is
unknown, and at a leg boundary, so the shadow dims with its leg. A single
segment is a run. The side is the sign of `banks`, and the layer's
`line-offset` is that sign times the pixel distance, so the shadow sits
downhill at every zoom. It is not tappable and has no legend row.

It returns where the crux rings, arrows and passage line did not because
it meets the rules above that they failed. It is one statement per
stretch, not per 25 m segment. It is drawn on steep ground only, so a
route with none carries no extra line. And it says where the exposure is
and which way it drops, which nothing else on the map does at the zoom a
route is framed at: the legs say up or down, the rail's bank ribbon (retired
by SNOW-1065) said which way the ground tilts only once a reader opened it.

**2026-09-30.** The steep-ground shadow is off the map, with its
source, layer and `steepRuns` / `steepShadowCollection`. At the zooms a
route is read at, the offset line sat under the leg casing's edge and
read as a thicker casing rather than as steep ground. Two replacements
were tried beside the line and dropped: a comb of hachures, and filled
wedges pointing down the slope (the Böschung / slopes mark of the
swisstopo and Ordnance Survey legends). Neither read at route scale.

In their place the slope classes return to the line itself, from z14
(`ROUTE_SLOPE_MINZOOM` in `map.js`). Below z14 a route is its legs, as
above. From z14 the leg lines stop and `routes-slope-line` /
`routes-slope-unknown` (source `routes-slopes`, built by
`slopeSegmentCollection` in `route_legs_core.js`) paint the route's core
per 25 m segment in the six classes, in the slope raster's palette,
with an unknown segment in grey. The leg casing and the numbered
transitions stay at every zoom, so the legs remain countable; each
segment carries its leg's `i`, so opening a leg on the rail dims the
others' segments as it dims their lines. The climb dash does not carry
over: a dash restarts on every segment.

Each segment follows the route's own coordinates, not the straight 25 m
chord between its two boundaries (SNOW-1053). The casing under it is
drawn from those coordinates, so at z14 and beyond a chord left the
casing on every bend — by up to 9.9 m on the Hidden Valley canonical
track — and the coloured core no longer sat on the line it coloured.
The record carries `slope.seams`, one index per boundary into the
feature's geometry, and `segmentPaths` in `route_slope_core.js` draws
each segment as its two boundary points with the coordinates between
them. The cursor's dot sits half way along that path. A payload without
`seams` — cached before the key, or one the server could not place on
its geometry — still draws chords.

The density argument below is about the zoom a route is FRAMED at,
where the question is which part is the climb. z14 is past that: the
reader has zoomed in on a stretch, and the question there is how steep
it is. The legs answer the first zoom, the classes the second.

**2026-10-02 (SNOW-1065).** Rail two is deleted and there is one rail.
Pressing a leg dims the others on the map and profile; the cursor holds a
leg or a point, never both, and a tap on the open route's line only places
a point (it no longer opens the leg there). The map's slope classes from
z14 are now the only place a class is drawn along the route.

**2026-10-02, later the same day: no leg selection.** Hugo removed it to
simplify the route panel: nothing selects a leg, so nothing on the map is
ever dimmed, and `dimOpacity` is gone from `route_legs_core.js`. The legs
stay as the drawing this decision chose, on the map and in the profile's
fills; a tap on the line or the profile places a point, and the point is
the one thing the cursor holds. The "selection dims the others" bullet
below and the dimming consequence are historical.

## Why

- **Density.** A track changes slope class every few segments, so a
  route in six colours reads as texture. The reader had to zoom in to
  learn anything from it, and at the zoom where they had the question
  (which part of this is the climb?) it had no answer. The legs answer
  that at the zoom a route is first framed at.
- **The fall-line precedent.** The arrows are drawn per place and never
  per segment, because a mark every 25 m is noise
  ([the-fall-line-arrow-is-a-bearing-per-place.md](the-fall-line-arrow-is-a-bearing-per-place.md)).
  The line follows the same rule: one statement per stretch.
- **The classes move to the rail, not away.** The rail is where the
  steepness of one leg can be read against distance. SNOW-1019 put them
  on rail two's band strip (retired by SNOW-1065), closing the gap accepted on 2026-09-24 in
  which neither the map nor the rail carried a slope class; since
  2026-09-30 the map carries them again from z14 (above). The passages,
  cruxes and arrows said where the steep ground was until SNOW-1019 took
  them off the line (and SNOW-1066 removed cruxes altogether).
- **A selection dimmed the others instead of darkening the chosen leg**
  (until leg selection was removed, 2026-10-02).
  The chosen leg keeps the colour and weight the rail uses for it, so the
  two surfaces still read as one drawing, and the rest of the route stays
  on screen as context rather than disappearing. A darker or thicker leg
  would introduce a third line style the key does not explain.
- **`point_from` / `point_to` are on the wire.** The legs' `from` / `to`
  index the slope record's 25 m segments, which is what the cursor and
  the rails are keyed on. An unsampled route has no slope record and
  still has legs, so the map cannot slice its lines from the slope
  points. The point indices slice the route's own coordinates, which
  every route has and which the flat line has always drawn. Adjacent legs
  share their seam point, so the lines meet with no gap; a leg folded
  away server-side hands its points to its neighbour.

## Consequences

- `routes-line` and `routes-line-casing` exclude any owned route that
  carries `legs` while `route_legs_core.js` is loaded. The legs carry
  their own casing, so a dimmed leg is not framed at full strength.
- An overlay payload cached before SNOW-1017 carries `legs` without
  point indices. The `routes` source is handed a copy with `legs`
  removed from any route whose legs cannot all be sliced
  (`withDrawableLegs`), so such a route falls back to the flat line. The
  cached payload itself is untouched, because the rail reads its legs
  from it.
- The legend's route key has the two leg rows. The steepness bands and
  "Not surveyed" rows are gone, and SNOW-1019 took out the passage,
  fall-line and crux rows with the marks.
- HISTORICAL: the dimming followed `window.pwaRouteRail.cursor()`,
  through the one subscription `bindRouteCursor` in `map.js` holds. That
  subscription now draws only the cursor's dot, and the rail's `close()`
  clears the point before it drops the cursor, which is how the map
  hears the rail's ×, Escape and backdrop closes.
