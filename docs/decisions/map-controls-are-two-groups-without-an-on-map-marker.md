---
name: map-controls-are-two-groups-without-an-on-map-marker
description: .map-control-group capsules (map, yours), no data-overlay-shown ring, bulletin opacity menu row, map_panel_overlay_switch.js
status: current
last-reviewed: 2026-10-19
---

# The map controls are two groups, with no on-map marker

**Decision.** The bottom-right control column is two glass capsules
(`.map-control-group`), 16px apart, above the chevron:

- **the map** (`#map-control-group-map`): locate and layers. Always shown.
- **yours** (`#map-control-group-yours`): favourites, field observations,
  routes, downloads, in that order. It is the inner of
  `#map-controls-collapsible`, so the chevron folds this capsule and only
  this one.

Three changes come with it:

1. **No roundel draws an "on the map" marker.** The coloured ring painted
   from `data-overlay-shown` is gone from `static/css/map.css`.
   `static/js/map_roundel_overlay_state.js` still paints the attribute and
   the roundel's aria-label, so a screen reader still hears "shown on the
   map".
2. **Bulletin opacity is a layers-menu row.** The fill roundel
   (`#map-fill-toggle`), its flyout and `mapFillControlInit` are deleted. The
   five steps are a `role="radiogroup"` labelled "Bulletin opacity"
   (`#map-bulletin-opacity`) in the Conditions section, after ALBINA.
   Picking a step dispatches `snowdesk:bulletins-step` and leaves the menu
   open.
3. **Each "yours" panel has a "Show on the map" switch**
   (`includes/_map_overlay_toggle.html`, through `_ugc_panel.html`'s
   `toggle_id`). The switch and that layer's layers-menu row control the
   same setting. `static/js/map_panel_overlay_switch.js` carries a flip by
   clicking the row, and re-seeds every switch on
   `snowdesk:overlay-visibility-changed`.

**Why.** The old column was one strip of seven roundels, sorted by nothing a
reader could name: locate stayed out, and the rest folded together. Two
groups split it by what each control is about. "The map" is how the map
itself is read: where you are and what is drawn. "Yours" is your own
things. Downloads belongs with yours: an area is something the reader chose
and keeps, listed and managed in a panel like the other three, rather than a
view setting.

The ring was dropped because inside a shared capsule it read as a selected
tab: a lit roundel looked like the panel that was open, not like a layer
that was drawn. It also duplicated the layers menu, which already answers
"what is on my map" in one place, and the map itself shows the pins and
lines.

Opacity moved into the menu because it is a setting about what is drawn,
which is the menu's job. A roundel of its own was the one control in the
column that opened neither a panel nor a list.

The panel switch does not bring back SNOW-904's problem. SNOW-904 removed
these switches when they were the *only* control, which meant a reader had
to open five surfaces to learn what could go on the map. The menu still
lists every layer. The switch is a second way in to one layer, where the
reader is already looking at that layer's contents. Because it works by
clicking the menu row, it reuses the row's sign-in hand-off, its
offline-disabled guard and its bridge call, and adds no second copy of any
of them.

**Consequences.**

- With a route open on a phone, the map capsule stays and the yours capsule
  keeps only the routes roundel; the chevron hides. While a sheet is open,
  or the map is in `body.map-framing`, both capsules withdraw, as the column
  did before.
- The layers menu no longer closes when the chevron folds. Its roundel is in
  the capsule that never collapses, so the observer that closed it was
  removed.
- A new roundel joins one of the two capsules. One that would need its own
  on-map marker should first ask whether the layers menu already says it.
- The switch is shown to signed-out visitors, as SNOW-658's was. On
  favourites and routes it hands off to sign-in through the row, and then
  flips back.
- SNOW-832's empty-downloads guard did not come back. The switch drives the
  row, and the row shows the squares whether or not any area is downloaded.
- Supersedes the control placement described in
  [`bulletin-fill-is-a-user-choice.md`](bulletin-fill-is-a-user-choice.md),
  whose decision about the steps themselves stands.
