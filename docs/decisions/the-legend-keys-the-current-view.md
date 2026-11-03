---
name: the-legend-keys-the-current-view
description: Why the map legend (map_legend.js, map_legend_core.js) opens on a key for the current view, with All keys & sources behind it
status: current
last-reviewed: 2026-11-03
---

# The legend keys the current view

**3 Nov 2026.** The (i) card at the bottom left of the map
(`#map-legend-card`, `public/partials/_map_embed.html`) had grown to list
every key the map could ever need on every visit: the danger scale, pins,
field reports, slope classes, route legs, downloaded maps and the
attribution, about 1,150 px on a phone, with wildlife rest zones still to
come. Hugo: "This is now getting out of hand — we need to move to a 2-tier
legend."

## Decision

- **Three tiers, by how much the reader wants.** The card opens on
  **On the map now**, one compact line per layer. Its second page,
  **All keys & sources**, lists every key short, with an "On" dot on the
  layers switched on, and the full attribution. The **Map key** help topic
  (`#help-topic-map-key`, `help/_topic_map_key.html`) is the long form,
  linked from the second page. This follows the layers menu's own pattern,
  on/off first and detail one level down
  ([the-layers-menu-is-on-off-then-options](the-layers-menu-is-on-off-then-options.md)).
- **The first tier reflects the current view** (Hugo: "it should reflect
  the current view"). A key shows only while its layer is on (and not held
  by Terrain mode), the zoom draws it, and something of it is inside the
  viewport. `map_legend.js` asks the map, through `queryRenderedFeatures`
  over the viewport and the bridges map.js publishes
  (`pwaBulletinsLayer.isVisible`, `pwaTerrainFilter.availability`).
  `map_legend_core.js` (`legendState`) turns the answers into keys, rows and
  "On" marks. A row inside a key follows the view too: a field-report
  family, the live and recent states, the three rest-zone kinds, a resort
  or a favourite. A clustered report counts for every row, because a
  cluster hides which pins it holds.
- **Worked out only while the card is open,** on opening, on the map's
  `idle` and on the overlay, date, mode and terrain announcements. A closed
  card costs a pan nothing.
- **Four keys keep their own switch.** Problem terrain, Steep terrain,
  route legs and downloaded maps were already shown and hidden through
  `hidden` by their own modules (`map_problem_terrain_key.js`,
  `terrain_filter_sheet.js`, map.js's `syncRouteLegLegend` and
  `syncCoverageLegend`). The legend never writes `hidden` on them. It adds
  `data-legend-out-of-view` for the in-view half and reads `hidden` back,
  watched by a MutationObserver, so the two writers cannot fight.
- **The danger scale is shown once.** Problem slopes are painted in the
  danger colours, so their key says "Same colours, on the slopes" and keeps
  only the marks that are not on the scale (Hugo: "The scale is the danger
  level - it doesn't need repeating"). The scale therefore shows while
  problem slopes are painted, even with the regions' own colour off or
  faded to nothing.
- **"On" means switched on, not in view.** The second page's marks read
  each layer's own switch (its layers in the style and not hidden, or its
  module's state), so a layer with nothing in view, or below the zoom it
  draws from, still reads as on.
- **A one-line credit in the first tier,** naming each tile provider once
  (`shortSources`) and the three avalanche providers while their key is in
  view. The licences are honoured in full on the second page and in the
  colophon.
- **Rest zones get a key at last.** "Closed by law" (red), "Advised"
  (amber), and each again faint out of season — "Closed by law, out of
  season" and "Advised, out of season" — since the map keeps a zone's
  colour outside its period (Hugo, 3 Nov 2026: one faint row hid that two
  kinds were drawn),
  behind the same `route_drawing` flag as the layer. The colours are
  `--color-rest-binding` / `--color-rest-recommended`, mirroring
  `rest_zones_core.js`.
- **"Show map demo" left the card** for the "?" menu (Hugo: "show map demo
  should be in help, not info"). It shows how the map works, not what is on
  it.

- **Everything drawn over the map has a key, and every key is drawn.**
  Hugo, the same day: "Everything that renders over the map need a entry.
  And the entries must be visual". Weather symbols, SAC ski tours (way
  up, way down, summit, hut), the boundary tiers and the bulletins'
  grouping outline, a route's flat line, slope colours and marks, and the
  reader's position joined the keys. Both pages draw a key's rows from
  one partial (`public/partials/_map_legend_key.html`); the second page
  shows every row as swatches, line samples and marks, two to a line, and
  the prose is the help topic's alone. The route key now shows for any
  route on the map, not only a legged one, since it has a row for the flat
  line.

## Not done
- A pinned resort is keyed whenever resorts are in view, not only when a
  pinned one is: which resorts are pinned is private to map.js.
