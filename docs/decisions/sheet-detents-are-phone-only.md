---
name: sheet-detents-are-phone-only
description: Phone map sheet detents (peek, half, full; _overlay_sheet.html detents, sheet_detents_core.js, bindDetents); forms take none
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

**Why full width, and only locate stays.** Hugo's review of the first
build (16 Oct 2026): an inset card with the whole roundel column and the
bottom-left stack lifted onto it read as two layers of chrome stacked over
the ground the sheet was meant to leave clear. A detent sheet is therefore
full width and flush to the foot, and while it is open the map keeps one
control, locate, on the sheet's top edge; the rest return when it closes.

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
  draggable header keep their own press.
