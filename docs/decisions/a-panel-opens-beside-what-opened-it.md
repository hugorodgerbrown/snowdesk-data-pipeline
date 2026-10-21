---
name: a-panel-opens-beside-what-opened-it
description: Desktop map panels open beside their trigger — selections left under search, roundel panels right; one object sheet; no sheet focus ring
status: current
last-reviewed: 2026-10-21
---

# A panel opens beside what opened it

**Decision.** On a desktop (640px and up) a map panel opens on the side of
the thing that opened it.

- **A selection opens on the left.** The object sheets (region, place,
  weather, and the route panel) describe something the reader picked: a
  tap on the map, or a pick from the search pill at the top left. They
  dock in one 400px column under that pill, from the pill row down to the
  date row. On a phone the same sheets slide up from the foot with their
  detents ([`sheet-detents-are-phone-only.md`](sheet-detents-are-phone-only.md)).
- **A roundel opens on the right.** Roundel panels (downloads, favourites,
  observations, routes list, terrain filter) and the layers menu are opened
  from the roundel column at the right edge, so they inset beside that
  column, as they always have. Form panels opened from them follow them.

`includes/_overlay_sheet.html`'s `side="left"` marks a selection sheet;
`static/js/map_overlay_bounds.js` places it and `static/css/map.css` ("The
selection column") does the rest.

**Why.** Hugo's review, 21 Oct 2026. Picking a region in search, at the top
left, opened its sheet at the far right of the map: the eye had to cross
the whole screen to find the answer to what it had just asked. And it put
"what I selected" in the same corner as "how the map is drawn" (layers,
data overlays), two different kinds of thing sharing one place. Split by
side, each side means one thing: the left is what you have selected, the
right is the map's controls and your own lists. Google Maps makes the same
split, with the place panel on the left and the map controls on the right.

**Rules extracted from it.**

1. **A surface opens beside its trigger.** It is the general form of the
   decision, and it applies past the map: a menu drops from its button, a
   calendar rises from the date control (SNOW-794), a list sits beside its
   roundel. A surface that opens across the screen from the control that
   asked for it is a defect.
2. **One object sheet at a time, at every width.** The four selection
   sheets share one column on a desktop as they share one foot on a phone,
   so opening one closes the others (`map_sheet.js`, `closeOtherSheets`).
   That includes the route panel: picking a region closes an open route.
3. **On a desktop, what a side panel covers steps aside; the floor never
   moves.** The zoom, help and legend roundels slide to sit beyond the
   column while a selection sheet is open. The date row, which is every
   sheet's floor and sets the day the sheet shows, stays put beneath it.
   This is the desktop counterpart of the phone rule that a sheet covers
   and moves nothing: a phone sheet is a temporary layer over the whole
   width, while a desktop column is a standing region, so what sits under
   it moves out rather than going dark. Option A, a shorter panel stopped
   above the stack so nothing moved, was mocked and declined: it cost about
   150px of reading height on a laptop.
4. **What drops from the search pill draws over the column.** Search
   results are the next selection, so they sit above the current one.
5. **A sheet is a focus target, not a control.** A sheet takes focus as it
   opens, so Escape and the keyboard reach it, but it draws no focus ring
   round itself; the controls inside it keep theirs. A ring round a whole
   panel, for a press nobody made, read as the panel being selected.

**Consequences.**

- A new map panel answers two questions before anything else: what kind it
  is (the design system's "Map panels" table) and what opened it. A new
  selection sheet passes `side="left"`; a new roundel panel passes nothing.
- A camera fit that frames a selection reserves the column's width
  (`paddingClearingRegionSheet` in `static/js/map.js` measures which side
  the sheet is docked on).
- Anything new in the bottom-left stack above the date row slides with the
  zoom, help and legend roundels; anything new on the date row does not.
