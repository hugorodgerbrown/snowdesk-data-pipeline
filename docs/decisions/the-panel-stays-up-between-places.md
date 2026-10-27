---
name: the-panel-stays-up-between-places
description: Map object sheet stays up between places (MapSheet pending, data-sheet-stale, morph, open({awaiting}), data-sheet-handover)
status: current
last-reviewed: 2026-10-27
---

# The panel stays up between places

**Decision.** When the reader moves from one resort, region or weather
point to the next, the open object sheet (place, region, weather) keeps
what it shows until the next body replaces it in one swap. The owner calls
`controller.pending({ keep })` instead of emptying the sheet: the current
children are marked `data-sheet-stale` and dimmed (after a 150 ms beat),
whatever the owner has already refreshed — the place sheet's heading, the
region sheet's peek row, repainted from the page — stays at full strength,
and the swap clears the marks. A phone sheet then glides between detent
heights through its CSS height transition; a desktop panel, which is as
tall as its content and so has no height to transition, is carried from its
old height to its new one by `morph` in `static/js/map_sheet.js`
(`data-sheet-morphing`, 240 ms). A sheet that takes over from another kind
(resort to weather, region to resort) starts at the height the other stood
at (`data-sheet-handover` skips the slide from the foot) and, when its owner
opens it with `{ awaiting: true }`, holds that height until its body lands
or 1.5 s pass.

**Why.** Hugo, 27 Oct 2026: flicking between resorts, the overlay
disappeared and reappeared, "looking like it's bobbing up and down". Every
owner emptied the open sheet to a loading line (or the region's peek row)
the moment a new place was asked for, so the sheet fell to a sliver and
rose again when the answer landed, 300–600 ms later on a phone network; the
weather sheet also called `open()` again, which put it back at its first
detent. Measured on a phone at 390×844: 365 px → 82 px → 365 px for every
resort-to-resort tap, 420 → 171 → 422 for regions at half. The reader is
comparing two places; the panel is the constant, only its content changes.

**Consequences.**

- An owner never empties an open object sheet to show it is loading. It
  calls `pending()` and lets the swap replace the content; a failed fetch
  replaces the stale body with its failure line, so an old place is never
  left under a new name.
- What is dimmed still names the place the reader left, so its controls
  (a resort's star, a pin's Share, Rename and Delete) act on nothing until
  the swap: map.css takes the pointer off them and `swallowStale` drops a
  keyboard press. The × still closes the sheet.
- Opening an already-open object sheet must not call `open()` again — that
  restarts the detent. Keep the detent the reader chose.
- `prefers-reduced-motion` turns off the desktop morph and the hand-over;
  the phone detents already drop their transition under it.
- Scrolling back to the top for a new place stays with
  `map_panel_trail.js`, which sees the swap bring a new `data-panel-key`.
