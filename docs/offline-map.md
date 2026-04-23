# Offline map (SNOW-15)

The map page ships with a "Save offline" CTA that registers a service
worker and precaches everything needed to render `/map/` without a
network connection. POC status — the UX (progress chip, failure count,
cache management) will harden under follow-up tickets.

**Pieces**:
- `static/js/sw.js` — the service worker. Cache-first fetch, chunked
  precache driven by `postMessage`, versioned-cache cleanup on activate,
  synthetic 204 fallback for uncached tile requests while offline.
- `static/js/offline.js` — the client controller. Registers the SW,
  fetches the precache manifest, forwards it to the SW, relays progress
  back into the DOM.
- `public/views.py::serve_sw` — serves `/sw.js` from the root URL path
  (required for a root-scoped SW) with
  `Service-Worker-Allowed: /` and `Cache-Control: no-cache`.
  Route registered at the project root (`config/urls.py`), not under
  `public/urls.py`, since `/sw.js` must be a sibling of `/`.
- `public/api.py::offline_manifest_map` — builds the precache manifest.
  Zero DB queries. One outbound HTTP call to OpenFreeMap's TileJSON
  endpoint (`_fetch_vector_tile_template`) to resolve the current
  versioned vector-tile URL template — without this the precached keys
  wouldn't match the URLs MapLibre actually requests at runtime and the
  cache would be silently useless. Degrades to a hard-coded fallback
  template on OFM failure so the manifest always returns something.

**Cache version** — `_OFFLINE_MANIFEST_VERSION = "map-shell-v1"`. Bump
the suffix when the manifest contents change in a way that requires
clients to re-precache; the SW's `activate` handler deletes any cache
whose name starts with `map-shell-` and doesn't match the current
version.

**Manifest contents**:
- Django shell assets — `/map/` HTML, `output.css`, `map.css`, `map.js`,
  `offline.js`, favicon.
- The three map JSON endpoints (`today-summaries`, `resorts-by-region`,
  `regions.geojson`).
- MapLibre GL JS + CSS from CDN (version pinned to `_MAPLIBRE_VERSION`
  in `api.py` — must match `public/templates/public/map.html`).
- OpenFreeMap style JSON, TileJSON, sprites (1x + 2x), glyph PBFs for
  the Noto Sans fontstacks, plus vector tiles (z5–z10) and Natural
  Earth raster tiles (z5–z6) covering the Swiss bounding box
  `_SWISS_BBOX`.

**i18n** — `sw.js` has no translatable strings (never renders UI).
`offline.js` strings are flagged with `// i18n: translatable` comments
for the future JS-i18n phase; do not wrap them yet (same convention as
`map.js`).
