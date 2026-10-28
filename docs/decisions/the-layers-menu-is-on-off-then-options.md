---
name: the-layers-menu-is-on-off-then-options
description: Layers menu #basemap-menu — one switch per layer, options pages (data-layers-page), bulletins and boundaries master switches
status: current
last-reviewed: 2026-10-28
---

# The layers menu is on/off first, options one level down

**Decision.** The map's layers menu (`#basemap-menu`, SNOW-1266) has two
levels. Level one has one row per layer, in two groups:

- **Reference data**: Avalanche bulletins, Steep terrain, Weather, Resorts,
  Boundaries, Basemap.
- **Your data**: Favourites, Routes, Field observations, Downloaded areas.

Every row has the same shape. A switch on the right says whether the layer
is drawn. A layer with options has an opener on the left (icon, name, a
summary line and a right chevron) that shows its options in place of level
one, on a `data-layers-page` with a back button. A layer with no options is
a single control: the whole row is its switch. Basemap has no switch,
because there is always one basemap. Steep terrain's opener opens the
terrain filter's own sheet, because that sheet is the layer's options.
Every open starts at level one. The menu is full width on a phone, with its
own close button; on a desktop it sits beside the roundels at a fixed width.

Bulletins and Boundaries have no single row that could be their switch, so
each has a **master** switch (`data-layer-master`), derived from the rows on
its page:

- **Bulletins** is on while an opacity step above 0 is checked. Switching
  it off sends step 0, which replaces the old "Bulletins off" segment.
  Switching it on sends the step it was switched off at
  (`snowdesk.map.bulletins.last-on-step`), or 50% if none is stored.
- **Boundaries** is on while any EAWS tier is on. Switching it off clicks
  each tier that is on, and remembers them
  (`snowdesk.map.boundaries.last-on`). Switching it on clicks them back, or
  Micro regions if none are stored.

The header counts the level-one switches that are on. A provider or a tier
never counts on top of its layer, and the basemap is never counted.

**Why.** SNOW-904's four collapsible sections (Places, Conditions,
Boundaries, Basemap) grouped rows by subject. That mixed two different
questions in one list, "is this drawn?" and "how is it drawn?". Hugo tried
three regroupings and a drill-down, and each one left a reader unsure where
a layer lived ("Where are routes — routes menu, or layers menu?"). Asking
the on/off question first, once per layer, answers "what is on my map" in
one screen. The options live one tap down, where they cannot be confused
with a second layer. The groups follow the map's own model: what Snowdesk
draws from providers and its own reference data, and what the reader made.
Field observations are user-generated content, so they belong in Your data.

**Consequences.**

- Each row is still the `.basemap-menu-item` button it was. map.js,
  map_layer_sync_status.js, map_panel_overlay_switch.js and the bridges
  needed no change.
- A master switch only clicks the rows it governs. Nothing downstream knows
  that masters exist, and a master never writes a layer's own storage key.
- Highlight problem terrain is on the bulletins page while the steep-terrain
  layer is rethought.
- The per-section storage keys (`snowdesk.map.layers.section.*`) are
  abandoned and are no longer read.
- A new layer is a new level-one row, using
  `public/partials/_map_layers_switch_row.html`, or an opener plus a switch
  if it has options. It is never a row on another layer's page.
- Amends [map-controls-are-two-groups-without-an-on-map-marker](map-controls-are-two-groups-without-an-on-map-marker.md):
  the opacity control is now on the bulletins page, and it has four steps.
