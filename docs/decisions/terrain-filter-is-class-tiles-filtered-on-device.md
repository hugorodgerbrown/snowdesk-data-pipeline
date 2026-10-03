---
name: terrain-filter-is-class-tiles-filtered-on-device
description: Terrain filter — class tiles (height, octant, slope band) painted on device via terrainfilter://; TERRAIN_CLASS_TILE_URL (SNOW-978)
status: current
last-reviewed: 2026-10-03
---

# The terrain filter is class tiles, filtered on the device

**Decision.** The map's terrain filter (aspect × slope × elevation) is
drawn from **terrain-class tiles** whose pixels are facts, not colours, and
the colour is made in the browser:

- Tiles: `TERRAIN_CLASS_TILE_URL`, published by the snowdesk-tiles repo
  (SNOW-987) at `tiles.snowdesk-data.info/terrain-class/v1/{z}/{x}/{y}.png`,
  z12–14, with `terrain-class/v1/tiles.json` carrying the encoding, zoom
  range, bounds and the "© swisstopo" attribution.
- **Pixel contract.** Opaque 256 px PNG, alpha always 255.
  R,G = height in whole metres, uint16 big-endian. B = `octant << 5 | band`
  — octant N=0, NE=1 … NW=7 with N covering 337.5–22.5° (the split
  `apps/core/geo.py`'s `octant_for` uses), band = `floor(slope / 5)` in
  0..17. **B = 254** is level ground (no aspect; R,G still the height).
  **B = 255** is no data (R = G = 0). A tile with no data, or a zoom
  outside 12–14, answers **HTTP 204**.
- `static/js/map.js` registers a `terrainfilter://` MapLibre protocol. Its
  handler fetches the class tile, decodes it once with
  `createImageBitmap(…, {premultiplyAlpha: 'none', colorSpaceConversion: 'none'})`
  and `getImageData`, keeps it in a 64-tile LRU, paints it for the current
  filter with `terrain_filter_core.paintTile`, and returns an `ImageBitmap`.
- The source's tile URL carries a hash of the filter (`?v=…`). A filter
  change calls `setTiles` with the new hash (debounced 150 ms), so MapLibre
  asks for every tile again and each answer comes from the LRU.
- The classifier returns a **palette index**, not a boolean, and receives
  the tile and pixel coordinates: 0 transparent (no match; level ground),
  1 the match tint, 2 the no-data hatch drawn from pixel coordinates.

**Why.**

- A filter has 8 aspects × 5 lower × 5 upper slope bounds × an open
  elevation band. Server-rendered tiles per filter would be a combinatorial
  cache and a request per change; a fact tile is one fetch per place, and
  any filter is a repaint with no network — which also works offline for
  ground already viewed.
- The server never learns what anyone filters for.
- The palette-index seam is for SNOW-979, which needs a four-state palette
  and per-region clipping; a boolean predicate would have to be replaced to
  get there.
- Hatching no-data rather than leaving it blank keeps the rule the slope
  layer learned the hard way: blank must never be readable as "does not
  match" where it means "not surveyed".

**Consequences.**

- `colorSpaceConversion: 'none'` is load-bearing: a browser that
  colour-managed the PNG would move every height and octant. Safari's
  fidelity here is the main risk to watch; the tiles are opaque, so
  premultiplication is moot.
- In MapLibre 4.7.1 a protocol handler that resolves with a falsy `data`
  leaves the tile pending forever, so "nothing here" is a transparent (or
  hatched) bitmap, never an empty answer.
- `setTiles` reloads the source, so a filter change can flash the layer
  briefly while tiles repaint.
- `COVERAGE_BOUNDS` in `terrain_filter_core.js` is the swissALTI3D extent
  as a rectangle; confirm it against the published `tiles.json` bounds. A
  204 inside it is hatched, outside it transparent.
- `TERRAIN_CLASS_TILE_URL` defaults to empty — the feature is off until an
  operator sets it, and the CSP origin is derived only when it is set
  (`optional_basemap_origin`).
- The class tiles' origin is already a registered service-worker basemap
  origin, so viewed tiles are cached like basemap tiles. Nothing extends or
  fights that.
- Local development uses the DEBUG-only synthetic tiles in
  `apps/public/dev_terrain_class.py`
  (`TERRAIN_CLASS_TILE_URL=http://localhost:3000/dev/terrain-class/v1/{z}/{x}/{y}.png`),
  which follow the same contract.
