---
name: legs-not-slope-classes-on-the-map
description: route_legs_core.js, routes-leg-climb/-descent, routes-transitions, point_from/point_to — a route on the map is its legs
status: current
last-reviewed: 2026-09-24
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
record are unchanged. The bank ribbon on rail two replaced the arrows.
The no-fall passages remain the one terrain mark on the line.

**2026-09-24, later (SNOW-1019).** The passage split line is off both
maps too, with its legend row. The line on the map is now its legs, the
numbered transitions and the start and end markers, and nothing else.
The passages are shown on rail two only, as bars under the bank ribbon;
`passages` still travels on the slope record and the detail sheet still
names them.

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
  steepness of one leg can be read against distance. SNOW-1019 puts them
  on the leg rail. Until it lands the map and the rail carry no slope
  classes at all; that gap was accepted on 2026-09-24. The passages,
  cruxes and arrows still say where the steep ground is.
- **A selection dims the others instead of darkening the chosen leg.**
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
- The dimming follows `window.pwaRouteRail.cursor()`. The rail's
  `close()` closes the open leg before it drops the cursor, which is how
  the map hears the rail's ×, Escape and backdrop closes.
