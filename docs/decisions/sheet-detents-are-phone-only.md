---
name: sheet-detents-are-phone-only
description: Phone sheet detents (_overlay_sheet.html detents, data-sheet-detents, bindDetents); forms take none; sheet covers, never moves, map controls
status: current
last-reviewed: 2026-10-16
---

# Sheet detents are phone-only, and forms take none

**Decision.** `includes/_overlay_sheet.html` takes an optional `detents`
list drawn from `peek`, `half` and `full`. Below the `sm` breakpoint (640px)
a sheet that declares detents shows a drag handle and rests at one of those
heights; at `sm` and above it is the inset desktop panel, unchanged, and
the handle is hidden. A sheet declares only the detents it can use: a thing
tapped on the map (a region, a route, a pin) uses all three, a list uses
`half,full`, and a form (report, add favourite, sign in) declares none and
keeps its one docked state.

**Why phone-only.** The detents exist to keep the ground visible while the
reader looks at what they tapped. On a phone the docked sheet covered the
map, so the reader lost the very thing they were asking about; peek and half
give it back. On a desktop the inset panel already sits beside the map, so
there is nothing to give back, and a draggable panel there would only add a
gesture a mouse does badly (map-page functional spec §3.7). Google and Apple
Maps make the same split: a draggable sheet on a phone, a fixed side panel
on a desktop.

**Why full width, and why the furniture is covered rather than moved.**
Hugo's review of the first build (16 Oct 2026): an inset card with the whole
roundel column and the bottom-left stack lifted onto it read as two layers
of chrome stacked over the ground the sheet was meant to leave clear. A
detent sheet is therefore full width and flush to the foot. Two later
builds kept the column in play — lifting it onto the sheet's edge, then
folding it down to locate — and both were withdrawn the same day: each
needed the column's geometry measured and published on every settle (the
map's foot sits above the viewport's when the site footer runs under it,
which the first lift got wrong), and each moved controls the reader had
not touched. The sheet now slides up over the map's furniture and covers
it; nothing on the map moves or folds. What a peeking sheet would have
hidden that matters — the date — moved to the phone's top row instead,
beside search, where no sheet reaches.

Covering does not mean leaving covered controls live. The sheet's top edge
lands where the detent puts it, not on a row boundary, so it can leave a
sliver of a roundel above it, and a covered button stays in the tab order.
So what a detent covers is set `visibility: hidden`, keyed on the detent
`map_sheet.js` writes to the root's `data-map-sheet`: at peek the bottom
row on each side (the chevron and the info roundel), at half and full both
stacks whole. Nothing moves; what is under the sheet is simply gone.

**Why forms take none.** A drag down below the lowest detent closes the
sheet. On a form that would throw away what the reader has typed, from a
gesture they may have meant as a scroll. A form is a task that ends with a
submit or a cancel, not an object that can be glanced at, so it has no
summary to peek with either.

**Why the rules are a separate core.** The settle rule (nearest detent to
the release height projected by its velocity, close below half the lowest)
is pure arithmetic, so it lives in `static/js/sheet_detents_core.js` under
`// @ts-check` and is tested in Vitest, and `map_sheet.js`'s `bindDetents`
only binds it to pointer events. Without the core on the page,
`bindDetents` returns null and the sheet behaves exactly as before.

**Consequences.**

- Every surface adopts detents in its own ticket (region SNOW-1098, route
  SNOW-1099, resort and pin SNOW-1100, weather SNOW-1101, the four lists
  SNOW-1102). Declaring detents on a form is a regression, not a choice.
- Every detent height rule is inside a phone media query in
  `static/css/map.css`. Do not add a desktop branch.
- The camera padding is applied with the view held still (the centre moves
  to the point already under the new padded centre), so a settle never
  moves the ground. At full the padding stays at the previous detent's,
  because the map is covered.
- Content marks the end of its peek summary with `data-sheet-peek`, and a
  header the reader may drag by with `data-sheet-drag`. Controls inside a
  draggable header keep their own press. Content may also narrow the
  declared detents with `data-sheet-detents`; the region sheet drops FULL
  on a day with no bulletin, since FULL exists to reveal one.
- Nothing on the map is repositioned for a sheet. `data-map-sheet` on the
  root says a sheet is up; do not reintroduce a lift or a fold without
  reading the paragraph above.
- HALF and FULL fit their content (Hugo, 16 Oct 2026, on the route
  sheet): `bindDetents` measures it and writes `--sheet-half-height` and
  `--sheet-full-height`, capped at half the screen and at the room below
  the top inset. HALF stops under an element the content marks
  `data-sheet-half`, else at the end of the content. A ResizeObserver
  re-fits the resting detent when content changes height in place (the
  route sheet's point header), but never mid-drag. Content that must count
  toward FULL while hidden below it is hidden with `visibility`, not
  `display: none`, which has no box to measure.
- The route sheet's FULL is staff-only (SNOW-1099): it is the point debug
  readout. The partial declares `peek,half` for everyone else.
- A padding change is skipped while the camera is moving and applied on
  `moveend`, because a `jumpTo` mid-flight cancels the flight (a search
  pick frames its region just as the sheet's content re-fits PEEK).
