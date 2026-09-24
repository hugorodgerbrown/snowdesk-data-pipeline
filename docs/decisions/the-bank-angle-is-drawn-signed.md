---
name: the-bank-angle-is-drawn-signed
description: bank.py, bank_angles, roll_deg, `banks` on the wire, bankTicks — the signed roll across a route track, kept signed and sent flat
status: current
last-reviewed: 2026-09-24
---

# The bank angle is drawn signed

## Decision

A saved route's slope record yields a **bank angle** per segment — the
roll, how far the ground tilts across the track:
`tan(roll) = tan(slope) × sin(aspect − bearing)`. Its companion,
`tan(pitch) = tan(slope) × cos(aspect − bearing)`, is the along-track
gradient the profile already draws, and `tan²(pitch) + tan²(roll) =
tan²(slope)`.

- **Signed.** Positive where the ground falls away on the skier's right,
  negative on the left. `apps/routes/services/bank.py` never takes the
  absolute value, and the drawing (`static/js/bank_ribbon_core.js`) leans
  each tick's top toward the downhill shoulder.
- **Derived at read time**, like `fall_line.py` and `passages.py`; nothing
  is stored. `roll_deg` is a column of the staff terrain table
  (`terrain_detail`), and `compact_slope` sends `banks`.
- **No gate.** Every known segment carries a roll, whatever its angle.
- **`banks` is a flat per-segment list**, aligned with `angles`, one whole
  degree each, null for an unknown segment.

## Why

### The sign was argued both ways

The case for magnitude only: a signed tick asks the reader to hold a
convention — which shoulder does a right lean mean? — and a reader who
half-remembers it reads the wrong shoulder. The argument pointed to the
fall-line arrows, which failed in that way: a mark whose meaning rests on
a remembered convention gets read backwards by the reader who most needs
it.

What defeated it is the difference between a **lookup** and a
**pattern**. The fall-line arrow is a lookup: one mark, one bearing, and
the reader must decode that mark correctly to learn anything. The bank
ribbon is read as a pattern across many ticks: a run leaning one way is a
sustained traverse, an alternation is a switchback sequence, and a change
of side is a turn. None of that needs the convention; it needs only that
left and right are different, which a magnitude cannot show. Dropping the
sign would delete the one thing the ribbon says that the colour and the
profile do not.

### The measurement: the flips a reader sees are few

On the reference track, the median sign change happens at a lean of
3–6° — the tick is near vertical on both sides of it, so the flip is not
visible as a flip. Counting only changes of side at a lean of 15° or more,
the per-leg counts drop from 17 to 3, 12 to 4, 13 to 8 and 101 to 10.
The visible flips are the real turns; the rest is the track wobbling
across the fall line, which draws as near-vertical ticks and reads as
"straight down", which it is.

### Gentle ground limits itself, so there is no gate

`|sin δ| ≤ 1`, so `|roll| ≤ slope` at every heading: a 20° face can
never bank a track by more than 20°. The fall-line arrow needs its 30°
gate because an aspect on near-level ground is a confident bearing off
noise; a roll on gentle ground is a small number and draws as a
near-upright tick. There is nothing to withhold.

### A flat array, not thinned marks

The fall-line arrows are thinned to one per 250 m because an arrow is a
lookup and 600 of them are a texture. The ribbon is the opposite: it
places a tick every 8 px across a zoomed leg, which at desktop width is
about one tick per 50 m of track — two strides. Thinned `{"i": 123,
"deg": -23}` marks at that spacing would cost about 20 bytes each and
still be needed for about every other segment; a bare whole degree costs
about 4. On a 15 km tour (~600 segments) the flat array is about 2.4 kB
against about 6 kB of marks, and it is exact at any zoom.

This is the shape
[a-slope-segment-is-the-shared-record](a-slope-segment-is-the-shared-record.md)
rejects for the aspect. The difference is the drawing: a per-segment
aspect had no drawing that could use it, while the ribbon reads a value
about every other segment. Whole degrees keep the cost to about 2.4 kB,
beside the 13 kB the boundary coordinates already take.

## Consequences

- **No `abs()` anywhere on the path.** `bank_angle_deg`'s docstring and
  `tests/routes/test_bank.py`'s sign tests name SNOW-1021, so a tidy-up
  that takes the magnitude reads as deleting a feature and fails the
  build.
- **No gate constant**, and no test holding one equal to
  `STEEP_THRESHOLD_DEG`.
- **A null bank is a gap in the ribbon, never a vertical tick** — an
  upright tick would claim the ground is level across the track where
  nothing is known.
- **The mount is deferred to SNOW-1019.** This decision ships the
  derivation, the wire field and the pure drawing core (`bankTicks`);
  rail two, its legend line and the tokens that paint `strong` ticks
  arrive with the rail. The core takes the caller's own x → sample-index
  conversion, the rule `route_cursor_core.js` set, so it does not depend
  on how that rail lays out its axis.
- **About 2 kB more per sampled route** in the offline-cached routes feed.
