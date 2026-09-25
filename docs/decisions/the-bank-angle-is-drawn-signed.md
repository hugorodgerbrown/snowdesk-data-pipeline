---
name: the-bank-angle-is-drawn-signed
description: bank.py, `banks` on the wire, bankWedge level-ski wedges, bankGlyphs max-|roll| grouping, the zoom placeholder — kept signed, sent flat
status: current
last-reviewed: 2026-09-25
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
  absolute value, and the drawing (`static/js/bank_ribbon_core.js`) puts
  each level-ski wedge's pale half on the downhill side (SNOW-1031; until
  then it leaned each tick's top toward the downhill shoulder).
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

## Superseded drawing (SNOW-1031)

The leaning ticks described above were replaced by **level-ski wedges**
after the 25 Sep design review: a tick's lean had to be decoded, and a
30° tick read much like a 60° one. The data, the sign and every reason
above for keeping the sign stand; only the mark changed.

- **The glyph.** `bankWedges` in `static/js/bank_ribbon_core.js` draws
  one 14 px glyph every 15 px (`GLYPH_PITCH`, `GLYPH_HALF_WIDTH`). The
  level line through its centre is the skis; the ground line runs through
  the same pivot from the uphill end `(−7, −dy)` to the downhill end
  `(+7, +dy)`, mirrored by the sign, with
  `dy = min(13, 7 × 1.5 × tan|roll|)` (`EXAGGERATION`, `CAP_PX`).
- **The side is the fill.** The uphill triangle is filled solid
  (`--color-text-2`) and the downhill one at 30% of the same token, so the
  pale half is the side the ground falls away to. A positive roll puts it
  on the right. The ground line is `--color-text-1`.
- **The fall line is a flat line, not a gap.** Under `MIN_FILL_PX`
  (0.6 px, about 3°) the fills are dropped and only the ground line is
  drawn. A null bank still draws nothing.
- **The ×1.5 exaggeration** lets a 10° bank read at the glyph's width;
  the 13 px cap (reached at about 51°) keeps the row at 26 px, and
  rail two keeps its passage bars in their own 4 px row under the bank
  row, so a capped wedge never covers one.
- **The `strong` threshold is gone.** The wedge's size carries the
  magnitude, so no second ink is needed.
- **The pitch widened from 8 px to 15 px**, so the "flat array" argument
  above now reads a value about every fourth segment on a zoomed leg
  rather than every other; the cost comparison is unchanged. Rail two no
  longer lays glyphs at a fixed pitch — see the next section; `bankWedges`
  keeps the fixed-pitch layout for any other caller.

## The bank row follows the zoom (SNOW-1031 revision)

The Leg 7 review revised how rail two lays the glyphs out. Rail two now
always opens **fitted** — the whole leg, however long — so at that scale
it is an overview, and zooming is how it is read in detail. The rail is
never widened to make something tappable, and the bank is never averaged
to make it drawable.

- **Glyphs group whole segments.** `bankGlyphs` in
  `static/js/route_rail_two_core.js` gives each glyph N consecutive
  segments, `N = ceil(10 px / segment width)` (`glyphGroup`), aligned to
  the leg's start so a group never splits a segment and never shifts as
  the view pans. Each glyph's geometry comes from `bankWedge`, the one-glyph
  function `bank_ribbon_core.js` exports, at half-width
  `min(7, group width / 2 − 0.5)`.
- **The maximum |roll|, never the mean.** A glyph draws the segment with
  the largest |roll| in its group, with that segment's side. A zig-zag of
  +20°, −35°, +20° averages to about level, which would draw a switchback
  on a steep face as a track on the fall line — the opposite of what the
  ribbon exists to show. The largest roll is the one a reader has to plan
  for, and its sign is a real segment's sign. A group whose banks are all
  unknown draws nothing.
- **The placeholder.** Past `MAX_GROUP` (N > 3, a glyph summarising more
  than 75 m) the row draws no glyphs at all: a dashed centre line and the
  plain-text label "Zoom in to see the bank" stand in. It is not a button;
  pinch, wheel and the −/+ buttons zoom, and a double-tap goes straight to
  `resolveSpan`, the widest span that draws. The scale is constant across
  the lane, so the row is all drawn or all placeholder. On a 390 px lane
  Leg 7 fitted is 0.9 px a segment (N = 12, placeholder); at ×4 it is
  3.7 px (N = 3, drawn); legs up to about 1.2 km draw fitted.
- **Passages keep their own mark.** A 4 px bar under the bank row,
  spanning the passage's real extent but never under 6 px wide
  (`passageBox`), drawn at every zoom and never folded into the band or
  the bank row.
- **The readout stays exact.** "37° slope · 36° bank" reads the segment
  under the cursor at every zoom; only the drawing groups.
