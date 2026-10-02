---
name: the-point-card-names-the-experience
description: Point card words not figures (trackWord, groundWord, headline); the track scale shared with the wheel (TRACK_STEPS)
status: current
last-reviewed: 2026-10-02
---

# The point card names the experience

## Decision

The reading for one point on a saved route is the route panel's point
header (SNOW-1064; a header of the panel, not a card of its own, since
SNOW-1068): the aspect wheel and two lines of words, in the title and
meta line's place, with the eyebrow naming the route and the point's
distance ("MONT FORT · 4.1 KM").

- **Words, not figures.** The headline is the track's steepness plus how
  it crosses the slope — "Very steep fall line descent", "Gentle rising
  traverse". Line two names the ground in the EAWS words — "Extremely
  steep slope". The card shows no degrees, no heading and no aspect.
- **The track has its own scale.** Level under 5°, gentle under 15°,
  moderate under 25°, steep under 35°, very steep from 35°. The aspect
  wheel's inner ring is filled on the same five steps (`TRACK_STEPS`),
  so the colour and the word always name the same step. The outer ring
  keeps the EAWS slope classes, because it names the ground.
- **Fall line or traverse, nothing between.** The heading against the
  downhill direction: within 45° is the fall line (descent or climb),
  everything else a traverse whose direction is the gradient's. A
  gradient that disagrees with the heading reads "…, turning". The rule
  applies only where an aspect exists — ground of 5° or more; on flat or
  unsampled ground the headline is the track alone.
- **A traverse says which side the slope falls.** Line two gains the
  side the ground falls away to, relative to the skier — "Very steep
  slope, falling skier's right" (`fallSide`, the sign of the aspect's
  turn from the heading). "Skier's right" is the guidebook term, and it
  is relative, not a compass point, so the card still names no aspect.
  On the fall line the ground falls ahead or behind, so no side is named.
- **The panel stays put.** The route panel is pinned top-left — under
  the region chip's row on desktop, on the top edge on a phone — and the
  profile is always showing; opening a route, pressing a leg or placing
  a point changes only what its header says (SNOW-1068). Pinned to the
  bottom, the panel lifted the bottom controls whenever it opened; on
  top, under the chip row, nothing on the map moves. A point is placed by
  dragging along the profile, so the words sit directly above the finger.
  The panel's × clears a placed point first, as the first Escape does,
  and closes the route only once no point is placed.

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
