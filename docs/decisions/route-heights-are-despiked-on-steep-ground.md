---
name: route-heights-are-despiked-on-steep-ground
description: terrain_heights._despiked — median despike of model heights on ground of 40°+, SPIKE_* constants, no altimeter (SNOW-1051)
status: current
last-reviewed: 2026-10-19
---

# A route's model heights are despiked on steep ground

## Decision

`terrain_heights._stations` passes the model's boundary heights through
`_despiked` (SNOW-1051) before anything else reads them, so the profile,
the legs, ascent and descent, the point card's `segmentGradients` and the
staff `track_gradient_deg` all see the same cleaned series. A boundary
height is replaced by the median of the seven boundaries around it
(`SPIKE_HALF_WINDOW = 3`, ±75 m) when it sits more than
`SPIKE_THRESHOLD_M = 3.0` from that median **and** the ground there is at
least `SPIKE_MIN_GROUND_DEG = 40.0` (the steeper of the two segments
meeting at the boundary, from the record's `segments[].angle_deg`). It
applies only with a whole, hole-free window, so a track's first and last
three boundaries and any point beside a hole are left as recorded.
Nothing is stored; the record's `heights` stay what the sampler wrote.

## Why

On a 45–60° face a recorded position a few metres uphill of a level bench
reads several metres too high. The error is a single-point spike, not a
drift. On the Col de la Chaux road (boundaries 463–474, level at about
2,194 m) three points read 5–9 m high, which the point card turned into an
8° "climb" on 47° ground. Widening the gradient window only moves the
error onto the neighbours, so the heights are cleaned instead.

A median does it without a height comparison: inside a one-way window
(any steady climb or drop, however steep) the median is the point's own
value and nothing moves. Only a point that breaks the pattern is touched.

## Evidence

A sweep over the four canonical tours (1,606 boundaries, 40 km; 48
combinations of window, threshold and gate). The chosen setting alters 15
boundaries (0.9%), moves no leg, removes 62 m of 1,528 m ascent (4.1%) and
62 m of 5,727 m descent, and takes the ticket's worst reading from 8.0° to
under 2°. A 35° gate moves one leg boundary and flattens a real 39° dip on
Hidden Valley; 40° does neither. The exact altered boundaries are pinned
in `tests/routes/test_terrain_heights.py`.

## Consequences

- **The device altimeter plays no part.** It was used in the sweep only as
  a short-span witness to check the altered points were not real terrain.
  A rule that required it as a witness gave identical results.
- A real narrow bump or dip taller than 3 m on ground of 40° or more is
  flattened. This is the cost of the rule.
- Not addressed: false readings on ground under 40° and on real steps
  (7 of the 10 raw false readings on the corpus), and anything within
  75 m of a track's end or a model hole.
- The corpus is four tours and 15 altered points; re-sweep the three
  constants when a fifth joins.
- Related: [route-heights-come-from-the-terrain-model.md](route-heights-come-from-the-terrain-model.md),
  [the-rail-readout-is-the-tracks-angle-and-the-grounds-class.md](the-rail-readout-is-the-tracks-angle-and-the-grounds-class.md)
  (recorded the limit as unsolved).
