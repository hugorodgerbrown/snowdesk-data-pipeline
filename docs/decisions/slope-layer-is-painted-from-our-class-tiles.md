---
name: slope-layer-is-painted-from-our-class-tiles
description: Slope layer = terrain filter slope mode (SLOPE_PALETTE, slope_classes_core.js) on class tiles; replaces SLOPE_TILE_URL raster
status: current
last-reviewed: 2026-10-16
---

# The slope layer is painted from our terrain-class tiles

**Decision.** The map's slope-angle layer is the terrain filter's
"Colour by slope" mode (SNOW-1073). It paints each pixel of the
terrain-class tiles (SNOW-978,
[terrain-filter-is-class-tiles-filtered-on-device.md](terrain-filter-is-class-tiles-filtered-on-device.md))
by its 5° slope band on the SLF ramp — 30–35, 35–40, 40–45, 45–50 and
over 50° — on the device, through the same `terrainfilter://` source as
the match tint. swisstopo's pre-rendered "Slope classes over 30°" raster
(`ch.swisstopo.hangneigung-ueber_30`, SNOW-691) is removed: its Layers
row, its legend section, `slope_overlay_core.js`, `SLOPE_TILE_URL` and its
CSP origin, and the pinning of its tiles in offline downloads (SNOW-692).

- The mode is a field of the persisted `snowdesk.map.terrain_filter` JSON:
  `mode: 'match' | 'slope'`. Absent or unknown reads as `'match'`.
- `createClassifier(filter)` / `paletteFor(filter)` in
  `static/js/terrain_filter_core.js` pick `createSlopeClassifier` and
  `SLOPE_PALETTE` for the slope mode. The ramp is
  `static/js/slope_classes_core.js`'s `CLASSES`, which
  `route_slope_core.js` reads too.
- The mode shares the sheet's aspect, slope-range and elevation settings.
  The slope range says which classes are painted; it defaults to 30° and
  up.
- Nothing is drawn below z12, where the tiles start; the sheet says
  "zoom in", as it does for the match mode.
- An old `snowdesk.map.overlay.slope=true` is ignored, not migrated:
  carrying it over would silently switch on a filter that needs the
  class tiles.

**The source is still swisstopo.** Both layers start from swisstopo
elevation data. Ours is swissALTI3D, the 0.5 m elevation model, resampled
to the 5 m grid the terrain tiles carry. What changes is who computes the
slope and how: Snowdesk computes it, with the same Horn kernel and 10 m
window as `sample_slope` (`apps/locations/services/terrain.py`), rather
than reading swisstopo's finished picture.

**What swisstopo says about its raster.** The layer's metadata — the
geo.admin.ch legend at
`https://api3.geo.admin.ch/rest/services/api/MapServer/ch.swisstopo.hangneigung-ueber_30/legend?lang=en`
and the geocat.ch record `77f0637f-8d52-45bc-b824-d6e5719de55b`, read on
2026-10-16 — says it is the first derivative of a **combined 10 m
elevation model**: swissALTI3D for Switzerland and Liechtenstein, RGE ALTI
for France, TINITALY/01 for Italy, DGM10 for Austria, DGM1 for Bavaria and
EU-DEM for Baden-Württemberg, classified as the SLF recommends; that the
slope is recalculated when swissALTI3D is updated; and that precision is
not guaranteed where two base models meet. It does not name the slope
kernel or its window. So the one documented difference in method is the
input — a 10 m multi-country composite against our 5 m swissALTI3D grid.
This record claims no difference in the slope algorithm, because the
metadata does not state one.

**Why.**

- **One model behind the overlay, the route line and the point card.**
  The route's slope-coloured core (z14 and up), its profile and the point
  card all read `sample_slope` over the 5 m grid. swisstopo's raster came
  from a different model, so a face could be orange on the overlay while
  the route line across it showed another class. Now the layer and the
  line are the same numbers.
- **The same numbers as `sample_slope`**: the same grid, kernel, window
  and 5° boundaries, so "35°" means one thing on every surface.
- **An aspect and elevation filter.** A painted raster cannot be
  filtered; class tiles can. The slope layer narrows to "north-facing,
  above 2,400 m" with the controls the filter already had.

**Costs.**

- **None of our shading below z12.** swisstopo's raster drew from z0; ours
  is blank zoomed out, and the sheet says zoom in.
- **Narrower coverage.** Our tiles cover Switzerland only
  (5.95–10.50°E, the class tileset's `COVERAGE_BOUNDS`); swisstopo's
  covered 5.14–11.48°E, 45.40–48.23°N, across parts of five countries.
  Inside our rectangle but outside Switzerland the ground is hatched as no
  data.
- **Heavier downloads.** Class tiles are opaque PNGs with data in every
  pixel. Over Verbier (z12/2130/1455, z13/4260/2911, z14/8521/5822) ours
  measured 156, 133 and 100 kB against swisstopo's 20, 13 and 4.5 kB,
  where most of the raster is transparent. The download estimate charges
  88 KiB a tile (`TERRAIN_CLASS_BYTES_PER_TILE`); SNOW-1072 made them an
  opt-in ("Terrain data"), so a reader chooses the cost.
- **Requires `TERRAIN_CLASS_TILE_URL`.** The setting defaults to empty. An
  environment without it has no terrain filter and, now, no slope layer.

**What swisstopo's raster still does where ours does not reach.**

- It covers France, Italy, Austria and southern Germany inside its
  rectangle — the Chamonix side of Mont Blanc, the Aosta valley, the
  Arlberg — where we show nothing.
- It draws at every zoom from z0, so steep ground reads on a regional
  view.
- Its pyramid carries native detail to z16 (z17 is a server-side upscale,
  measured in SNOW-691). Ours stops at z14 and MapLibre overzooms it, so
  above z14 a class square is a z14 pixel, about 6.6 m at 46°N. Neither
  resolves features smaller than its source grid — about 10 m for theirs,
  measured off the served tiles; the 10 m window for ours.

**Consequences.**

- The Layers menu has no slope row; the slope layer's way in is
  "Terrain filter…" → "Colour by slope". The zoom control labels the z12
  tick "Slope classes" in that mode (`slope_classes` in
  `window.pwaZoomGates`).
- The legend's terrain-filter section shows the five class swatches and a
  "Slope angle" heading linking to `/help/#help-topic-slope` in the slope
  mode (`data-terrain-filter-key`).
- Offline, the slope layer paints from whatever class tiles the device
  holds: tiles viewed online (passive cache) and the tiles of an area
  downloaded with "Terrain data". A download without it no longer pins
  any slope shading.
- The credit is "swisstopo — swissALTI3D", on the source's attribution and
  on `/colophon/`, worded as resampled and derived by Snowdesk.
- `route_slope_core.js` and `terrain_filter_core.js` both read
  `self.pwaSlopeClassesCore` at parse time, so `slope_classes_core.js`
  loads first on every page that loads either.
