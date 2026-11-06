---
name: swisstopo-raster-beyond-the-border
description: swisstopo raster winter map beyond the Swiss border, swisstopoedge:// protocol, Swiss mask, z13 fallback, 3D GATE_OUTLINE
status: current
last-reviewed: 2026-11-06
---

# swisstopo's raster map draws beyond the Swiss border

**Decision.** SNOW-1457 (6 Nov 2026), step 1 of the coverage roadmap. The
swisstopo winter basemap draws swisstopo's **raster** winter national map
(`ch.swisstopo.pixelkarte-farbe-winter`, WMTS, Web Mercator) wherever it
reaches outside Switzerland, over the vector style it already uses. Inside
Switzerland nothing changes: the vector map draws it, as before. 3D opens
wherever the raster has detail, not only over Switzerland.

**Why.** The vector style (`ch.swisstopo.basemap-winter.vt`) is
Swiss-only: its labels stop at the border and its rock hachures at about
10.5°E. The raster map keeps full detail, hachures included, well past
it. Measured on 6 Nov 2026:

| Tile zoom | Detail reaches |
|---|---|
| 13 and below | a quad from about (5.32, 48.22) to (11.42, 48.17), (11.25, 45.31) and (5.40, 45.33); below 12 a small-scale overview everywhere |
| 14–15 | about 10.5°E |
| 16 and above | close to Switzerland only |

That is all of Vorarlberg and the Aosta Valley, most of Tyrol, Lombardy
and Trentino, half of South Tyrol and the Mont Blanc side of the French
Alps, 118 regions outside Switzerland at their centres. Hugo put it first
on the roadmap because it improves the 2D map today.

## How

- **The splice** (`swisstopo_edge_core.spliceStyle`). One raster source and
  one layer are appended to the fetched winter style, so they draw over
  every basemap layer and under every layer the page adds. Not under the
  vector labels: the vector base source carries roads and place names
  outside Switzerland at low zooms, and they would double the raster's
  own. `resolveBasemapStyle` (`map_state.js`) splices on every swap: the
  picker, 3D and the online retry. At boot, `map.js` starts on an empty
  style and sets the URL at once with `transformStyle`. Booting the way
  basemap.at does, with a swap once the style resolves, lets `load` fire
  on the empty style. The map's load handler then installs its layers on a
  style the swap is replacing, and threw.
- **The protocol** (`swisstopo_edge.js`, `swisstopoedge://{z}/{x}/{y}`).
  For each tile:
  1. **Switzerland.** A tile wholly inside Switzerland returns empty and
     fetches nothing.
  2. **The fallback.** A blank tile (swisstopo answers pure white where it
     has no detail) is replaced by the nearest ancestor with detail, cut
     and enlarged. The walk never goes past z13, and ancestors sit in a
     small LRU.
  3. **The cut.** Switzerland is erased from the result.
- **The mask** is the union of CH's L1 regions, from
  `major-regions.geojson?country=ch`. Each polygon's outer ring and holes
  are oriented opposite ways, and they are filled together as one path
  with the non-zero rule. Neighbouring regions' shared edges then leave no
  anti-aliased seam, and a hole stays open.
- **Failures.** If the mask cannot load, every tile rejects, so nothing is
  drawn over Switzerland and MapLibre asks again later. A tile with no
  detail to draw also rejects if any request it depended on failed, its
  own or an ancestor's, and is never kept as an empty tile. A failed
  ancestor is dropped from the LRU, so the retry asks for it again.
  MapLibre 4.7.1 asks for no frame when a tile errors, and fires `load`
  only from a frame, so a failed edge tile left last would hold `load`, and
  every layer the page installs on it, until the reader moved the map.
  `map.js`'s `error` handler calls `triggerRepaint` for an edge-source
  error (`tests/js/test_map_edge_tile_error.js`).
- **128 px tiles.** MapLibre draws each 256 px tile at 128 CSS px, so it
  asks for the next zoom up. At 256 the raster's lettering was about twice
  the size of the vector map's across the border, and it was enlarged on
  every phone. At 128 it matches the vector map's lettering, and it is
  sharp at a device pixel ratio of 2. It costs four tiles where there was
  one, outside Switzerland only. The source's `maxzoom` is 16. Above that,
  MapLibre enlarges the tile itself.
- **The credit** is `© swisstopo`, the exact string the vector sources
  carry, so `updateMapAttribution` shows it once.
- **The service worker** learns `wmts.geo.admin.ch` from
  `swisstopo_edge_core.WMTS_ORIGIN`, seeded in `map.js`. A custom
  protocol's URLs have no origin to learn it from.
- **The 3D gate** is `swisstopo_edge_core.GATE_OUTLINE`, the z13 quad above.
  `map_view_core.covers` takes a ring as well as a box. `terrain_filter_core`'s
  `COVERAGE_BOUNDS` stays the slope tiles' box, and is the gate's fallback
  only if the edge core failed to load.

## Consequences

- **Past camera z14, east of 10.5°E, the map is an enlarged z13.** It is
  soft, and at the closest zooms visibly so. It is still the only detailed
  map there: the vector style draws nothing east of its own coverage.
- **EAWS outlines** draw on swisstopo winter in Austria, Italy and France
  (`BASEMAP_COUNTRIES`). That includes the parts of those countries the
  raster does not reach at close zoom
  ([boundaries-follow-the-basemap](boundaries-follow-the-basemap.md)).
- **Out of scope.** Offline area downloads still cache only the vector
  map, so the raster beyond the border is online-only, apart from what the
  service worker has cached in passing. Also out of scope: the trip page's
  style resolver (`basemap_style_core.js`), the light swisstopo style and
  the `/map/3d/` sandbox.
- **A seasonal or style change at swisstopo** that changes the blank-tile
  colour would make blank tiles read as detail. `isBlank` would then need
  the new colour.
