---
name: a-leg-transition-sits-on-the-extremum
description: detect_legs, _snap_to_extrema, SMOOTHING_WINDOW_M — a leg transition is moved from the smoothed turning point onto the raw high or low point
status: current
last-reviewed: 2026-10-01
---

# A leg transition sits on the high or low point, not beside it

## Decision

`detect_legs` (`apps/routes/services/legs.py`) finds a route's
transitions as the turning points of elevation smoothed over 100 m either
side (`SMOOTHING_WINDOW_M`). Since 2026-09-30 each transition is then
**snapped** onto the highest raw elevation within that window where a
climb ends, and the lowest where a descent ends (`_snap_to_extrema`). A
snap never crosses a neighbouring transition, so every leg keeps at least
one segment, and on a tie it takes the point nearest the smoothed one.

## Why

The smoothed series says **which** turns are real. It does not say where
they are: a 100 m average turns before or after the ground does. Measured
on the four canonical tracks on terrain-model heights, all twelve
transitions sat 9 to 50 m along the track from the true summit or low
point, and up to 13 m of height off it.

The error showed once the rail read each segment's own angle. The last
leg of the Mont Fort – Col de la Chaux tour, a descent, was cut 42 m and
9 m of height short of the col, so its first two segments read "13°
climb" and "5° climb" under the title "descend".

The window is the smoothing's own half-width because the point that
turned the average is inside it.

## Consequences

- **A climb ends on its high point and a descent on its low point**, on
  every surface that reads legs: the rail's leg fills, the map's leg
  lines and numbered transitions, the leg picker, and the gradient
  windows that stop at a leg's ends.
- **Leg figures move by the distance snapped.** The Backside climb's
  ascent reads 274.6 m, where the cut 39 m short of the top read 260.4 m.
  Route totals do not change: they are summed over the whole track.
- **A snap can only lengthen a leg's excursion**, so the 15 m merge
  threshold (`MIN_LEG_ASCENT_M`) is not re-run after it.
- **Rises inside a leg remain.** A descent can still hold short climbs
  that were never a transition; on the Col de la Chaux descent 25 of 362
  segments read as a climb, most by 1–3°.
- `tests/routes/test_legs.py` holds the rule on every canonical track:
  within the window no point is higher than a top or lower than a bottom.
