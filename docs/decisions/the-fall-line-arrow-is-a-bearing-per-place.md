---
name: the-fall-line-arrow-is-a-bearing-per-place
description: fall_line.py, fall_line_marks, fall_lines on the wire, routes-fall-lines — the downhill arrow on a route, steep ground only
status: current
last-reviewed: 2026-09-15
---

# The fall-line arrow is a bearing per place, not an aspect per segment

## Decision

A saved route carries small arrows pointing the way the ground under it
**falls** — the terrain's aspect, which is the direction of steepest
descent. Four constraints:

- **A bearing per PLACE.** The wire carries `fall_lines`: a list of
  `{"i": 12, "deg": 112}` — a segment index into the `angles` array the
  client already holds, and a whole compass degree. Never the
  per-segment aspect the record stores.
- **Derived at read time**, in `apps/routes/services/fall_line.py`, with
  both thresholds as keyword arguments. Nothing new is stored.
- **Steep ground only.** `FALL_LINE_GATE_DEG` is 30°, the number
  `STEEP_THRESHOLD_DEG` already is. Below it no arrow is drawn at all.
- **One per steep run, then one every `FALL_LINE_SPACING_M`** (250 m),
  with the spacing reset at every gap. The client thins no further; it
  places what it is given, and MapLibre's collision engine drops what
  will not fit.

## Why

### The colour cannot say which way a face runs

SNOW-910 colours the ground under the track, SNOW-911 rings the passages
where the ground around it can release, SNOW-964 splits the line where
the track is on no-fall ground. A traverse across a 40° face and a
descent of the same face get identical treatment on all three, and on the
ground they are not the same day out. SNOW-964 does name the
relationship — "down the fall line", "across it" — but only in a popup,
only in words, and only above 50°.

### Per-segment aspect is the payload the record was designed to avoid

[a-slope-segment-is-the-shared-record](a-slope-segment-is-the-shared-record.md)
rejected sending the aspect: 600 segments on a 15 km tour, roughly
doubling a payload the offline cache holds. That reasoning is intact, and
the marks respect it — about 60 marks and a kilobyte on the same tour,
against the 13 kB its boundary coordinates already cost.

The drawing argues the same way. Six hundred arrows is a texture, not a
direction; the reader of a textured line learns nothing and stops
looking. Spacing them is not a payload optimisation that happens to look
better, it is the only legible form of the mark — which is why the
spacing lives on the server beside the gate rather than in a client that
would have to be trusted to thin consistently on two surfaces.

### Read time, because a stored mark list would freeze both constants

`backfill_route_slope_samples` selects on a missing key, so a stored
`fall_lines` would make re-tuning either threshold a full re-walk of the
tile origin — the cost `passages` was designed around. Both gates are
keyword arguments, which is the mechanical proof that a sweep costs one
pass over rows already in memory.

### Nothing under 30°, and the surfaces have to say so

An aspect sampled on near-level ground is noise: a 5 m grid gives a 2°
valley floor a confident bearing off a stream bank or a road cutting.
Drawing it would be a claim with no content, and it would spend the
reader's attention on the arrows that do not matter.

The cost is a real hazard, because **an absent arrow is readable as flat
ground**. Three surfaces carry the correction: the legend row says "steep
ground only", `/help/#help-topic-slope` says where there is no arrow
there is no claim, and the coloured line underneath never goes quiet —
whatever the arrows say or fail to say, the band colour is still there.

### The arrow may be dropped; the ring may not

`routes-fall-lines` is the only route mark with `icon-allow-overlap:
false`. A crux ring dropped by the collision engine understates the day,
which is why those ignore collision entirely. An arrow dropped at z12
costs nothing: the survivors say the same thing about the same face. That
asymmetry is what lets the server keep one spacing rule for every zoom.

## Consequences

- **A trip carries the marks too** (`trip_map.js`), from the snapshot's
  own record. The trip page is what the group sees — the people who did
  not plan the route and have never looked at the ground.
- **A snapshot or record written before this simply draws no arrows.**
  There is no backfill and none is possible to need: the marks are
  derived from `aspect_deg`, which every record has carried since
  SNOW-910.
- **The mark is not in the tap path.** Every arrow sits on the middle of
  a segment `routes-slope-line` still draws, well inside the 8 px
  tolerance, so a tap on an arrow already opens its route. Adding the
  layer to `MARKER_EXCLUSION_LAYERS` would maintain a second path to the
  same popup — and one that comes and goes with the zoom, since this is
  the mark the collision engine may drop.
- **Nothing was added to the popup.** A route has no single aspect, so
  "faces NE" would be a summary of a distribution; the arrows are
  per-place and the popup is per-route. `passages` already carries the
  one sentence about direction a whole route can honestly support.
- **`slope_summary.segment_lengths_m` became public** to space the
  marks — it was private to `passages`, and a second copy of the
  stride-not-chord rule is the copy that goes wrong.
- **No tuning command.** `report_route_passages` sweeps its own four
  gates because "how much of the estate would this mark?" was a real
  distribution question at 50°. Here the gate is fixed by consistency
  with the rest of the product and the spacing by what a reader can take
  in, so a sweep would answer a question nobody is asking. The keyword
  arguments are in place for the day one is.

## Alternatives rejected

**Send a flat `aspects` array beside `angles`.** The doubled payload the
shared-record decision rejects, and a drawing nobody can read. Both, at
once.

**Draw an arrow on every segment, thinning client-side.** Two surfaces
would each need the thinning rule, and two implementations of it would
eventually disagree about how many passages a face has — the reasoning
`cruxes` already follows in refusing to re-group client-side.

**Scale the arrow, or fade it, by steepness.** The line under it already
carries the angle, in a palette the legend explains and the raster
shares. A second encoding of the same number would compete with it, and
a half-size arrow on 31° ground reads as a less certain direction rather
than a gentler slope.

**Offset the arrow to the side of the track.** `icon-offset` rotates with
the icon under `icon-rotation-alignment: 'map'`, so the offset would run
along the fall line rather than across the track — and an arrow beside
the line stops being anchored to the ground it describes.

**A gate of its own, tuned independently of `STEEP_THRESHOLD_DEG`.** It
would make the map argue with itself: an arrow on ground the colour scale
calls gentle, or a bare stretch just above the threshold the popup
quotes. They are two product decisions and are held equal by a test
(`tests/routes/test_fall_line.py`) rather than by an import, so a
deliberate divergence is one edited assertion with a reason beside it.
