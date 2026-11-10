---
name: one-sheet-design-on-a-phone
description: Phone sheet card — Saved, Help, New pin; data-sheet-card, data-sheet-fit, half 50dvh, full 75dvh, grab-handle header
status: current
last-reviewed: 2026-11-10
---

# One sheet design on a phone

**Decision.** Below 640px, in the installed app and in a phone browser
alike, Saved, Help and New pin are one card (SNOW-1486):

- **One card.** The same 12px inset each side (`--map-edge-inset-*`, whose
  bottom one clears the app's tab bar), the card's corners and the glass
  shadow. The four Saved sheets pass `card=True` to
  `includes/_overlay_sheet.html` (`data-sheet-card`); the "?" menu
  (`#map-help-menu`) takes the same rules in `static/css/map.css`'s "One
  sheet design on a phone" block.
- **One header.** A grab handle, then the title and × on one row.
  Saved's title is **Saved items**, with its tabs on the row below and the
  + right beside them (`includes/_sheet_header.html`, `lead_template`). The
  × shows on every phone: nothing in Saved is `.app-hidden`.
- **One height rule.** Saved opens at **half** (50dvh) so the map stays in
  view, and its handle takes it to **full** (75dvh); a drag down from half
  closes it. Content marked `data-sheet-fit` (New pin, the report form, the
  saved confirmations) and the "?" menu are as tall as their content, never
  past 75dvh.

The card is not an object sheet. `static/js/map_sheet.js` binds its
detents but marks no `data-map-sheet`, lifts no camera, and drags from the
handle and header only, so a finger on the list scrolls it. It hands its
detent only to another card, so a Saved tab switch keeps the height. A
fit sheet's handle is drawn but inert, so a typed name is never lost. Help
closes on a drag down its header, at the distance a Saved sheet closes at
(`map_help_menu.js`). From 640px nothing changes: Saved docks left under
search and the "?" menu keeps its corner.

**Why.** Each panel drew its own card, header and height: Saved filled the
screen with no title or ×, Help was flush left at half height with a
"HELP" label, and the add-a-pin form filled the screen over the pin it was
placing, with a Cancel that did what × did. Hugo's board (10 Nov 2026) put
them on one card. Half keeps the map in view; three quarters caps the
list. A task fits its content so the map and the pin stay visible.

**Consequences.**

- Saved at half may sit over part of the right-hand roundels in a phone
  browser; × closes it.
- This supersedes, for the Saved sheets, the "a roundel list declares no
  detents" rule of
  [`sheet-detents-are-phone-only.md`](sheet-detents-are-phone-only.md):
  they declare half and full, as cards, and hide no roundel.
- A new phone sheet that is not a thing tapped on the map passes `card=True`
  and marks its forms `data-sheet-fit`.
