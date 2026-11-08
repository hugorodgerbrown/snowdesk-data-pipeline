---
name: the-winter-map-beyond-switzerland-is-restyled-osm
description: Winter map past the Swiss border is restyled OSM (map_beyond_switzerland, osmclip://, alpinerelief://), not raster or national basemaps
status: current
last-reviewed: 2026-11-08
---

# Past the Swiss border, the winter map is OpenStreetMap in its own style

**Decision.** SNOW-1460 (Hugo, 6 Nov 2026). Where swisstopo's winter map runs
out, the same style carries on: OpenFreeMap's OpenMapTiles vector tiles of
OpenStreetMap, drawn with the winter style's own layer definitions, over a
relief we paint from the elevation tiles the 3D view already uses. It is one
basemap in 2D and 3D, behind the `map_beyond_switzerland` flag, which is
released separately from `map_3d`. With both flags on, 3D opens anywhere in the
Alps: the Alpine gate, about 5.0–16.6°E and 43.7–48.3°N, which leaves out the
Pyrenees and Corsica.

The splice, which is idempotent and touches only the winter style, is
`plan(style)` in `static/js/beyond_switzerland_core.js`.
`static/js/map_beyond_switzerland.js` applies it on every `style.load` and
`styledata`. It adds three things:

1. **OpenStreetMap, restyled.** Every `base.vt` layer on an OpenMapTiles
   source-layer is copied onto the `beyond-osm` source, just above its
   original. swisstopo's `base.vt` uses the OpenMapTiles schema, so most copies
   change only their source. The rest get small class fixes in `fixLayer`:
   - peaks by height;
   - `is_route` widths;
   - a lift's subclass;
   - the wood, glacier and rock fills;
   - the Swiss and Liechtenstein borders left out (swisstopo draws them).
2. **Our relief.** `alpinerelief://` paints a neutral hillshade from the
   Terrarium tiles at `MAP_3D_DEM_TILE_URL`. It adds a grey rock tone on
   slopes over 40°, darker on the shadow side. Inside swisstopo's
   **detailed relief** (Switzerland, and the strip east to about 10.5°E where
   `relief.vt` has hachures from z12) the tile is erased, so the hachures
   are never covered. The layer sits above swisstopo's flat edge shading.
3. **Scree.** OpenStreetMap's `rock` landcover (not `bare_rock`) gets
   swisstopo's own scree pattern.

**The join is cut in the tiles, not filtered in the style.** `osmclip://` is
answered by a worker, `static/js/osm_clip_worker.js`, which uses
`static/js/osm_clip_core.js` and the vendored `vector_tile_kit.min.js` to clip
each OpenFreeMap tile to the outside of the **swisstopo extent** (CH and LI,
`static/js/swisstopo_extent.js`):

- a tile wholly inside comes back empty;
- a tile wholly outside passes through untouched;
- on a border tile, polygons keep their outside part with its holes, lines are
  cut at the border and only the outside pieces kept, and points inside are
  dropped.

A `within` filter cannot do this. It drops a whole feature that straddles the
border rather than cutting it, so forests and lakes would stop short of the
line or draw twice. The winter style's own layers are left unfiltered:
`base.vt` carries no features past the border (a z12 tile over St Anton is
209 bytes, against 166 KB over Valais), so they already stop there.

**Why.**

- **The raster join was tried and reverted.** SNOW-1457 (#1499) drew
  swisstopo's raster winter national map (`pixelkarte-farbe-winter`) past
  the border over the vector style, with Switzerland erased from each tile.
  #1502 reverted it, and Hugo chose this design on 6 Nov 2026. The raster
  reaches only a band past the border (Vorarlberg, most of Tyrol, Aosta,
  Lombardy, Trentino, half of South Tyrol and the Mont Blanc side of the
  French Alps, as #1499 listed). That falls short of every Alpine region,
  which is what this ticket is done by. A raster also draws its own labels
  and line weights beside the vector style. The extent, mask and protocol
  maths it proved out (`swisstopo_edge_core.js` at e2485a8) is the prior art
  for this one.
- **National basemaps meant four maps, not one.** The ticket first chose
  basemap.at for Austria, IGN Plan for France and OpenFreeMap elsewhere.
  Each has its own style, attribution, zoom band and licence. None is a
  winter map, and there is no Italian one at all
  ([boundaries-follow-the-basemap](boundaries-follow-the-basemap.md)). A
  border crossing would have changed the look of the map three times. One
  schema drawn in one style removes the seam rather than hiding it.
- **The relief is the winter map's character.** OpenMapTiles has no relief, and
  without one the ground past the border would read as flat. The DEM tiles are
  already hosted for 3D (SNOW-1444), so the relief costs no new origin.

**Consequences.**

- **Loaded only for the flag.** The five scripts load after the map bundle in
  `public/map.html`, never in it. A reader without the flag downloads none of
  them, and the workers start on the first tile request. Their URLs, and the
  URLs of the scripts they import, come from `#map`'s data attributes through
  `{% static %}`, because hashed static names in production mean a worker
  cannot work out a sibling's URL.
- **The 3D gate is handed over late.** `map_view_3d.js` starts on
  `terrain_filter_core.COVERAGE_BOUNDS`. `map_beyond_switzerland.js` hands it
  the Alpine ring through `pwaMapView.setCoverage`. A `?view=3d` boot waits
  for that ring under the existing two-second cap, and `map.js` leaves the
  boot downgrade to it. The terrain class tiles stay Swiss (SNOW-1459).
- **Attribution.** The OpenStreetMap credit arrives with a source added after
  `style.load`, so `map.js` clears its cached source ids on a `sourcedata`
  for an id it does not hold.
- **The service worker caches it like any basemap.** The `osmclip://` source
  names only its protocol, so the live style cannot teach the worker
  OpenFreeMap's host. `map.js` seeds the allowlist with the origin of
  `data-osm-style-url`, whose tiles share it. The elevation tiles are
  pinned only by an area download, as for 3D. Alpine area downloads are not
  part of this decision.
- **The boundary outlines are unchanged.** `BASEMAP_COUNTRIES` still declares
  the winter map as CH, so with the flag on, the outlines past the border are
  still drawn only on OpenFreeMap
  ([boundaries-follow-the-basemap](boundaries-follow-the-basemap.md)).
- **Cost on a border tile.** Clipping a dense border tile takes about 230 ms
  on the worker, off the main thread. A tile wholly outside the extent is
  passed on undecoded, and one wholly inside is never fetched, so a reader
  looking at Switzerland asks OpenFreeMap for nothing past low zooms.

**Tuning (staff).** Hugo asked on 8 Nov 2026 for Ötztal to be "a touch"
lighter, and for a way to try other shading. The relief's strengths now live
in `alpine_relief_core.TUNING_DEFAULTS`: rock from 40°, rock tone 0.42, shadow
0.36 and highlight 0.24 of full alpha (from 0.55, 0.45 and 0.24), with the rock
greys lifted to 170 and 126. A staff reader in the flag gets "Tune the relief"
in the map's "?" menu, which opens a form panel
(`public/partials/_map_relief_tuning_panel.html`,
`static/js/map_relief_tuning.js`) with four sliders. Each move repaints the
relief through `pwaBeyondSwitzerland.tune`, which posts a `tune` message to the
relief worker and gives the relief tiles a `?v=` key so MapLibre fetches them
again. The values are saved on that device under the localStorage key
`snowdesk.map.reliefTuning`, and are read back only when `#map` carries
`data-relief-tuning` (staff). Every other reader always gets the defaults. A
tuning worth keeping is made the default by changing `TUNING_DEFAULTS`.
