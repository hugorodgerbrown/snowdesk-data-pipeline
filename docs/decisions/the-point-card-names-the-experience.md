---
name: the-point-card-names-the-experience
description: Point card Track and Slope lines; no turn or switchback; a turn's side from its ends (turnOf, kindOf, trackWord, groundWord)
status: current
last-reviewed: 2026-10-17
---

# The point card names the experience

## Decision

The reading for one point on a saved route is the route panel's point
header (SNOW-1064; a header of the panel, not a card of its own, since
SNOW-1068): the aspect wheel and two lines of words, in the title and
meta line's place, with the eyebrow naming the route and the point's
distance ("MONT FORT · 4.1 KM").

- **Two labelled lines: Track and Slope (SNOW-1094).** Each line
  carries a label, because the two describe different things — the
  track's heading and step, the ground's angle and fall side — and
  nothing else on the card said which was which:

  ```
  TRACK  NW → W · Gentle descent
  SLOPE  Moderate, falling skier's left
  ```

  The wheel's accessible name reads the labels too: "Track: NW to W,
  Gentle descent; Slope: Moderate, falling skier's left".
- **Words, not figures.** The Track line is the heading, then the
  steepness and kind as one phrase — "E · Very steep fall line",
  "S → NE · Gentle ascent". The Slope line names the ground in the EAWS
  words — "Extremely steep"; the label carries the word "slope". The
  card shows no degrees and no aspect. It names the heading, as the
  compass points the wheel's inner ring lights: one, or the first and
  last steps' joined by an arrow where the segment turns.
- **Up or down is a mark, not a word.** The kind is ascent, descent,
  traverse or fall line; whether the track climbs or descends is the
  wheel's centre mark — a bar for level, and one, two or three chevrons
  up or down for gentle, moderate, and steep or very steep. (Ascent and
  descent are words where the chord says nothing else: a turning
  segment, or one crossing no slope.)
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
- **Two headings: fall line, ascent or descent (SNOW-1094).** A segment
  whose first and last steps head into different compass sectors — the
  inner ring lighting two, adjoining or not — turned. A wobble inside one
  sector did not. Descending, where either end heads in the ground's own
  sector, it is the fall line, turning or not, and the Slope line names
  no side. Every other turning segment is an ascent or a descent by its
  gradient's sign; a level one has no kind, and its Track line reads
  "Level" alone, while its Slope line still names the side.
  There is no "turn" or "switchback" kind.
- **A turning segment's side comes from its two ends.** The Slope line
  reads the side the ground falls at the first step and the last
  (`turnOf`): one side where they agree — "Moderate, falling skier's
  left" — and both where they differ — "Steep, falling skier's left,
  then right", the track having crossed the fall line. Never from the
  chord.
- **A traverse says which side the slope falls.** The Slope line gains
  the side the ground falls away to, relative to the skier — "Very
  steep, falling skier's right" (`fallSide`, the sign of the aspect's
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

The first rule called any crossing of the fall line in the direction of
travel a switchback, uphill or down, and read the crossing from a side
flip alone, so a fall-line track wobbling 2° either side of the aspect
counted. The second called every two-sector segment a turn. Both were
wrong on the ground. "Switchback" is an uphill word: a climb that
crosses the fall line is one, and a climb that changes heading on one
side of it is just a turn. Downhill, a skier whose track heads straight
down the slope at either end is on the fall line, whatever the track
does in between; calling that a turn hid the one fact a descent is
planned around.

The third rule (SNOW-1094) dropped "turn" and "switchback" as kinds,
because each repeated something the card already said. "Turn" restated
the heading pair: "NW → W" is a turn. "Switchback" restated the Slope
line: "falling skier's left, then right" is a crossing of the fall line.
With both gone, a turning segment's kind is what its gradient says,
ascent or descent, or the fall line on the way down.

The same change moved a turning segment's side onto its ends. The side
had come from the chord's reading, which carries a side only when the
chord itself is a traverse. Two routes that share a bend split it into
segments at different points, so their chords differ: on the bend near
Les Tsamos, Mont Fort – Backside read "Moderate slope, falling skier's
left" and Mont Fort – Col de la Chaux read "Moderate slope", under the
same headline. Where a segment turned, its chord is a line between two
headings the track never took, and says nothing about which way the
ground falls under the skier; the two ends do.

The labels went on in the same change. Two unlabelled lines left the
reader to work out that the first described the track and the second
the ground; with a label each, the ground words no longer need "slope"
in them, and the steepness and kind read as one phrase, "Gentle
descent", rather than a list.

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
  sector, so a kick turn that only just reaches the fall line can miss
  it by up to 22.5° and name one side rather than two.
- The descending fall-line test is sector equality, so a descent heading
  22° off the fall line can still read as a plain descent.
- How far the track turned is said only by the heading pair — "NE → E"
  against "S → NE" — since no kind names a turn.
