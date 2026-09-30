---
name: the-rail-readout-is-the-tracks-angle-and-the-grounds-class
description: rail two readout — trackGrade, slopeTerm (flat under 5°), steepShares, ROWS_FITTED, staff debug rail, readout-no-fall, no selection
status: current
last-reviewed: 2026-09-30
---

# The rail's readout is the track's angle and the ground's class

## Decision

2026-09-30, reviewed wedge by wedge on the Mont Fort – Col de la Chaux
tour's last leg. Under the cursor rail two reads one line of two facts:
**"25° descent · steep slope"**.

- **The track's own angle, measured.** `trackGrade` over
  `segmentGradients`: the height change 25 m either side of the segment's
  midpoint, read off the heights rail one's profile draws. Ascent, descent,
  or "level" where it rounds to 0°.
- **The ground's class, in the avalanche services' words.** `slopeTerm`
  on the EAWS glossary's slope-gradient classes: moderate under 30°, steep
  from 30°, very steep from 35°, extremely steep from 40°. Snowdesk adds
  one class below them: **flat, under 5°** (`FLAT_GROUND_DEG`).
- **No track word, no bank, no slope angle.** "Traverse", "Fall-line",
  "Skin" and "Bootpack" are gone from the readout, and the labelled
  stretch blocks are gone from the fitted track row (`segmentWord` and
  `stretches` are deleted). Fitted, the row is empty and the lane is
  18 px (`ROWS_FITTED`); the wedges bring it back to 44 px.
- **The gradient window stops at the leg's ends**, in the readout's 50 m
  window and in the staff terrain table's 125 m one (`track_gradient_from`
  / `track_gradient_to` name the segments each row was summed over).
- **The text does not move.** It is left-aligned at the lane's left edge;
  the anchor under the cursor and its stem are gone.
- **The card:** the title carries the leg's vertical and its length
  ("Leg 7 — descend 1,324 m over 9.1 km"); the subtitle names the very
  steep and extremely steep ground the leg crosses and gives no figure
  (`steepShares`), with no line at all for a leg with neither.
- **A staff debug rail** under rail two shows every figure behind the
  wedge under the cursor, read from `/_route-terrain/<uuid>/?format=json`.

## Why

- **Measured, not derived.** `tan²(pitch) + tan²(bank) = tan²(slope)`
  gives a track angle from the slope and the bank alone, and it always
  agrees with the wedge. It disagreed with the heights by more than 5° on
  25 of the leg's 364 segments, and the profile is drawn from the heights.
  The rule set for the readout was that the text, the profile and the map
  agree, so the text reads what the profile draws.
- **The authorities' classes, not ours.** The bands already broke at 30°,
  35° and 40°; the track row's "Gentle under 25°" matched nothing. EAWS
  has one class under 30°, and the track's own angle says how the travel
  is there.
- **Flat at 5°, not 10°.** At 10°, over half the flat segments on the two
  recorded tours sat beside a track descending 5° or more ("9° descent ·
  flat"); at 5°, four on each tour do.
- **A leg turns on a summit or a low point**, so a window reaching past
  it averages a descent with the climb behind it: the first segment down
  from the col drops 5.2 m in 25 m, about 12°, and read 7°.
- **The words repeated the screen.** Across the ten legs of the two
  recorded tours every climb was one "Skin" block and every descent
  alternated "Moderate" and "Traverse": the leg title and the band strip
  already said both. No steep stretch long enough to label was anything
  but a traverse.
- **Percentages and metres of steep ground read as precision the data
  does not have**; naming the class is the claim the record supports.

## Consequences

- **One wedge is enough to name a class** in the subtitle. A leg whose
  only very steep ground is one 25 m segment reads "Crosses very steep
  terrain".
- **The lane changes height at the wedges' threshold**, 18 px to 44 px,
  and the card with it; `onResize` tells the map.
- **Fitted, the lane is an 18 px touch target.** A tap puts the cursor
  on the segment under it (SNOW-1052; until then it picked the steepest
  band within 22 px sideways).
- **A bench cut across a steep face still reads wrong.** A road traverse
  on the Col de la Chaux leg sits at about 2,194 m for 250 m, and three
  boundary heights read 5–9 m high where the recorded position is off the
  bench; the readout gives "8° ascent · extremely steep slope" for level
  track. A wider window moves the error onto the neighbours. Not solved.
- **The EAWS classes are defined on a 1:25,000 map, at the steepest part
  of a slope.** The record is a 10 m window every 25 m, so it names local
  features a map-scale reading would not.
- **The band strip and the legend keep their degree ranges.** The
  selection text ("50 m 40–45°") that also did went with the selection
  in SNOW-1052 (below).
- The earlier records of this row and readout are in
  [the-bank-angle-is-drawn-signed.md](the-bank-angle-is-drawn-signed.md);
  its SNOW-1044 section's words, stretches and readout are superseded
  here. The wedges, the sign and the kick turns stand.

## SNOW-1052: the selection readout is gone

2026-09-30. Rail two no longer selects a band or a passage, and the
readout no longer reads a selection. Every gesture on the lane — tap,
drag, drag release, mouse hover, arrow keys — only moves the cursor, and
a tap leaves it at the tapped segment after the lift. The readout is
always the point line above, or the hint "Drag or tap to read a point."
with no cursor. Inside a no-fall passage the line ends
**"· no-fall passage"** (`readout-no-fall`), because the passage bar is
4 px tall and the point line says nothing about it.

- **Why.** The selection readout ("25 m 45–50°") said what the band's
  colour already shows. The pick jumped sideways — the steepest band
  within the tap radius won, not the one under the finger — it fired on
  every drag release, and it left the readout on the selection while the
  cursor moved on, so the line under the lane described a place the
  cursor had left.
- **What went.** The cursor's `select` / `clearSelection` and its
  `selection` state (`route_cursor_core.js`); `selectionBox` and
  `steepestBand` (`route_rail_two_core.js`); the outline, the veil
  either side of it and `data-selected` on rail two; Enter and Space;
  the map's highlighted stretch (`selectionLine`, the
  `route-cursor-selection` source and its two layers). `nearestRange`
  stays: the leg picker uses it.
- **What stands.** Pinch, the −/+ zoom, the double-tap zoom, the leg
  picker, the passage bars, and `aria-valuetext`'s bank side and "Kick
  turn" after the line.
