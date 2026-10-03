---
name: the-point-card-names-the-experience
description: Point card heading • steepness • kind, turns (turnOf, kindOf, trackWord, groundWord); track scale shared with the wheel
status: current
last-reviewed: 2026-10-03
---

# The point card names the experience

## Decision

The reading for one point on a saved route is the route panel's point
header (SNOW-1064; a header of the panel, not a card of its own, since
SNOW-1068): the aspect wheel and two lines of words, in the title and
meta line's place, with the eyebrow naming the route and the point's
distance ("MONT FORT · 4.1 KM").

- **Words, not figures.** The headline is heading • steepness • kind —
  "E • Very steep • fall line", "S → NE • Gentle • turn"
  (SNOW-1069). Line two names the ground in the EAWS words — "Extremely
  steep slope". The card shows no degrees and no aspect. It names the
  heading, as the compass points the wheel's inner ring lights: one, or
  the first and last steps' joined by an arrow where the segment turns.
- **Up or down is a mark, not a word.** The kind is ascent, descent,
  traverse, fall line or turn; whether the track climbs or
  descends is the wheel's centre mark — a bar for level, and one, two or
  three chevrons up or down for gentle, moderate, and steep or very
  steep.
- **The track has its own scale.** Level under 5°, gentle under 15°,
  moderate under 25°, steep under 35°, very steep from 35°. The aspect
  wheel's inner ring is filled on the same five steps (`TRACK_STEPS`),
  so the colour and the word always name the same step. The outer ring
  keeps the EAWS slope classes, because it names the ground.
- **Fall line or traverse, nothing between.** The chord against the
  downhill direction: within 45° of either is the fall line, everything
  else a traverse. A gradient that disagrees with the chord — climbing
  within 45° of downhill — has turned inside the segment and reads as the
  plain ascent or descent. The rule applies only where an aspect exists —
  ground of 5° or more; on flat or unsampled ground the kind is the
  track's alone, ascent or descent.
- **Two headings is a turn.** A segment whose first and last steps head
  into different compass sectors — the inner ring lighting two, adjoining
  or not — is a turn, climbing, descending or level. A wobble inside one
  sector is not. Where the side the ground falls also differs between
  the two ends (`turnOf`), the turn crossed the fall line, and line two
  gives both sides — "Steep slope, falling skier's left, then right".
- **A traverse says which side the slope falls.** Line two gains the
  side the ground falls away to, relative to the skier — "Very steep
  slope, falling skier's right" (`fallSide`, the sign of the aspect's
  turn from the heading). "Skier's right" is the guidebook term, and it
  is relative, not a compass point, so the card names no aspect.
  On the fall line the ground falls ahead or behind, so no side is named.
- **The panel stays put.** The route panel is pinned top-left — under
  the region chip's row on desktop, on the top edge on a phone — and the
  profile is always showing; opening a route or placing a point changes
  only what its header says (SNOW-1068). Pinned to the bottom, the panel
  lifted the bottom controls whenever it opened; on top, under the chip
  row, nothing on the map moves. A point is placed by a tap on the
  profile's top line or on the route's line, so the words sit directly
  above the place tapped. Since 2026-10-02 there is no leg selection and
  no drag: the profile marks the point with a dot on its top line and
  the point's elevation and distance beside the cursor line, pressing
  the wheel clears the point and keeps the route, as the first Escape
  does, and the panel's × always closes the route.

## Why

The map already shows which way the line runs and which way the slope
faces, so a heading or aspect in the card repeats it in a harder form.
What the map cannot show is how steeply the reader is climbing or
descending, and whether they are on the fall line — and that is what a
skier plans around. Degrees are precise but have to be translated by the
reader every time; the words are the translation.

On the EAWS slope classes nearly every skin track is under 30°, so a
track coloured or named on them was blue and "moderate" almost
everywhere: the scale carried no information about the track. A track of
12° and one of 28° feel nothing alike on skins, so the track needed steps
of its own. 5° is the level edge because it is also where the server
stops giving ground an aspect (flat ground faces nowhere).

The point card was first a second card of its own at the top-left with
an empty state ("Select a point…"), the rail at the bottom (SNOW-1064).
That split one reading across the screen — the drag at the bottom, its
words at the top — and the empty card held space for nothing. Folding it
into the panel's header put the words beside the drag and removed the
empty state.

The first version named no heading either, on the grounds that the map
shows direction (SNOW-1064). Used on real routes it did not: the map
draws the line but not which way it runs at the cursor, and the wheel's
inner ring lights a turning segment's first and last steps separately.
Where those two sectors were not adjoining they read as two unrelated
directions, while the headline, read off the chord, said nothing about a
turn — a 90° switchback on the Col de la Chaux skin track read "Gentle
fall line climb" (SNOW-1069). Across the two Mont Fort routes about two
segments in five light two sectors; most adjoin, but one in twenty-five
leaves a gap. Naming the sectors in words makes the two lit sectors one
reading, and on a skin track, whose kick turns are 50 m apart, a 25 m
segment holds a turn often enough that the turn needs a name of its own.

The turn was first called a switchback, and given only to a turn through
the fall line in the direction of travel — uphill while climbing,
downhill while descending. That was wrong twice over: a turn into the
adjoining sector is not a switchback, it is just a turn, and
"switchback" is an uphill word, wrong on a descent. A switchback that
crossed on a side flip alone also caught a fall-line track wobbling 2°
either side of the aspect. "Turn", for any segment that lights two
sectors, is neutral in both directions and agrees with the ring.
The chevrons replaced the triangle so the centre mark carries the step
as well as the direction, and the words could drop "rising",
"descending" and "climbing" to fit one line.

A middle "diagonal" band between the fall line and a traverse was tried
in the mockup and dropped: it described a geometry, not an experience,
and every point reads more clearly as one or the other.

## Consequences

- A figure a reader wants in degrees is not on the card. The staff
  terrain table (`/_route-terrain/<uuid>/`) keeps every number.
- The aspect on the wire is one of eight sectors (SNOW-976), so the
  45° and 135° edges carry up to 22.5° of rounding. Sending a bearing
  would sharpen the edges without changing the rule.
- `segmentGradients` lives in `route_point_card_core.js`, its one reader;
  it is the same window, stopped at a leg's ends, rail two used (rail two
  was retired by SNOW-1065).
- A new surface that describes a point uses `trackWord` / `groundWord`
  and the track scale rather than inventing its own words.
- Whether a turn crosses the fall line is decided against the aspect's
  sector, so a turn that only just reaches the fall line can miss it by
  up to 22.5° and name one side rather than both.
- "Turn" does not say how far the track turned: a 45° bend into the next
  sector and a 180° kick turn both read "turn". The heading pair says the
  rest — "E → SE" against "S → NE".
