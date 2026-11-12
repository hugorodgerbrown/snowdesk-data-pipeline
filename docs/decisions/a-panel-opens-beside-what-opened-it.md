---
name: a-panel-opens-beside-what-opened-it
description: Desktop map panels open beside their trigger — selections left under search, roundel panels right; one object sheet; no sheet focus ring
status: current
last-reviewed: 2026-11-10
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
- **A roundel opens on the right.** Roundel panels (downloads, terrain
  filter) and the layers menu are opened from the roundel column at the
  right edge, so they inset beside that column, as they always have. Form
  panels opened from them follow them.
- **Exception: Saved docks left (SNOW-1481, Hugo).** The star opens Saved —
  the favourites, routes, trips and report sheets as one panel — in the
  left column under search, though its roundel is on the right, so that
  the reader's own lists sit where the installed app's Saved does
  ([`saved-is-one-panel-on-every-screen.md`](saved-is-one-panel-on-every-screen.md)).

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
3. **On a desktop, a side panel covers what is under it and moves
   nothing.** The zoom, compass, help and legend roundels stay where they
   are while a selection sheet is open, under the column; a reader who
   wants them closes the sheet. The date row, which is every sheet's floor
   and sets the day the sheet shows, stays put beneath it. What those
   roundels open (the legend card, the zoom flyout, the calendar) still
   opens beside the column, so a roundel left visible below a short sheet
   never opens its card behind it. This is the phone rule too: a sheet
   covers and moves nothing. Option A, a shorter panel stopped above the
   stack so nothing moved, was mocked and declined: it cost about 150px of
   reading height on a laptop.

   *Revised 8 Nov 2026 (Hugo).* Until then the roundels slid to sit beyond
   the column whenever a sheet opened, on the view that a desktop column is
   a standing region and what sits under it should move out rather than go
   dark. In use, four controls jumping sideways on every pick read as
   restless, and the controls were rarely wanted while reading a sheet.
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
