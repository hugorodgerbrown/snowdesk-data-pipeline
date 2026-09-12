/*
 * static/js/basemap_download_core.js — Pure tile-URL helpers for the
 * map's basemap download controls (SNOW-521 per-region rework;
 * SNOW-522 re-adds a client-side tile-math port for the custom-area
 * control).
 *
 * Dependency-free IIFE attaching ``self.pwaBasemapDownloadCore`` — same
 * idiom as ``basemap_cache_core.js`` (frozen export, no side effects).
 * Loaded on the page (window context) BEFORE ``static/js/map.js`` in
 * ``home.html``, unlike ``basemap_cache_core.js`` which is only ever
 * ``importScripts``'d into the service worker — this module has no SW
 * dependency, so it's a plain deferred ``<script>`` tag.
 *
 * SNOW-521 replaced the viewport-anchored "Download basemap" control
 * with per-region download: each region's tile coverage is precomputed
 * server-side (``regions.services.basemap_tiles`` — see that module's
 * docstring for the stored blob shape) and served over the API, so the
 * per-*region* download does no tile enumeration or byte-estimate
 * arithmetic of its own — ``rangesToTileURLs`` below still just turns
 * that server-computed data into URLs.
 *
 * SNOW-522 re-introduces the tile-index/byte-estimate arithmetic
 * client-side, deliberately, for the *custom-area* download: a
 * user-drawn bbox (the map panned/zoomed under a fixed framing
 * rectangle) is not precomputable server-side the way a region's fixed
 * boundary is — there is no stable ID to precompute against, and the
 * live "up to N MB" readout has to track every frame of that pan/zoom
 * with no network round-trip. ``lonLatToTile``/``tileRangesForBBox``/
 * ``tileCount``/``centreTile``/``buildBlob`` below are therefore a
 * DELIBERATE re-port of ``apps/regions/services/basemap_tiles.py``'s
 * pure functions of the same names (module docstring there has the full
 * algorithm rationale) — this is not drift back to a client that forgot
 * the math moved server-side; the two now legitimately coexist for two
 * different download shapes. Kept honest against the Python by a shared
 * golden vector asserted in both ``tests/js/test_basemap_download_core.js``
 * and ``tests/regions/services/test_basemap_tiles.py`` — see either
 * test's comment for the paired assertion. That shared vector is a
 * convention, not a mechanism: nothing fails the build if the two drift,
 * so treat any change to the Python tile math as also owed here.
 *
 * Coordinate convention: **(lon, lat) order**, matching
 * ``basemap_tiles.py``'s deliberate carve-out from the project's usual
 * (lat, lon) argument order — see that module's docstring. Kept
 * identical here so a reader porting between the two never has to
 * mentally swap axes.
 *
 * SNOW-583 clips a REGION download to its real boundary plus a margin
 * tile server-side (``apps.regions.services.basemap_tiles.
 * build_region_blob``), so a region's ``z`` entry is no longer always the
 * 4-int rectangle ``[xmin, xmax, ymin, ymax]`` — it is now
 * ``{"<y>": [xmin, xmax]}``, one row span per present row. The
 * CUSTOM-AREA blob ``buildBlob`` (below) produces is UNCHANGED and stays
 * rectangular — a user-drawn rectangle genuinely is one — and a response
 * served from the stale window (`max-age=300`,
 * `stale-while-revalidate=86400`, SNOW-902) can still hand back an
 * old-shape rectangle until its revalidation lands. ``zoomRows`` is the
 * one accessor every consumer of a blob's ``z`` routes through so both
 * shapes are handled in exactly one place.
 *
 * Public API — attached to ``self.pwaBasemapDownloadCore``:
 *
 *   zoomRows(zEntry)
 *     Normalises one zoom level's ``z`` entry — either shape — to
 *     ``{"<y>": [xmin, xmax]}``. See the SNOW-583 note above; every
 *     function below that walks a blob's tiles goes through this rather
 *     than assuming a rectangle.
 *   tileSources(spec) / tileSourceCount(spec) / tileSourcesKey(spec) /
 *   tileURLs(sources, z, x, y) / sourceScaledMb(mb, spec, count?)
 *     SNOW-843: the tile-source group. A basemap is one or more vector
 *     SOURCES, each with one or more hostnames MapLibre round-robins
 *     between per tile (``urls[(x + y) % urls.length]``) — so "the tile
 *     URL" is a list, not a string, and a download that stored a single
 *     template pinned a fraction of one layer. Every function below that
 *     builds or reads a tile URL routes through these. See ``tileSources``
 *     for the accepted shapes (a legacy template string included). SNOW-868 makes
 *     ``sourceScaledMb`` price a tile PER BASEMAP rather than at one
 *     global worst case, and recompute from the blob's ``count`` when the
 *     caller has it so the documents allowance is not multiplied by the
 *     source count.
 *   basemapKeyForTileSources(spec) / bytesPerTileForBasemap(key) /
 *   bytesPerTileForSources(spec)
 *     SNOW-868: which basemap a resolved tile-source spec belongs to, and
 *     what one of its tiles costs. Resolved from the source template's
 *     HOST rather than the basemap picker — see
 *     ``basemapKeyForTileSources`` for why that matters. The measurements
 *     behind the figures are in ``BYTES_PER_TILE_BY_BASEMAP``'s comment.
 *   rangesToTileURLs(spec, blob)
 *     Expands a full basemap_download blob's ``z`` tile-index ranges
 *     (as fetched from ``/api/region-basemap-tiles/?id=...`` OR produced
 *     locally by ``buildBlob`` below) into the full list of tile URLs —
 *     one per tile PER SOURCE, each at the host MapLibre will ask it
 *     from. Returns ``[]`` for an unresolvable ``spec`` or ``blob``, or a
 *     blob with no ``z`` ranges.
 *   lonLatToTile(lon, lat, z)
 *     Web Mercator ``[x, y]`` tile indices for ``(lon, lat)`` at zoom
 *     ``z`` — mirror of ``basemap_tiles.lon_lat_to_tile``. Not clamped
 *     to the valid ``[0, 2**z - 1]`` range; ``tileRangesForBBox`` clamps
 *     explicitly, matching the Python.
 *   tileRangesForBBox(bbox, minZ, maxZ)
 *     ``{"<z>": [xmin, xmax, ymin, ymax]}`` for every zoom in
 *     ``[minZ, maxZ]`` — mirror of ``basemap_tiles.tile_ranges``, same
 *     clamping.
 *   tileCount(ranges)
 *     Total tile count across every zoom level in ``ranges`` — mirror of
 *     ``basemap_tiles.tile_count``.
 *   centreTile(bbox, z)
 *     The tile at ``bbox``'s centre point, at zoom ``z`` — mirror of
 *     ``basemap_tiles.centre_tile``.
 *   buildBlob(bbox, minZ, maxZ, ceilingMb?)
 *     The full blob (``{band, count, mb, over_ceiling, centre_tile, z}``)
 *     — mirror of ``basemap_tiles.build_blob`` — produced in the SAME
 *     shape ``rangesToTileURLs`` already consumes, so a
 *     locally-built blob and a server-fetched one are interchangeable.
 *     ``ceilingMb`` is what ``over_ceiling`` is measured against; omitted,
 *     it falls back to ``DOWNLOAD_CEILING_MB``. Every page caller passes
 *     the DEVICE's ceiling (``map_basemap_downloads.js``'s
 *     ``basemapDeviceCeilingMb``) — see ``deviceCeilingMb`` below.
 *   budgetScaleForBBox(bbox, minZ, maxZ, sourceCount?, ceilingMb?,
 *   bytesPerTile?)
 *     The largest factor in ``[0, 1]`` by which ``bbox`` may be scaled
 *     about its centre while its download still fits under that same
 *     ceiling. Client-only — it has no ``basemap_tiles.py`` counterpart
 *     and needs none: the server never sizes a framing rectangle. Do NOT
 *     go looking for a Python twin to keep it honest against.
 *   deviceCeilingMb(estimate)
 *     The largest download THIS device may hold — the real ceiling, and
 *     the exact inverse of ``hasStorageHeadroom``. Client-only for the
 *     obvious reason: the server cannot see a device's storage.
 *   hasStorageHeadroom(estimate, mb)
 *     SNOW-568: whether a download of ``mb`` megabytes fits in the
 *     origin's remaining storage quota, with a safety margin. Client-only,
 *     like ``budgetScaleForBBox`` — no ``basemap_tiles.py`` counterpart.
 *   MICRO_BAND, WORST_CASE_BYTES_PER_TILE, BYTES_PER_TILE_BY_BASEMAP,
 *   DOWNLOAD_DOCUMENTS_MB, DOWNLOAD_CEILING_MB, STORAGE_HEADROOM_FACTOR
 *     Constants mirroring ``basemap_tiles.py``'s module-level constants
 *     of the same name (see there for the sizing rationale) — except
 *     ``STORAGE_HEADROOM_FACTOR``, which is client-only.
 *     ``DOWNLOAD_CEILING_MB`` is now only a fallback on this side: the
 *     ceiling a page applies comes from ``deviceCeilingMb``.
 *     ``BYTES_PER_TILE_BY_BASEMAP`` is client-only (SNOW-868) — the server
 *     has no view of the requester's basemap, exactly as it has none of
 *     the source count.
 *
 * SNOW-586: a third client-only group — area identity and the standing
 * download-budget arithmetic that replaced the pinned cache's old
 * entry-count FIFO trim (``static/js/sw.js``) with one Cache Storage
 * bucket per downloaded area (``snowdesk-basemap-pinned-<areaId>``) plus
 * a byte budget, so evicting one area can never perforate another's tiles
 * — see ``docs/decisions/per-area-pinned-basemap-caches.md`` for the full
 * rationale.
 *
 *   DOWNLOAD_BUDGET_MB
 *     The standing budget across every pinned area, in megabytes (500).
 *     Client-only — unlike ``MICRO_BAND``/``WORST_CASE_BYTES_PER_TILE``/
 *     ``DOWNLOAD_CEILING_MB`` above, this has NO ``basemap_tiles.py``
 *     counterpart and must never gain one: Cache Storage is per-browser,
 *     so a standing on-disk budget is a client concept the server has no
 *     stake in (mirrors ``STORAGE_HEADROOM_FACTOR``'s same carve-out).
 *     Overridable per device via the ``meta:app`` row
 *     ``basemap.budgetMb`` (``static/js/map.js``'s
 *     ``basemapDownloadBudgetBytes``) — SNOW-588's managed-downloads UI
 *     changes that row, not this constant.
 *   PINNED_CACHE_PREFIX
 *     The Cache Storage name prefix every per-area pinned bucket shares
 *     (``'snowdesk-basemap-pinned-'``) — mirrors
 *     ``static/js/sw.js``'s ``BASEMAP_PINNED_CACHE_PREFIX`` exactly (the
 *     two can't share a literal across a page/worker boundary, so this is
 *     kept honest by ``tests/js/test_basemap_download_core.js``'s
 *     round-trip assertion against ``pinnedCacheName``).
 *   CUSTOM_AREA_ID
 *     SNOW-586 formalised this as "the fixed area id for the one
 *     custom-area download" (``'custom'``). SNOW-635 lets more than one
 *     custom area exist, so this is no longer that — it is now one
 *     RESERVED legacy id: the area a pre-SNOW-635 device already had
 *     downloaded keeps it (and the ``snowdesk-basemap-pinned-custom``
 *     bucket it names) when ``map.js``'s lazy migration wraps it into the
 *     new ``basemap.customAreas`` array, because Cache Storage has no
 *     rename. Every custom area downloaded since is minted a fresh id by
 *     ``generateCustomAreaId`` below and is never ``CUSTOM_AREA_ID``
 *     itself. Code asking "is this id a custom area" must call
 *     ``isCustomAreaId``, never compare against this constant directly.
 *   generateCustomAreaId()
 *     SNOW-635: a fresh id for a NEW custom-area download
 *     (``'custom-' + a random UUID``), minted once per confirmed run so
 *     more than one custom area can be downloaded and kept at once — a
 *     second download used to collide with the first on this very id.
 *   isCustomAreaId(areaId)
 *     SNOW-635: whether ``areaId`` names a custom-area download — the
 *     legacy ``CUSTOM_AREA_ID`` or one of the ``generateCustomAreaId``
 *     family. Every caller that used to compare against ``CUSTOM_AREA_ID``
 *     directly to mean "is this a custom area" (``manageRows``,
 *     ``evictBasemapAreas``, …) goes through this instead, so the
 *     ``'custom-<uuid>'`` id format stays private to this module.
 *   areaIdForRegion(regionId)
 *     The area id for a region download (``'region-' + regionId``) —
 *     formalises the id a region-download area is keyed under; there was
 *     no such concept before this ticket, region downloads were
 *     identified only by ``region_id`` in the ``basemap.regions`` record.
 *   pinnedCacheName(areaId)
 *     The Cache Storage name for ``areaId``'s pinned bucket
 *     (``PINNED_CACHE_PREFIX + areaId``).
 *
 * SNOW-856 adds a third group — the SHARED BASE LAYER, the z0-7 tiles
 * every area on the device reads when the camera is zoomed out past a
 * download's z10 floor. It is not an area: one per BASEMAP, shared by
 * every area under it, and outliving any of them. It gets its own
 * ``base-`` id namespace and therefore its own pinned bucket, which is
 * what lets the worker's read path find it with no change at all.
 *
 *   areaIdForBaseLayer(basemapKey) / isBaseLayerAreaId(areaId) /
 *   baseLayerBasemapKey(areaId)
 *     The ``base-<basemapKey>`` id, its predicate, and its inverse — the
 *     third namespace beside ``region-`` and ``custom-``. Every surface
 *     that lists or evicts areas has to exclude these. SNOW-863 added the
 *     inverse so a bucket can name its own basemap without the
 *     ``meta:app`` record, which is written later and can be missing.
 *   intersectBBox(a, b)
 *     The overlap of two bboxes, or null.
 *   baseLayerBBox(cameraBBox, styleBounds) / baseLayerBand(basemapKey?) /
 *   baseLayerBlob(…) / baseLayerTileURLs(spec, …)
 *     The base layer's extent, band, blob and tile URLs. The camera bbox
 *     makes the extent sufficient and the style's declared bounds make it
 *     finite — see ``baseLayerBBox`` for why deriving it from the style
 *     alone breaks on the global default basemap. SNOW-868: the BAND is
 *     per basemap too (``BASE_LAYER_BANDS``), because what it costs to
 *     cover a country is not what it costs to cover the world — the last
 *     two arguments of ``baseLayerBlob``/``baseLayerTileURLs`` are the
 *     basemap key, and omitting it keeps the default band.
 *   isTileEntryURL(url) / baseLayerStaleEntries(entries, expectedTiles)
 *     SNOW-929: a base-layer bucket now holds the documents that draw its
 *     band as well as the band itself, so the re-banding check has to be
 *     able to tell a tile from a document and judge only the tiles. See
 *     ``baseLayerStaleEntries`` for why folding the documents into the
 *     expected set would let a provider bin a 21 MB band by renaming a
 *     sprite path.
 *   planEviction(areas, incoming, budgetBytes)
 *     Given the areas currently on disk and an incoming run, decides
 *     whether it fits the standing budget and, if not, which areas to
 *     evict (oldest ``savedAt`` first) to make it fit. See its own
 *     docstring for the full contract.
 *
 * SNOW-569 and the tile-grid rework that followed it add a second
 * client-only group — the geometry a download's on-map progress grid
 * needs (``bboxPolygon``, ``tileBounds``, ``gridZoomFor``,
 * ``tileGridPlan``). Like
 * ``budgetScaleForBBox`` these have no ``basemap_tiles.py`` counterpart
 * and need none: the server never draws anything. They live here rather
 * than in ``map.js`` because they are pure functions of geometry, which
 * makes them unit-testable without a MapLibre instance — everything in
 * ``map.js`` that touches them is a side effect on a live map.
 *
 *   bboxPolygon(bbox)
 *     A ``bbox`` as a GeoJSON Polygon — the custom-area download's
 *     equivalent of a region's own boundary.
 *   tileBounds(z, x, y)
 *     The ground one tile covers, as ``[west, south, east, north]`` —
 *     the inverse of ``lonLatToTile``, and the only place the grid's
 *     squares get their geometry.
 *   featureBBox(feature) / bboxesOverlap(a, b) / pointInBBox(lon, lat, bbox)
 *     SNOW-924: the rectangle group behind "what is inside this area".
 *     ``featureBBox`` moved here from ``map.js`` (SNOW-811's copy, which
 *     is now a one-line adapter for MapLibre's nested pair). The two
 *     predicates are INCLUSIVE at the edges, unlike ``intersectBBox``
 *     above — see ``bboxesOverlap`` for why a shared edge counts.
 *   bboxFromZoomRanges(z) / areaBBox(area)
 *     SNOW-924: the ground an area covers, from its stored record alone.
 *     A custom area is its ``bbox``; a region area has only ``z`` since
 *     SNOW-583, so its rectangle is derived from the tile rows.
 *   areaContentPlan({bbox, regionFeatures, weatherFeatures, days,
 *   weatherDetailTemplate})
 *     SNOW-924: the bulletin pages and weather sheets inside an area —
 *     the two sets too large to fetch wholesale. Read its docstring for
 *     the contract that makes a rectangle the right test, and for why a
 *     weather sheet is one UNDATED url per location.
 *   cachedTilesFromURLs(spec, cachedURLs, zoom)
 *     The tiles a cache actually holds, read back out of its URLs — the
 *     pure half of the "cached tiles" overlay. A tile counts only when
 *     every source holds it (SNOW-843).
 *   gridZoomFor(blob)
 *     Which single zoom level of a download's band to draw the grid at
 *     — the deepest, so a square is a real tile.
 *   tileGridPlan(spec, blob)
 *     The grid's cells AND the run's tile URLs ordered to fill them one
 *     at a time — see its docstring for why the ordering is the feature.
 *   blobFullyCached(spec, blob, cached)
 *     SNOW-570, widened by SNOW-583: whether EVERY tile in ``blob``'s own
 *     ``z`` (either shape, via ``zoomRows``) is present in ``cached`` —
 *     "is this download actually available offline?". Replaces the old
 *     ``downloadedIds(template, entries, cachedURLs)``/internal
 *     ``_bboxFullyCached`` pair now that the only two callers (the
 *     per-region and custom-area done-probes) each ask about ONE blob at
 *     a time rather than a whole list of regions — SNOW-583 dropped the
 *     "Downloaded areas" overlay's per-region ring (see
 *     ``docs/decisions/region-downloads-clip-custom-areas-dont.md``), the
 *     last caller that needed the list form.
 *   missingRenderDependencies(depURLs, cached)
 *     SNOW-844: which of an area's RENDER dependencies — its style JSON,
 *     each vector source's TileJSON, its sprite JSON+PNG at 1x and 2x —
 *     are absent from ``cached``. The second half of "is this download
 *     actually available offline?"; ``blobFullyCached`` above is the
 *     first, and it is deliberately left alone rather than widened. Two
 *     small pure predicates beat one big one here because the two answers
 *     drive DIFFERENT states: complete tiles with a missing sprite is not
 *     the same condition as missing tiles, and the surfaces say so
 *     differently ('incomplete' + repair vs 'idle' + download).
 */

(function () {
  'use strict';

  // Mirrors apps/regions/services/basemap_tiles.py::MICRO_BAND — the
  // custom-area download uses the same zoom band as the region download
  // (z10-14), so the two produce directly-comparable size estimates.
  var MICRO_BAND = [10, 14];

  // Mirrors apps/regions/services/basemap_tiles.py::WORST_CASE_BYTES_PER_TILE
  // — calibrated against seven real max-size custom-area downloads
  // (SNOW-631), and re-checked 2026-09-07 against a real 253-tile download
  // that averaged 34 KB a tile. See that constant's comment for why an
  // estimate-vs-actual gap of 3x turned out not to be this number.
  var WORST_CASE_BYTES_PER_TILE = 50 * 1024;

  // SNOW-868: what a tile actually costs, PER BASEMAP. The single figure
  // above was calibrated on OpenFreeMap and then spent on every style, so
  // the "up to N MB" the UI shows was not an upper bound on swisstopo at
  // all — a Ybrig region download reported 59.9 KB a tile against an
  // estimate priced at 50 KB, and the promise the readout makes is that
  // the real number comes in UNDER it.
  //
  // Measured 2026-09-08 by fetching real tiles, not modelled. The
  // project's own ``build_region_blob`` expanded all 149 CH regions into
  // their real 36,511 tile coordinates; 400 of those were sampled at
  // random and each fetched from swisstopo (both sources) and OpenFreeMap
  // — 1,200 requests, zero failures, and the true geographic and per-zoom
  // mix (z10 464, z11 956, z12 2,452, z13 7,412, z14 25,227) rather than a
  // handful of town-centre tiles, which are the densest in any area.
  //
  //   source            mean     p50     p95      max
  //   openfreemap       25.2 KB  17.1    76.0     186.3
  //   swisstopo base    48.6 KB  42.9    98.8     190.2
  //   swisstopo relief  73.1 KB  38.2    236.0    572.3
  //   swisstopo/source  60.8 KB  40.8    171.0    572.3
  //
  // The Ybrig download trace says 59.9 KB per swisstopo tile. The sample
  // says 60.8. Two independent methods agreeing to 1.5% is what made a
  // full 4.5 GB download of every region unnecessary.
  //
  // What the constant has to bound is a DOWNLOAD's mean, not the fattest
  // tile: a download averages hundreds of tiles, so only small areas carry
  // real variance. Bootstrapped from the sample at the real CH region
  // sizes (min 56 tiles, median 181, max 2,816), the worst-region p99 is
  // 34.7 KB for OpenFreeMap (at 56 tiles; 30.3 at 181, 26.4 at 2,816) and
  // 85.5 KB per source for swisstopo (74.1 / 63.9). Each constant below
  // clears its own p99. That is also why 72 KB was REJECTED for swisstopo
  // even though it sits well above the 60.8 KB mean — a small region would
  // exceed it about 1% of the time, and "up to" would be false again.
  //
  // OpenFreeMap is deliberately ABSENT from ``BASEMAP_HOST_KEYS`` below
  // even though it has an entry here: its origin is deployment-dependent
  // (``OPENFREEMAP_STYLE_URL``, config/settings/base.py, and staging
  // self-hosts at tiles.snowdesk-data.info — docs/runbooks/
  // self-hosted-tiles.md), so there is no host to match on. It resolves
  // through the fallback instead, and the fallback's 50 KB IS its measured
  // value. Do not "complete" the host table with an origin staging does
  // not use.
  var BYTES_PER_TILE_BY_BASEMAP = {
    // Measured, see above: mean 25.2 KB, worst-region p99 34.7 KB.
    openfreemap_liberty: 50 * 1024,
    // Measured, see above: 60.8 KB per source over two sources, worst-region
    // p99 85.5 KB per source. Charged per source by ``sourceScaledMb``.
    //
    // 96 KB clears that p99 by 12%, and the margin is DELIBERATE, not a
    // rounded measurement: the readout promises an upper bound, and a
    // re-measurement is free to nudge p99 up a little. Anything that
    // re-measures should move this constant to keep a comparable margin
    // rather than shave it to the new p99 — the test asserts only
    // ``> 85.5 * 1024``, so eroding the headroom to nothing stays green
    // right up until the promise is false again.
    swisstopo_winter: 96 * 1024,
    // Same tile sources as winter — the two styles differ only in paint.
    swisstopo_light: 96 * 1024,
    // PROVISIONAL. Only CH region geometry exists in the local fixture, so
    // there was no French region set to run the stratified sample against.
    // This is a crude four-point sample (weighted mean ~52 KB) scaled by the
    // same worst-region factor the CH bootstrap produced. Generous rather
    // than accurate; nothing is blocked by it, because it already sits well
    // above the measurement. Replace it by re-running the same stratified
    // method against real FR region geometry, or against a real download
    // trace.
    ign_plan: 96 * 1024,
    // PROVISIONAL, on the same footing as ign_plan: a crude four-point
    // sample (~85 KB, with a fat z10 tail — one tile came back at 782 KB)
    // scaled by the CH worst-region factor. Replace it the same way.
    basemap_at: 144 * 1024,
  };

  // SNOW-868: host fragment → the basemap key whose per-tile figure prices
  // it. Matched against a tile source's URL TEMPLATE, never against the
  // picker — see ``basemapKeyForTileSources`` for why that distinction is
  // load-bearing. OpenFreeMap is deliberately absent; see the table above.
  var BASEMAP_HOST_KEYS = [
    ['.geo.admin.ch', 'swisstopo_winter'],
    ['data.geopf.fr', 'ign_plan'],
    ['wien.gv.at', 'basemap_at'],
  ];

  // Mirrors apps/regions/services/basemap_tiles.py::DOWNLOAD_DOCUMENTS_MB —
  // the style, sprite, TileJSON and promoted glyphs a run writes into the
  // bucket beside its tiles, which the estimate used to ignore entirely.
  // An allowance rather than a prediction: see that constant's comment for
  // the measured split and for why the glyph half cannot be predicted from
  // a bbox.
  var DOWNLOAD_DOCUMENTS_MB = 2;

  // The FALLBACK ceiling, used only when the device cannot say what it can
  // hold. The real ceiling is ``deviceCeilingMb`` below: how large one
  // download may be is a question about the device it lands on, so a
  // constant is the wrong kind of answer — it either refuses a download a
  // phone had room for, or lets one through that it did not.
  //
  // Mirrors apps/regions/services/basemap_tiles.py::DOWNLOAD_CEILING_MB,
  // which is that module's only ceiling: the server has no view of a
  // device, so the ``over_ceiling`` flag it stores on a region row is
  // advisory and the client decides for itself.
  var DOWNLOAD_CEILING_MB = 200;

  // SNOW-856: the zoom band the SHARED BASE LAYER covers. An area
  // download pins ``MICRO_BAND`` (z10-14) over its own ground; this pins a
  // shallow band over the whole map, once per basemap, for every area to
  // share.
  //
  // The gap this closes: the map's camera goes down to z4
  // (``MIN_ZOOM``, static/js/map.js), a download's floor is z10, and
  // nothing precached a basemap tile at any zoom
  // (``PRECACHE_URLS = [OFFLINE_FALLBACK, RESET_SCRIPT]``, static/js/sw.js).
  // So an offline reader who zoomed out fell off the edge of every area
  // they owned. That was invisible until SNOW-854 closed the
  // unclassified-cross-origin leak — before it, those tiles were quietly
  // fetched over a connection the user had told the app not to spend, and
  // the map drew.
  //
  // **The band is a function of the BASEMAP'S EXTENT, not a global
  // constant (SNOW-868).** This is the DEFAULT — what an unknown basemap
  // gets — and it is OpenFreeMap's, because a band's cost is set by how
  // much ground the style covers, and the default basemap covers the
  // world while the other three cover one country each.
  //
  // The history reads like a flip-flop and is not one. SNOW-856 shipped
  // z0-9, so the base layer would abut ``MICRO_BAND``'s z10 floor with no
  // gap. SNOW-863 trimmed it to z0-7 for everything, having measured the
  // default basemap. SNOW-868 measured the OTHER THREE and found the
  // ruling was right for OpenFreeMap and was never a statement about a
  // national style. z8+z9 over each basemap's own extent:
  //
  //   openfreemap_liberty   33.4 + 88.0 MB  = 121 MB   (140 + 486 tiles,
  //                                                     Alps-wide)
  //   swisstopo (both srcs)  1.4 +  3.0 MB  = 4.4 MB   (CH)
  //   ign_plan               2.1 +  2.3 MB  = 4.4 MB   (FR)
  //   basemap_at            ~3.2 + ~4.1 MB  = ~7.3 MB  (AT)
  //
  // The OpenFreeMap figure independently reproduces the 123 MB the
  // SNOW-863 comment carried, which is what makes the other three
  // trustworthy. 121 MB is a quarter of the standing 500 MB budget spent
  // before a single area is downloaded; 4.4 MB is not a price, it is a
  // rounding error, and it buys the seam away for every Swiss, French and
  // Austrian reader.
  //
  // **Do not restore the gap for the DEFAULT basemap on tidiness
  // grounds.** The two-level gap costs nothing the reader can see there:
  // MapLibre renders the nearest cached ancestor for a tile it does not
  // hold (``findLoadedParent``), so z8 and z9 draw from the stored z7 tile
  // — softer, never blank, which is the whole promise. That same mechanism
  // is what makes the map draw coarsely outside a download's ground
  // (SNOW-856's accepted trade), so this is not a new behaviour to reason
  // about, just the same one over two more levels. Anything below z10 is
  // context; detail is the area download's job, and 121 MB is not worth
  // paying to be able to say the numbers touch.
  //
  // No ``basemap_tiles.py`` counterpart, and it needs none: the base
  // layer's extent is the CAMERA's, which is a client-side constraint the
  // server has no view of.
  var BASE_LAYER_BAND = [0, 7];

  // SNOW-868: the band per basemap, defaulting to ``BASE_LAYER_BAND``
  // above for anything not listed. The national styles close the seam
  // because closing it costs them 4.4 to 7.3 MB; OpenFreeMap is listed
  // explicitly at the default rather than left implicit, so a reader can
  // see the ruling was made for it and not merely omitted.
  var BASE_LAYER_BANDS = {
    openfreemap_liberty: [0, 7],
    swisstopo_winter: [0, 9],
    swisstopo_light: [0, 9],
    ign_plan: [0, 9],
    basemap_at: [0, 9],
  };

  /**
   * The base layer's zoom band for one basemap (SNOW-868).
   *
   * @param {string} [basemapKey] A ``BASEMAP_STYLES`` key. Omitted or
   *   unknown yields ``BASE_LAYER_BAND``, the default — the conservative
   *   direction, since the default is the CHEAPEST band and an unknown
   *   basemap is one whose extent nothing here has measured.
   * @returns {number[]} ``[minZ, maxZ]``.
   */
  function baseLayerBand(basemapKey) {
    var band = BASE_LAYER_BANDS[basemapKey];
    return band || BASE_LAYER_BAND;
  }


  // Kilometres in a degree of latitude. Equirectangular, and deliberately
  // the same figure `map_drop_zone.js` draws its ring with — the circle on
  // screen and the tiles fetched under it have to agree with each other
  // more than either has to be geodesic.
  var KM_PER_DEGREE_LAT = 111.32;

  // SNOW-568: the fraction of the origin's REMAINING storage quota a
  // single download may claim. Client-only — no basemap_tiles.py twin.
  // Half leaves room for the shell cache, the passive basemap cache the
  // user's ordinary browsing keeps filling, IndexedDB, and the mutation
  // queue, none of which stop growing because a download is in flight.
  var STORAGE_HEADROOM_FACTOR = 0.5;

  // SNOW-586: the standing budget across every pinned area, in megabytes.
  // Client-only — see the module header's "third client-only group" note
  // for why this must never gain a basemap_tiles.py counterpart.
  // Overridable per device via meta:app's basemap.budgetMb
  // (static/js/map.js's basemapDownloadBudgetBytes); SNOW-588 changes
  // that row, not this constant.
  var DOWNLOAD_BUDGET_MB = 500;

  // SNOW-586: the Cache Storage name prefix every per-area pinned bucket
  // shares. Mirrored as FOUR separate literals — this one, static/js/sw.js's
  // BASEMAP_PINNED_CACHE_PREFIX, static/js/map.js's own
  // BASEMAP_PINNED_CACHE_PREFIX, and static/js/map_layer_sync_status.js's
  // PINNED_BASEMAP_CACHE_PREFIX — because a page script, a worker script,
  // and this dependency-free core can't share one constant across those
  // load-context boundaries. tests/js/test_basemap_download_core.js's
  // round-trip assertion (areaIdForRegion / pinnedCacheName) only checks
  // THIS module's own internal consistency — that pinnedCacheName always
  // returns PINNED_CACHE_PREFIX + areaId — it does not and cannot compare
  // against the other three files' copies. Cross-file agreement is a
  // review discipline, not an enforced mechanism, the same convention
  // basemap_tiles.py's shared golden vector documents for the Python↔JS
  // tile math (see that module's own comment): a reviewer changing one
  // copy of this prefix is responsible for checking whether the other
  // three need the same change.
  var PINNED_CACHE_PREFIX = 'snowdesk-basemap-pinned-';

  // SNOW-586: originally "the fixed area id for the one custom-area
  // download". SNOW-635 lets more than one exist, so this is now a
  // RESERVED legacy id only — the area a pre-SNOW-635 device already had
  // downloaded keeps it (see this module's header for why: Cache Storage
  // has no rename). Never mint this for a NEW area (generateCustomAreaId
  // never returns it) and never compare an id against it directly to mean
  // "is this a custom area" (use isCustomAreaId).
  var CUSTOM_AREA_ID = 'custom';

  /**
   * A fresh id for a new custom-area download.
   *
   * SNOW-635: every confirmed custom-area download mints its own id rather
   * than sharing the single ``CUSTOM_AREA_ID`` — that single id is why a
   * second download silently replaced the first before this ticket. The
   * ``crypto.randomUUID`` fallback mirrors ``mutation_queue.js``'s
   * ``_mintIdempotencyKey`` (and, ultimately, ``db.js``'s ``_randomHex``)
   * for the rare runtime without it.
   *
   * @returns {string}
   */
  function generateCustomAreaId() {
    try {
      if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return 'custom-' + crypto.randomUUID();
      }
      var bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      var out = '';
      for (var i = 0; i < bytes.length; i++) {
        out += bytes[i].toString(16).padStart(2, '0');
      }
      return 'custom-' + out;
    } catch (_e) {
      // No crypto at all — a low-entropy but still-unique-enough value so
      // the download keeps working rather than throwing.
      return (
        'custom-' + Date.now().toString(16) + '-' + Math.floor(Math.random() * 1e9).toString(16)
      );
    }
  }

  /**
   * Whether ``areaId`` names a custom-area download.
   *
   * SNOW-635: true for the legacy ``CUSTOM_AREA_ID`` (a pre-SNOW-635
   * device's one area, migrated in place — see ``CUSTOM_AREA_ID``'s own
   * comment) OR any id ``generateCustomAreaId`` mints. Every caller that
   * used to test ``areaId === CUSTOM_AREA_ID`` to mean "is this a custom
   * area" goes through this instead, so the ``'custom-<uuid>'`` shape
   * stays private to this module — mirrors how ``areaIdForRegion``'s
   * ``'region-'`` prefix is never parsed out of an id elsewhere either.
   *
   * @param {string} areaId
   * @returns {boolean}
   */
  function isCustomAreaId(areaId) {
    return areaId === CUSTOM_AREA_ID || (typeof areaId === 'string' && areaId.indexOf('custom-') === 0);
  }

  // SNOW-847: the glyph ranges a download pins, for every fontstack the
  // style declares. A FIXED set, not one derived from the style or probed
  // against the host — both alternatives were measured on 2026-09-09 and
  // neither works:
  //
  //   - Derivation from the STYLE is impossible without decoding the
  //     area's own vector tiles, since a style says which fonts its labels
  //     use but never which codepoints.
  //   - Derivation from the HOST is impossible because every one of the
  //     four basemaps answers HTTP 200 for all 256 ranges. An unpublished
  //     range is a tiny stub (29-45 bytes; 627 on OpenFreeMap), not a 404,
  //     so only the BODY SIZE distinguishes "has glyphs" from "has none" —
  //     and finding that out costs the whole download. OpenFreeMap's
  //     ``Noto Sans Regular`` is 33.7 MB across the full space (it carries
  //     CJK), so "fetch what the host publishes" is ~100 MB for that style
  //     against a 37 MB region download.
  //
  // So: the blocks Alpine labels actually draw from, fixed here. Latin-1
  // through Latin Extended-B and combining diacritics (0-1023), Latin
  // Extended Additional (7680-7935), and General Punctuation through
  // Mathematical Operators (8192-8959) — the last of which is what the
  // 2026-09-08 staging trace caught missing as
  // ``Frutiger Neue Condensed Regular/8192-8447``: en dashes, primes,
  // vulgar fractions and arrows are ordinary furniture on a map, and the
  // ``0-1023`` set this ticket originally proposed would not have fetched
  // them.
  //
  // Measured cost of this set across EVERY fontstack each style declares:
  // swisstopo 0.81 MB, IGN 1.49 MB, OpenFreeMap 1.83 MB, basemap.at
  // 2.11 MB. Against a 37 MB region download that is 2-6%. Widening it to
  // cover Cyrillic, Greek Extended and the geometric-shape blocks was
  // costed at 4.12 MB worst case and declined: an Alpine region download
  // renders Latin-script labels, so those bytes buy coverage nothing on
  // the page asks for.
  //
  // A range a style's fonts do not publish still costs one request and the
  // stub's few dozen bytes. That is the price of not decoding tiles, and
  // it is paid once per download.
  var GLYPH_RANGES = Object.freeze([
    '0-255',
    '256-511',
    '512-767',
    '768-1023',
    '7680-7935',
    '8192-8447',
    '8448-8703',
    '8704-8959',
  ]);

  // MapLibre style-expression operators. An array whose head is one of
  // these is an EXPRESSION, so its head is an operator name and its tail
  // holds operands — never a fontstack. Used by ``collectFontstacks``
  // below to tell ``["Frutiger Neue Regular"]`` (a fontstack) from
  // ``["get", "class"]`` (a property read whose operand is NOT a font).
  //
  // Getting this wrong in either direction is silent: too narrow and real
  // fontstacks are missed, so those labels ship unlabelled offline — the
  // failure this ticket exists to fix. Too wide and property names are
  // fetched as if they were fonts, which 404s harmlessly but pollutes the
  // record's deps with URLs no probe can ever satisfy, so the area reads
  // ``incomplete`` for good.
  var STYLE_EXPRESSION_OPS = Object.freeze([
    'array', 'at', 'boolean', 'case', 'coalesce', 'collator', 'concat', 'downcase',
    'feature-state', 'format', 'geometry-type', 'get', 'has', 'id', 'image', 'in',
    'index-of', 'interpolate', 'interpolate-hcl', 'interpolate-lab', 'length', 'let',
    'literal', 'match', 'number', 'number-format', 'object', 'properties',
    'resolved-locale', 'slice', 'step', 'string', 'to-boolean', 'to-color',
    'to-number', 'to-string', 'typeof', 'upcase', 'var', 'zoom',
    'all', 'any', '!', '==', '!=', '<', '<=', '>', '>=',
  ]);

  /**
   * Collect font names out of one ``text-font`` value.
   *
   * SNOW-847: ``text-font`` is not always a plain array of names. In the
   * swisstopo winter style two of the five fontstacks appear ONLY inside a
   * ``["match", ["get", "class"], …]`` expression, as
   * ``["literal", ["Frutiger Neue Condensed Medium"]]`` — a scan that reads
   * only array-valued ``text-font`` misses them, and the towns and lake
   * elevations they label ship without glyphs.
   *
   * The walk is deliberately conservative about what counts as a font:
   * a ``literal``'s array payload, or a plain all-string array that is not
   * itself an expression. Anything else is descended into rather than
   * collected, so operator names and property reads never reach the URL
   * list.
   *
   * @param {*} node A ``text-font`` value or any sub-expression of one.
   * @param {Set<string>} out Accumulator, mutated in place.
   * @returns {void}
   */
  function collectFontstacks(node, out) {
    // A BARE string is never a fontstack here. It is a `match` label, or a
    // `get`'s property name — collecting it is how an earlier version of
    // this walk returned `class`, `town` and `lake_elevation` alongside the
    // real fonts. A fontstack is always an ARRAY of names, whether written
    // literally or wrapped in `["literal", …]`; the one place a bare string
    // is accepted is a top-level `text-font`, handled by `styleFontstacks`.
    if (!Array.isArray(node) || node.length === 0) return;
    var head = node[0];
    var i;
    if (head === 'literal') {
      for (i = 1; i < node.length; i += 1) {
        var payload = node[i];
        if (Array.isArray(payload)) {
          for (var j = 0; j < payload.length; j += 1) {
            if (typeof payload[j] === 'string') out.add(payload[j]);
          }
        } else if (typeof payload === 'string') {
          out.add(payload);
        }
      }
      return;
    }
    if (typeof head === 'string' && STYLE_EXPRESSION_OPS.indexOf(head) !== -1) {
      for (i = 1; i < node.length; i += 1) collectFontstacks(node[i], out);
      return;
    }
    var allStrings = true;
    for (i = 0; i < node.length; i += 1) {
      if (typeof node[i] !== 'string') {
        allStrings = false;
        break;
      }
    }
    if (allStrings) {
      for (i = 0; i < node.length; i += 1) out.add(node[i]);
      return;
    }
    for (i = 0; i < node.length; i += 1) collectFontstacks(node[i], out);
  }

  /**
   * Every fontstack a style's layers can ask for, sorted.
   *
   * SNOW-847. Sorted so the URL list a run fetches — and therefore the
   * ``deps`` it records — is stable across runs of the same style, which
   * is what lets a later probe compare the two by value.
   *
   * @param {Object | null | undefined} style A MapLibre style object, as
   *   ``map.getStyle()`` returns it.
   * @returns {string[]} Empty for a style with no layers or no labels.
   */
  function styleFontstacks(style) {
    var out = new Set();
    var layers = style && Array.isArray(style.layers) ? style.layers : [];
    for (var i = 0; i < layers.length; i += 1) {
      var layout = layers[i] && layers[i].layout;
      if (!layout) continue;
      var textFont = layout['text-font'];
      if (textFont === undefined || textFont === null) continue;
      if (typeof textFont === 'string') {
        // Not valid MapLibre, but a style that ships one would otherwise
        // lose the font silently. Only accepted at the TOP level — see
        // `collectFontstacks` for why a bare string inside an expression is
        // not a font.
        out.add(textFont);
        continue;
      }
      collectFontstacks(textFont, out);
    }
    return Array.from(out).sort();
  }

  /**
   * Every glyph URL a download should pin for ``style``.
   *
   * SNOW-847: the cross product of the style's declared fontstacks and
   * ``GLYPH_RANGES``, substituted into the style's own ``glyphs`` template.
   * Fed into the run's URL list so glyphs are FETCHED by the download
   * rather than promoted out of the passive cache — see ``GLYPH_RANGES``
   * for why the range set is fixed, and ``sw.js``'s ``_promoteGlyphs`` for
   * what promotion still covers.
   *
   * The fontstack is percent-encoded, matching what MapLibre requests: the
   * names carry spaces, and a pinned entry keyed on an unencoded URL is one
   * nothing will ever look up.
   *
   * @param {Object | null | undefined} style A MapLibre style object.
   * @returns {string[]} Empty when the style declares no ``glyphs``
   *   template or no fontstacks — both of which mean "nothing to pin",
   *   never "not yet known".
   */
  function glyphURLs(style) {
    var urls = [];
    var template = style && typeof style.glyphs === 'string' ? style.glyphs : '';
    if (!template) return urls;
    var stacks = styleFontstacks(style);
    for (var i = 0; i < stacks.length; i += 1) {
      var encoded = encodeURIComponent(stacks[i]);
      for (var j = 0; j < GLYPH_RANGES.length; j += 1) {
        urls.push(template.replace('{fontstack}', encoded).replace('{range}', GLYPH_RANGES[j]));
      }
    }
    return urls;
  }

  /**
   * Every slope-angle raster URL a download should pin for ``blob``.
   *
   * SNOW-692: the slope overlay shipped with opportunistic offline support
   * only — its tiles landed in the passive, FIFO-trimmed ``BASEMAP_CACHE``
   * as they were viewed, so terrain the user looked at online might still
   * be there offline, and might not. For a layer whose whole purpose is
   * answering "how steep is that" while standing in front of it with no
   * signal, that is the wrong end state.
   *
   * Same ground and same band as the basemap tiles, so this walks the SAME
   * server-computed blob rows ``rangesToTileURLs`` does rather than doing
   * tile maths of its own. Two things make it not simply a second call to
   * that function:
   *
   *   - **The layer's own rectangle.** The raster is a multi-country DEM
   *     composite clipped to a rectangle that is NOT Switzerland plus a
   *     buffer (``COVERAGE_BOUNDS``, slope_overlay_core.js). A region
   *     straddling the edge must DROP the tiles outside it rather than
   *     fetch them and take the failures into the run's `failed` count,
   *     which would fail an otherwise complete download.
   *   - **The layer's own zoom ceiling.** The service answers HTTP 400
   *     past z17 and its real detail stops at z16, so anything deeper is
   *     skipped. The download band tops out at z14 today, making this
   *     headroom rather than a live constraint — it is enforced anyway
   *     because the two ceilings are independent and nothing else would
   *     notice if the band moved.
   *
   * @param {string} template An XYZ template with ``{z}``/``{x}``/``{y}``.
   * @param {Object | null | undefined} blob The download blob, for its
   *   ``z`` row spans.
   * @param {number[] | null | undefined} bounds ``[west, south, east,
   *   north]`` the raster covers. Omitted means no clip.
   * @param {number} maxZoom Deepest zoom to request, inclusive.
   * @returns {string[]} Empty when the template, blob or rows are missing —
   *   the run then pins no slope tiles, exactly as before this ticket.
   */
  function slopeTileURLs(template, blob, bounds, maxZoom) {
    var urls = [];
    if (!template || !blob || !blob.z) return urls;
    var ceiling = typeof maxZoom === 'number' ? maxZoom : Infinity;
    var rect = Array.isArray(bounds) && bounds.length === 4 ? bounds : null;
    var zKeys = Object.keys(blob.z);
    for (var zi = 0; zi < zKeys.length; zi += 1) {
      var z = Number(zKeys[zi]);
      if (!Number.isFinite(z) || z > ceiling) continue;
      var rows = zoomRows(blob.z[zKeys[zi]]);
      var yKeys = Object.keys(rows);
      for (var yi = 0; yi < yKeys.length; yi += 1) {
        var y = Number(yKeys[yi]);
        var span = rows[yKeys[yi]];
        for (var x = span[0]; x <= span[1]; x += 1) {
          if (rect && !_tileIntersectsBBox(z, x, y, rect)) continue;
          urls.push(
            template
              .replace('{z}', String(z))
              .replace('{x}', String(x))
              .replace('{y}', String(y)),
          );
        }
      }
    }
    return urls;
  }

  /**
   * Whether a tile's ground overlaps ``bbox``.
   *
   * SNOW-692. Overlap, not containment: a tile straddling the raster's edge
   * carries real data on the inside and has to be fetched. Touching edges
   * count, matching ``coversPoint``'s inclusive rectangle in
   * slope_overlay_core.js — a tile flush against the boundary is one the
   * service still answers.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @param {number[]} bbox ``[west, south, east, north]``.
   * @returns {boolean}
   */
  function _tileIntersectsBBox(z, x, y, bbox) {
    var tile = tileBounds(z, x, y);
    return tile[0] <= bbox[2] && tile[2] >= bbox[0] && tile[1] <= bbox[3] && tile[3] >= bbox[1];
  }

  /**
   * The area id for a region download.
   *
   * SNOW-586: formalises "area" as the unit a pinned cache bucket and a
   * budget record are both keyed on — region downloads previously had no
   * such id, only a bare ``region_id``.
   *
   * @param {string} regionId
   * @returns {string}
   */
  function areaIdForRegion(regionId) {
    return 'region-' + regionId;
  }

  /**
   * The Cache Storage name for ``areaId``'s pinned bucket.
   *
   * @param {string} areaId
   * @returns {string}
   */
  function pinnedCacheName(areaId) {
    return PINNED_CACHE_PREFIX + areaId;
  }

  /**
   * The area id for a basemap's shared base layer (SNOW-856).
   *
   * A THIRD id namespace beside ``region-`` and ``custom-``, and the
   * reason it is one rather than a flag on an existing area: the base
   * layer is not an area. It is shared by every area on the device, it
   * covers ground no area asked for, and it must survive the eviction of
   * the area whose download happened to fetch it. Giving it its own
   * ``PINNED_CACHE_PREFIX`` bucket buys all of that for free — the
   * worker's ``_searchPinnedBuckets`` walks every bucket under the
   * prefix, so the offline READ path needs no change whatsoever.
   *
   * Keyed by basemap rather than being a single global bucket because
   * the tiles are a specific style's: a device holding areas under both
   * swisstopo and OpenFreeMap needs both base layers, and neither can
   * answer for the other.
   *
   * @param {string} basemapKey A ``settings.BASEMAP_STYLES`` key.
   * @returns {string}
   */
  function areaIdForBaseLayer(basemapKey) {
    return 'base-' + basemapKey;
  }

  /**
   * Whether ``areaId`` names a base layer rather than a user's area.
   *
   * Every surface that lists, sizes, evicts or reconciles "the areas on
   * this device" has to be able to tell the two apart — a base layer is
   * real bytes the user is spending, but it is not a download they chose
   * and must never be offered for deletion or picked as an eviction
   * candidate. Mirrors ``isCustomAreaId``'s shape so the ``'base-'``
   * prefix stays private to this module.
   *
   * @param {string} areaId
   * @returns {boolean}
   */
  function isBaseLayerAreaId(areaId) {
    return typeof areaId === 'string' && areaId.indexOf('base-') === 0;
  }

  /**
   * The basemap a base-layer area id belongs to (SNOW-863).
   *
   * The inverse of ``areaIdForBaseLayer``, and the only sanctioned way to
   * read a key back out of an id — the ``'base-'`` prefix stays private to
   * this module, exactly as ``'region-'`` and ``'custom-'`` do.
   *
   * It exists because the BUCKET has to be able to describe itself. A
   * base layer's ``meta:app`` record is written by the page after the
   * service worker's warm resolves, so a reader who closes the tab in
   * between is left with a complete bucket and no record — and before
   * this function the only thing that could name that bucket's basemap
   * was the record that is missing. The result was a row labelled with a
   * raw bucket id under "Unknown basemap", offering to delete the shared
   * overview map.
   *
   * @param {string} areaId
   * @returns {string} The ``settings.BASEMAP_STYLES`` key, or ``''`` for
   *   an id that is not a base layer's.
   */
  function baseLayerBasemapKey(areaId) {
    return isBaseLayerAreaId(areaId) ? areaId.slice('base-'.length) : '';
  }

  /**
   * The overlap of two ``[minLon, minLat, maxLon, maxLat]`` boxes.
   *
   * @param {number[]} a
   * @param {number[]} b
   * @returns {number[]|null} ``null`` when they do not overlap, or when
   *   either is not a well-formed 4-number box.
   */
  function intersectBBox(a, b) {
    const ok = (box) => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite);
    if (!ok(a)) return null;
    if (!ok(b)) return a;
    const out = [
      Math.max(a[0], b[0]),
      Math.max(a[1], b[1]),
      Math.min(a[2], b[2]),
      Math.min(a[3], b[3]),
    ];
    return out[0] < out[2] && out[1] < out[3] ? out : null;
  }

  /**
   * The ground a basemap's base layer covers (SNOW-856).
   *
   * ``cameraBBox`` is the authority and ``styleBounds`` only ever
   * narrows it. That order is the whole design:
   *
   *   - The CAMERA bbox (``MAX_BOUNDS`` in static/js/map.js — passed in
   *     rather than duplicated here, so there is one definition of where
   *     this map can go) is what makes the layer SUFFICIENT. The reader
   *     cannot pan outside it, so a layer covering it cannot leave a hole.
   *   - The STYLE's declared bounds are what make it FINITE. Deriving the
   *     extent from those alone — the first design this ticket had —
   *     works for the three national styles and blows up on the default
   *     one: OpenFreeMap Liberty is global, and z0-9 worldwide is roughly
   *     350,000 tiles. Intersecting instead means a global style is
   *     bounded by the camera and a national one is bounded by its own
   *     coverage, and neither ever asks a provider for ground it does not
   *     serve.
   *
   * @param {number[]} cameraBBox ``[minLon, minLat, maxLon, maxLat]``.
   * @param {number[]|null|undefined} styleBounds The source's TileJSON
   *   ``bounds``, in the same order. Absent or malformed leaves
   *   ``cameraBBox`` unnarrowed — a style that declares no coverage is
   *   claiming all of it, and the camera is still a bound.
   * @returns {number[]|null} ``null`` when the style covers no part of
   *   the map's own extent, which is a style nothing here should be
   *   downloading a base layer for.
   */
  function baseLayerBBox(cameraBBox, styleBounds) {
    return intersectBBox(cameraBBox, styleBounds);
  }

  /**
   * The base layer's blob for one basemap (SNOW-856).
   *
   * Deliberately ``buildBlob`` rather than a bespoke shape: the result
   * is then interchangeable with a region's server-computed blob and a
   * custom area's locally-built one, so ``rangesToTileURLs``,
   * ``blobFullyCached`` and the byte estimate all work on it unchanged.
   * A rectangle is also the right shape here — unlike a region, there is
   * no boundary to clip to.
   *
   * @param {number[]} cameraBBox See ``baseLayerBBox``.
   * @param {number[]|null|undefined} styleBounds See ``baseLayerBBox``.
   * @param {string} [basemapKey] SNOW-868: whose band to build. Omitted
   *   keeps ``BASE_LAYER_BAND``, the default — see ``baseLayerBand``.
   * @returns {Object|null} ``null`` when there is no overlap.
   */
  function baseLayerBlob(cameraBBox, styleBounds, basemapKey) {
    const bbox = baseLayerBBox(cameraBBox, styleBounds);
    if (!bbox) return null;
    const band = baseLayerBand(basemapKey);
    return buildBlob(bbox, band[0], band[1]);
  }

  /**
   * Every tile URL a basemap's base layer needs (SNOW-856).
   *
   * One URL per tile PER SOURCE, each from the host MapLibre will
   * actually ask that source for — this goes through ``rangesToTileURLs``
   * precisely so the ``urls[(x + y) % urls.length]`` rotation is the same
   * one the area download and the live map both use. A base layer stored
   * under a host the map never asks is a bucket full of tiles that never
   * serve, which is the SNOW-843 failure repeated one layer down.
   *
   * @param {string | string[][]} spec The style's tile sources.
   * @param {number[]} cameraBBox See ``baseLayerBBox``.
   * @param {number[]|null|undefined} styleBounds See ``baseLayerBBox``.
   * @param {string} [basemapKey] SNOW-868: whose band to build. Omitted
   *   keeps ``BASE_LAYER_BAND``, the default — see ``baseLayerBand``.
   * @returns {string[]}
   */
  function baseLayerTileURLs(spec, cameraBBox, styleBounds, basemapKey) {
    const blob = baseLayerBlob(cameraBBox, styleBounds, basemapKey);
    return blob ? rangesToTileURLs(spec, blob) : [];
  }

  // SNOW-929: the tail every tile URL these styles emit has, and that no
  // document they emit has — ``/{z}/{x}/{y}.{ext}``, three numeric path
  // segments and a tile extension.
  //
  // Host-independent and path-only on purpose. The hosts rotate
  // (``tileURLForSource``), OpenFreeMap's tileset path carries a dated
  // build id that changes under us (``planet/20260906_080001_pt/…``), and
  // a tile may arrive with a query string or an API key appended — none of
  // which this can be allowed to care about. What it must get right is the
  // NEAR MISSES, which all fail on the numeric triple:
  //
  //   /fonts/Noto%20Sans%20Bold/0-255.pbf   glyph range — ``0-255`` is one
  //                                         segment, not three, and not a
  //                                         number
  //   /sprites/ofm_f384/ofm@2x.png          sprite — ``ofm@2x`` is not
  //   /styles/liberty                       style document — no extension
  //   /planet, /basemapvectorneu/root.json  TileJSON
  //
  // ``.jpg``/``.jpeg`` are here for completeness rather than for a basemap
  // this project ships: every current style serves ``.pbf`` vector tiles,
  // and OpenFreeMap's natural-earth source serves ``.png`` rasters.
  const TILE_ENTRY_PATH = /\/\d+\/\d+\/\d+\.(?:pbf|mvt|png|jpg|jpeg)$/i;

  /**
   * Whether ``url`` names a TILE rather than one of the documents that
   * draw it (SNOW-929).
   *
   * A pinned bucket holds both since SNOW-929 put the base layer's style,
   * TileJSON, sprite and glyph ranges in beside its tiles, so any check
   * over a bucket's contents now has to be able to tell the two apart.
   * The one that needs it is ``baseLayerStaleEntries`` below — see there
   * for what goes wrong if a document is judged as a tile.
   *
   * Decided from the PATH alone, so a query string, a fragment or an API
   * key never changes the answer — see ``TILE_ENTRY_PATH`` above for the
   * near misses this has to reject and why each one does.
   *
   * @param {string} url A cache entry's url, absolute or relative.
   * @returns {boolean} ``false`` for a non-string, an unparseable url, or
   *   anything without the numeric-triple tail — "not provably a tile",
   *   which every caller reads as "leave it alone".
   */
  function isTileEntryURL(url) {
    if (typeof url !== 'string' || !url) return false;
    try {
      // A base is supplied so a relative entry still parses to a path;
      // its host is never read, and an absolute url ignores it outright.
      return TILE_ENTRY_PATH.test(new URL(url, 'https://snowdesk.info').pathname);
    } catch (_e) {
      return false;
    }
  }

  /**
   * Which of a base-layer bucket's ``entries`` are tiles the current band
   * does not ask for (SNOW-929) — the pure half of the re-banding check.
   *
   * SNOW-863 evicts a base-layer bucket whole when it holds anything
   * outside the current band's url set, because SNOW-856 shipped z0-9 and
   * the default band is z0-7: without that migration every device that
   * ever ran the old band keeps its z8 and z9 tiles for good (they are a
   * superset, so the missing-url plan is empty and nothing re-warms).
   * That check lived inline in ``map_basemap_downloads.js`` and judged
   * EVERY entry.
   *
   * SNOW-929 put the documents that draw the band into the same bucket,
   * which ends that. Folding the document list into the expected set
   * instead would hand a provider the ability to bin a 21 MB band by
   * renaming a sprite path or adding a fontstack — the expected documents
   * are derived from the LIVE style, so they move whenever the provider
   * moves them, while the tile set is a pure function of band, camera and
   * style. So the judgement is tiles only, and a document the current
   * plan happens not to name is left exactly where it is: it is at worst
   * a few tens of kilobytes, and it may well be the thing making the
   * band renderable.
   *
   * Pure, exported and given a truth table of its own
   * (``tests/js/test_basemap_base_layer.js``) rather than staying inline,
   * because the cost of ``isTileEntryURL`` mis-reading a tile as a
   * document is silent: SNOW-863's migration would stop firing and the
   * oversized bucket would sit there with no path out short of a reset.
   *
   * @param {string[]} entries The bucket's entry urls, as
   *   ``cache.keys()`` reports them.
   * @param {Set<string> | string[]} expectedTiles Every tile url the
   *   current band asks for, in either shape — the same contract
   *   ``blobFullyCached`` and ``missingRenderDependencies`` take.
   * @returns {string[]} The stale tile entries, in the order given.
   *   ``[]`` for an empty or unusable bucket ("there is nothing to throw
   *   away") AND for an empty ``expectedTiles``: failing to enumerate the
   *   band must never read as evidence that everything on disk is stale.
   */
  function baseLayerStaleEntries(entries, expectedTiles) {
    if (!Array.isArray(entries) || entries.length === 0) return [];
    const wanted = expectedTiles instanceof Set ? expectedTiles : new Set(expectedTiles || []);
    if (!wanted.size) return [];
    const stale = [];
    for (const entry of entries) {
      if (!isTileEntryURL(entry)) continue;
      if (wanted.has(entry)) continue;
      stale.push(entry);
    }
    return stale;
  }

  /**
   * Decide whether an incoming download fits the standing budget and,
   * when it doesn't, which areas to evict (oldest first) to make it fit.
   *
   * SNOW-586: replaces the old pinned cache's entry-count FIFO trim,
   * which evicted individual cache ENTRIES with no notion of which
   * download they belonged to — perforating whichever area happened to
   * hold the oldest-inserted tiles. Evicting whole AREAS instead means a
   * download can only ever be entirely present or entirely gone.
   *
   * A re-download of an area already in ``areas`` (``incoming.id``
   * matches an existing entry) is treated as a REPLACEMENT: that entry's
   * bytes leave the standing total before the incoming run's bytes are
   * added back in, so re-downloading an area never counts its own old
   * copy against itself.
   *
   * ``incoming.bytes`` exceeding what the budget has left ONCE the
   * un-evictable floor is accounted for is refused outright
   * (``impossible: true``, ``evict: []``) rather than evicting every other
   * area and still failing — no amount of eviction could ever make it fit.
   *
   * SNOW-856: that floor is the shared base layers. They are counted in
   * the standing total (real disk) and excluded from the candidate list
   * (shared, and not the user's to be offered) — see the inline comment.
   *
   * @param {Array<{id: string, bytes: number, savedAt?: string}>} areas
   *   Areas currently on disk, most fields best-effort (a record with no
   *   usable ``bytes``/``savedAt`` is treated as ``0``/unset). May include
   *   ``base-`` entries, which this counts but never evicts.
   * @param {{id: string, bytes: number}} incoming The run being planned.
   * @param {number} budgetBytes The standing budget, in bytes.
   * @returns {{fits: boolean, impossible: boolean, evict: string[],
   *   projectedBytes: number}} ``fits`` is true only when the incoming run
   *   already sits under budget with NO eviction needed — the caller's
   *   "run unchanged" case. ``evict`` lists area ids in the order they
   *   should be removed; a caller only removing some of them (a
   *   cancelled confirm, say) doesn't need to know that, since none of
   *   this function's own state depends on it. ``projectedBytes`` is the
   *   standing total once the plan runs (or, in the ``impossible`` case,
   *   the standing total as it stands today, since nothing changes).
   */
  function planEviction(areas, incoming, budgetBytes) {
    const budget = Number(budgetBytes) || 0;
    const list = Array.isArray(areas) ? areas : [];
    const incomingId = incoming && incoming.id;
    const incomingBytes = Number((incoming && incoming.bytes) || 0);

    // The base layers are not in this arithmetic at all — not as
    // candidates, and (SNOW-XXX) not as bytes either.
    //
    // They were counted, on the reasoning that they are real disk and a
    // budget ignoring real disk is a lie. What that missed is WHOSE budget
    // it is. The z0-9 layer is the app's own map data: fetched once per
    // basemap, shared by every area, never chosen and never removable on
    // its own. Charging it to the user's download budget spends up to
    // 100 MB of a 500 MB allowance on something they cannot point at,
    // cannot delete, and did not ask for — and on a 200 MB budget it can
    // make their SECOND download impossible. It is accounted for where the
    // app's own storage is: the Reset local data summary in account
    // settings.
    //
    // It still goes when the last area under its basemap goes
    // (`evictBasemapAreas`), which is the only moment nothing needs it.
    const others = list.filter(
      (a) => a && a.id !== incomingId && !isBaseLayerAreaId(a.id),
    );
    const evictable = others;

    // Nothing un-evictable is left in the total now, so exhausting the
    // candidate list always leaves exactly the incoming run — which is the
    // guarantee this check restores from before SNOW-856.
    if (incomingBytes > budget) {
      const standing = others.reduce((sum, a) => sum + (Number(a.bytes) || 0), 0);
      return { fits: false, impossible: true, evict: [], projectedBytes: standing };
    }

    let total = others.reduce((sum, a) => sum + (Number(a.bytes) || 0), 0) + incomingBytes;

    if (total <= budget) {
      return { fits: true, impossible: false, evict: [], projectedBytes: total };
    }

    // Oldest savedAt first; id is a deterministic tiebreak for equal or
    // missing timestamps, so a re-run of the same standing set always
    // proposes the same eviction order.
    const sorted = evictable.slice().sort((a, b) => {
      const ta = Date.parse(a.savedAt) || 0;
      const tb = Date.parse(b.savedAt) || 0;
      if (ta !== tb) return ta - tb;
      return String(a.id).localeCompare(String(b.id));
    });
    const evict = [];
    for (const area of sorted) {
      if (total <= budget) break;
      total -= Number(area.bytes) || 0;
      evict.push(area.id);
    }
    return { fits: false, impossible: false, evict: evict, projectedBytes: total };
  }

  /**
   * Normalise one zoom level's ``z`` entry to ``{"<y>": [xmin, xmax]}``,
   * whichever of the two shapes it arrived in (SNOW-583).
   *
   * A full blob's ``z[zoom]`` is one of:
   *   - a 4-int rectangle ``[xmin, xmax, ymin, ymax]`` — ``buildBlob``'s
   *     own shape (the custom-area download, always a rectangle) and
   *     what a region response served from its stale window can still be
   *     (SNOW-902) — expanded here into one identical span per row.
   *   - an object ``{"<y>": [xmin, xmax]}`` — a clipped region blob's
   *     shape (``apps.regions.services.basemap_tiles.build_region_blob``),
   *     taken as given.
   *
   * Every consumer of a blob's ``z`` below (``rangesToTileURLs``,
   * ``tileCount``, ``tileGridPlan``, ``blobFullyCached``) routes through
   * this rather than assuming either shape directly.
   *
   * @param {number[] | Object<string, number[]> | undefined} zEntry
   * @returns {Object<string, [number, number]>}
   */
  function zoomRows(zEntry) {
    if (!zEntry) return {};
    if (Array.isArray(zEntry)) {
      const [xmin, xmax, ymin, ymax] = zEntry;
      const rows = {};
      for (let y = ymin; y <= ymax; y++) rows[String(y)] = [xmin, xmax];
      return rows;
    }
    return zEntry;
  }

  /**
   * Normalise a tile-source spec to ``string[][]`` — one entry per vector
   * source in the style, each holding that source's own URL templates
   * (SNOW-843).
   *
   * A basemap is not one tile URL. A style declares one or more vector
   * SOURCES (the swisstopo winter style has two: ``ch.swisstopo.relief.vt``
   * and ``ch.swisstopo.base.vt``), and each source can list SEVERAL URLs
   * that differ only by hostname, which MapLibre round-robins between per
   * tile. Everything downstream of here — the download's URL list, the
   * cached-tiles overlay, the done-probe — has to agree with MapLibre about
   * both, or the tiles land in the cache under keys nothing will ever ask
   * for. That is exactly the bug this shape exists to close: a download that
   * pinned ``tiles[0]`` of the FIRST source alone kept a fifth of one of the
   * two layers, and the map came up blank offline over a full bucket.
   *
   * Accepted inputs, so a record written before SNOW-843 still resolves:
   *
   *   - a plain string — one source, one URL: ``[[url]]``;
   *   - ``string[][]`` — the current shape, taken as given (empty inner
   *     lists dropped);
   *   - ``string[]`` — read as one single-URL source each, which is what a
   *     flat list of distinct templates means.
   *
   * @param {string | string[] | string[][] | null | undefined} spec
   * @returns {string[][]} Empty for anything unusable — every caller
   *   already treats "no sources" as "nothing to do".
   */
  function tileSources(spec) {
    if (!spec) return [];
    if (typeof spec === 'string') return [[spec]];
    if (!Array.isArray(spec)) return [];
    const out = [];
    for (const entry of spec) {
      if (typeof entry === 'string' && entry) {
        out.push([entry]);
      } else if (Array.isArray(entry)) {
        const urls = entry.filter((url) => typeof url === 'string' && url);
        if (urls.length) out.push(urls);
      }
    }
    return out;
  }

  /**
   * How many vector sources ``spec`` names — the factor by which one
   * tile's worth of ground costs more than a single-source style's
   * (SNOW-843).
   *
   * @param {string | string[] | string[][] | null | undefined} spec
   * @returns {number} ``0`` when nothing resolves.
   */
  function tileSourceCount(spec) {
    return tileSources(spec).length;
  }

  /**
   * A stable string identifying ``spec``, for the equality tests that used
   * to compare two template STRINGS (SNOW-843).
   *
   * Several callers ask "was this area downloaded under the basemap that is
   * on screen now?" — the download record's sources against the live
   * style's. With a string on both sides that was ``===``; with a nested
   * array it needs a canonical form, and this is it. A legacy string record
   * and a live single-source single-URL style still produce the same key, so
   * an OpenFreeMap area downloaded before this ticket keeps matching.
   *
   * @param {string | string[] | string[][] | null | undefined} spec
   * @returns {string} ``''`` when nothing resolves — never equal to a real
   *   key, which is what makes an unresolved style fail the comparison
   *   rather than accidentally matching everything.
   */
  function tileSourcesKey(spec) {
    const sources = tileSources(spec);
    if (!sources.length) return '';
    return JSON.stringify(sources);
  }

  /**
   * The URL one source serves tile ``(z, x, y)`` from — MapLibre's own
   * choice, not ours (SNOW-843).
   *
   * MapLibre picks ``urls[(x + y) % urls.length]`` (``CanonicalTileID.url``
   * in ``static/js/maplibre-gl.min.js``). A download that stored every tile
   * under ``urls[0]`` therefore matched only the tiles whose indices happen
   * to sum to a multiple of ``urls.length`` — one in five for swisstopo's
   * five ``vectortilesN.geo.admin.ch`` hosts. Mirroring the selection here
   * is what makes a pinned tile findable: Cache Storage matches on the whole
   * URL, so the hostname is part of the key.
   *
   * Kept honest by ``tests/js/test_basemap_download_core.js``, which asserts
   * the rotation against the same worked example the SNOW-843 trace showed.
   *
   * @param {string[]} urls One source's URL templates, in style order.
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {string}
   */
  function tileURLForSource(urls, z, x, y) {
    const template = urls.length === 1 ? urls[0] : urls[(x + y) % urls.length];
    return template
      .replace('{z}', String(z))
      .replace('{x}', String(x))
      .replace('{y}', String(y));
  }

  /**
   * Every URL one tile needs — one per source (SNOW-843).
   *
   * @param {string[][]} sources Normalised by ``tileSources``.
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {string[]}
   */
  function tileURLs(sources, z, x, y) {
    const urls = [];
    for (const source of sources) urls.push(tileURLForSource(source, z, x, y));
    return urls;
  }

  /**
   * Which basemap's per-tile figure prices ``spec`` (SNOW-868).
   *
   * Resolved from the source template's HOST, deliberately, and NOT from
   * ``activeBasemapKey()``. That function reads the picker's DOM, which
   * ``map_basemap_picker.js`` updates synchronously on click — so between
   * the click and MapLibre finishing ``setStyle()`` it LEADS the render,
   * while ``activeBasemapTileSources`` still returns the outgoing style's
   * templates. Its own comment (``map_basemap_downloads.js``) says that
   * mismatch is harmless precisely because it is display-only and nothing
   * there feeds a decision. Pricing off it would end that guarantee, and in
   * that window would apply the incoming basemap's constant to the outgoing
   * basemap's tiles. Matching the host instead makes the estimate a
   * function of what will actually be fetched.
   *
   * The key returned is a PRICING representative, not an identity. Both
   * swisstopo styles serve from the same ``vectortilesN.geo.admin.ch``
   * hosts, so the host cannot tell winter from light — harmless only
   * because the table gives both the same figure. Do not read this as
   * "which basemap is on screen"; ``activeBasemapKey`` answers that, and
   * ``baseLayerBasemapKey`` answers it for a stored area.
   *
   * @param {string | string[] | string[][] | null | undefined} spec
   * @returns {string} ``''`` when no host matches — including OpenFreeMap,
   *   whose origin is deployment-dependent (see ``BYTES_PER_TILE_BY_BASEMAP``).
   */
  function basemapKeyForTileSources(spec) {
    const sources = tileSources(spec);
    for (const source of sources) {
      for (const url of source) {
        for (const [fragment, key] of BASEMAP_HOST_KEYS) {
          if (url.indexOf(fragment) !== -1) return key;
        }
      }
    }
    return '';
  }

  /**
   * What one tile of ``key``'s basemap costs, in bytes (SNOW-868).
   *
   * @param {string} key A ``BASEMAP_STYLES`` key.
   * @returns {number} ``WORST_CASE_BYTES_PER_TILE`` for an unknown key —
   *   which is also OpenFreeMap's own measured figure, so the default
   *   basemap is priced correctly by the fallback.
   */
  function bytesPerTileForBasemap(key) {
    const bytes = BYTES_PER_TILE_BY_BASEMAP[key];
    return Number.isFinite(bytes) ? bytes : WORST_CASE_BYTES_PER_TILE;
  }

  /**
   * What one tile costs for the style ``spec`` describes (SNOW-868) — the
   * composition of ``basemapKeyForTileSources`` and
   * ``bytesPerTileForBasemap``, and what the two frame-sizing call sites
   * pass to ``budgetScaleForBBox``.
   *
   * @param {string | string[] | string[][] | null | undefined} spec
   * @returns {number} Bytes per tile PER SOURCE.
   */
  function bytesPerTileForSources(spec) {
    return bytesPerTileForBasemap(basemapKeyForTileSources(spec));
  }

  /**
   * What a blob costs on the basemap ``spec`` describes, in megabytes
   * (SNOW-843, re-based per basemap by SNOW-868).
   *
   * A blob's ``mb`` is computed per TILE at ``WORST_CASE_BYTES_PER_TILE`` —
   * server-side for a region, ``buildBlob`` for a custom area — and neither
   * knows which basemap will be fetched. Two things follow from that, and
   * this is where both are corrected:
   *
   *   - a multi-source style fetches one tile per source per cell, so it
   *     costs a multiple of the ground (SNOW-843); and
   *   - a national style's tiles are simply fatter than the default
   *     basemap's — 60.8 KB against 25.2 measured — so the same tile count
   *     costs more (SNOW-868). See ``BYTES_PER_TILE_BY_BASEMAP``.
   *
   * Given ``count`` this recomputes from the tile count rather than scaling
   * ``mb``, because ``mb`` already has ``DOWNLOAD_DOCUMENTS_MB`` folded in
   * and scaling it would inflate the allowance too — the pre-SNOW-868 code
   * charged the documents once PER SOURCE. Every blob carries ``count``
   * (``{band, count, mb, over_ceiling, centre_tile, z}``, and it is one of
   * ``basemap_tiles._SUMMARY_KEYS``), so the ``mb``-only path below is a
   * fallback for a caller holding nothing but the number.
   *
   * @param {number} mb The blob's own per-tile estimate.
   * @param {string | string[] | string[][] | null | undefined} spec
   * @param {number} [count] The blob's tile count, when the caller has it.
   * @returns {number} ``mb`` unchanged when the style is UNRESOLVED — an
   *   unknown basemap must not inflate an estimate on a guess. Note this is
   *   an unresolved-spec test, not the single-source one it was before
   *   SNOW-868: a single-source NATIONAL style (ign_plan, basemap_at) still
   *   costs more than the fallback and must be priced.
   */
  function sourceScaledMb(mb, spec, count) {
    const sources = tileSourceCount(spec);
    if (!Number.isFinite(mb) || sources <= 0) return mb;
    const bytesPerTile = bytesPerTileForSources(spec);
    const bytesPerMb = 1024 * 1024;
    if (Number.isFinite(count) && count >= 0) {
      return Math.ceil((count * sources * bytesPerTile) / bytesPerMb) + DOWNLOAD_DOCUMENTS_MB;
    }
    // No count: scale the TILE half of ``mb`` only, then put the documents
    // allowance back. Exactly a no-op when the fallback figure applies to a
    // single-source style, which is the arithmetic ``buildBlob`` did.
    const tileMb = Math.max(0, mb - DOWNLOAD_DOCUMENTS_MB);
    return (
      Math.ceil((tileMb * sources * bytesPerTile) / WORST_CASE_BYTES_PER_TILE) +
      DOWNLOAD_DOCUMENTS_MB
    );
  }

  /**
   * Expand a full basemap_download blob's ``z`` ranges into tile URLs.
   *
   * SNOW-843: one URL per tile PER SOURCE, each from the host MapLibre will
   * ask that source for — see ``tileSources`` and ``tileURLForSource``.
   *
   * @param {string | string[][]} spec The style's tile sources — a
   *   ``{z}``/``{x}``/``{y}`` template string (one source, one host) or the
   *   ``string[][]`` shape ``tileSources`` normalises to.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   The full blob — either fetched from
   *   ``/api/region-basemap-tiles/?id=...`` or built locally by
   *   ``buildBlob`` (``{band, count, mb, over_ceiling, centre_tile, z}``
   *   — see ``regions/services/basemap_tiles.py``'s module docstring).
   *   Each zoom's ``z[zoom]`` is either shape ``zoomRows`` accepts.
   * @returns {string[]}
   */
  function rangesToTileURLs(spec, blob) {
    const urls = [];
    const sources = tileSources(spec);
    if (!sources.length || !blob || !blob.z) return urls;
    for (const zKey of Object.keys(blob.z)) {
      const z = Number(zKey);
      const rows = zoomRows(blob.z[zKey]);
      for (const yKey of Object.keys(rows)) {
        const y = Number(yKey);
        const [xmin, xmax] = rows[yKey];
        for (let x = xmin; x <= xmax; x++) {
          for (const url of tileURLs(sources, z, x, y)) urls.push(url);
        }
      }
    }
    return urls;
  }


  /**
   * Web Mercator ``[x, y]`` tile indices for ``(lon, lat)`` at zoom ``z``.
   *
   * Deliberate re-port of ``basemap_tiles.lon_lat_to_tile`` — see the
   * module header above for why. Not clamped to the valid
   * ``[0, 2**z - 1]`` range; ``tileRangesForBBox`` clamps explicitly,
   * matching the Python.
   *
   * @param {number} lon Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @param {number} z Zoom level.
   * @returns {[number, number]}
   */
  function lonLatToTile(lon, lat, z) {
    const n = Math.pow(2, z);
    const [wx, wy] = lonLatToWorld(lon, lat);
    return [Math.floor(wx * n), Math.floor(wy * n)];
  }

  /**
   * ``(lon, lat)`` as UNFLOORED Web Mercator world coordinates — the same
   * projection ``lonLatToTile`` floors, expressed as fractions of the
   * whole world in ``[0, 1]`` (x eastward from the antimeridian, y
   * southward from the north pole). Multiply by ``2**z`` for fractional
   * tile indices at zoom ``z``.
   *
   * Factored out of ``lonLatToTile`` (whose arithmetic it reproduces
   * operation-for-operation, so the golden vector is unaffected) because
   * ``budgetScaleForBBox`` below needs the projection WITHOUT the floor:
   * a rectangle's tile cost has to vary continuously with its size for
   * the frame to resize smoothly.
   *
   * @param {number} lon Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @returns {[number, number]}
   */
  function lonLatToWorld(lon, lat) {
    const x = (lon + 180.0) / 360.0;
    const latRad = (lat * Math.PI) / 180.0;
    const y = (1.0 - Math.log(Math.tan(latRad) + 1.0 / Math.cos(latRad)) / Math.PI) / 2.0;
    return [x, y];
  }

  /**
   * Tile-index ranges covering ``bbox`` for every zoom in ``[minZ, maxZ]``.
   *
   * Deliberate re-port of ``basemap_tiles.tile_ranges``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} minZ Shallowest zoom level (inclusive).
   * @param {number} maxZ Deepest zoom level (inclusive).
   * @returns {Object<string, [number, number, number, number]>}
   *   ``{"<z>": [xmin, xmax, ymin, ymax]}`` — one entry per zoom level,
   *   with indices clamped to the valid ``[0, 2**z - 1]`` range.
   */
  function tileRangesForBBox(bbox, minZ, maxZ) {
    const [west, south, east, north] = bbox;
    const ranges = {};
    for (let z = minZ; z <= maxZ; z++) {
      const [x0, y0] = lonLatToTile(west, north, z);
      const [x1, y1] = lonLatToTile(east, south, z);
      let xmin = Math.min(x0, x1);
      let xmax = Math.max(x0, x1);
      let ymin = Math.min(y0, y1);
      let ymax = Math.max(y0, y1);
      const maxIndex = Math.pow(2, z) - 1;
      xmin = Math.max(0, Math.min(xmin, maxIndex));
      xmax = Math.max(0, Math.min(xmax, maxIndex));
      ymin = Math.max(0, Math.min(ymin, maxIndex));
      ymax = Math.max(0, Math.min(ymax, maxIndex));
      ranges[String(z)] = [xmin, xmax, ymin, ymax];
    }
    return ranges;
  }

  /**
   * Total tile count across every zoom level in ``ranges``.
   *
   * Deliberate re-port of ``basemap_tiles.tile_count`` (rectangle
   * ranges) — but, via ``zoomRows``, also correct for a clipped region
   * blob's row-span ``z`` (SNOW-583's ``row_tile_count`` counterpart),
   * so one function serves both shapes.
   *
   * @param {Object<string, number[] | Object<string, number[]>>} ranges
   *   The ``{"<z>": ...}`` shape returned by ``tileRangesForBBox``, or a
   *   full blob's ``z``.
   * @returns {number}
   */
  function tileCount(ranges) {
    let total = 0;
    for (const key of Object.keys(ranges)) {
      const rows = zoomRows(ranges[key]);
      for (const y of Object.keys(rows)) {
        const [xmin, xmax] = rows[y];
        total += xmax - xmin + 1;
      }
    }
    return total;
  }

  /**
   * The tile at ``bbox``'s centre point, at zoom ``z``. Recorded on a
   * download's blob as ``centre_tile``; ``map.js``'s ``_probeDone`` reads
   * it back off the stored record rather than re-deriving a URL for it.
   *
   * Deliberate re-port of ``basemap_tiles.centre_tile``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} z Zoom level — the download's detail floor
   *   (``MICRO_BAND[1]``).
   * @returns {{z: number, x: number, y: number}}
   */
  function centreTile(bbox, z) {
    const [west, south, east, north] = bbox;
    const centreLon = (west + east) / 2.0;
    const centreLat = (south + north) / 2.0;
    const [x, y] = lonLatToTile(centreLon, centreLat, z);
    return { z: z, x: x, y: y };
  }

  /**
   * Build the full download blob for ``bbox`` — the SAME shape
   * ``rangesToTileURLs`` consumes, and the same shape
   * ``/api/region-basemap-tiles/`` serves for a region download.
   *
   * Deliberate re-port of ``basemap_tiles.build_blob``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} minZ The shallowest zoom level (``MICRO_BAND[0]``).
   * @param {number} maxZ The detail floor (``MICRO_BAND[1]``).
   * @returns {{band: [number, number], count: number, mb: number,
   *   over_ceiling: boolean, centre_tile: {z: number, x: number, y:
   *   number}, z: Object<string, number[]>}}
   */
  function buildBlob(bbox, minZ, maxZ, ceilingMb) {
    const ranges = tileRangesForBBox(bbox, minZ, maxZ);
    const count = tileCount(ranges);
    const totalBytes = count * WORST_CASE_BYTES_PER_TILE;
    // Plus the run's non-tile documents — see DOWNLOAD_DOCUMENTS_MB. Added
    // after the round-up so the two terms cannot both round the same
    // megabyte up.
    const mb = Math.ceil(totalBytes / (1024 * 1024)) + DOWNLOAD_DOCUMENTS_MB;
    return {
      band: [minZ, maxZ],
      count: count,
      mb: mb,
      over_ceiling: mb > resolveCeilingMb(ceilingMb),
      centre_tile: centreTile(bbox, maxZ),
      z: ranges,
    };
  }

  /**
   * Build a download blob for a CIRCLE rather than a box (hack mode).
   *
   * Same shape as ``buildBlob`` and interchangeable with it everywhere —
   * the ``z`` map is written as row spans (``{"<z>": {"<y>": [xmin,
   * xmax]}}``) instead of a rectangle, which every consumer already
   * handles: ``zoomRows`` normalises the two, and row spans are how a
   * REGION download clips its tiles to the region's boundary
   * (``basemap_tiles.build_region_blob``). This is the same trick against
   * a shape whose clipping needs no polygon maths.
   *
   * Why it is worth doing at all. The drop zone draws a circle, and a
   * circle is what the user chose; downloading its bounding box fetched a
   * fifth more tiles than they asked for and — the part that showed — drew
   * a SQUARE on the map afterwards. The downloaded-tiles overlay does not
   * render the stored bbox: it reads the real cached tiles out of Cache
   * Storage and draws one square per tile (``map.js``'s
   * ``refreshDownloadedOverlay``), so the shape on screen is exactly the
   * shape of what was fetched. Clipping here is therefore the whole fix —
   * no overlay change, no new geometry to store.
   *
   * Per row, the widest part of the circle within that row's latitude band
   * is at the latitude NEAREST the centre (the band's own edge, or the
   * centre's latitude for the row containing it). Taking the span there
   * over-includes a tile whose corner clips the circle, which is the right
   * direction: a tile the circle touches is a tile the map will ask for.
   *
   * Equirectangular, matching ``map_drop_zone.js``'s own circle geometry —
   * the ring drawn on screen and the tiles fetched under it have to agree
   * with each other more than either has to be geodesic.
   *
   * Client-only, like ``budgetScaleForBBox``: the server never sizes one of
   * these, and there is no ``basemap_tiles.py`` twin to keep it honest
   * against.
   *
   * @param {number} lat Centre latitude.
   * @param {number} lon Centre longitude.
   * @param {number} radiusKm Radius in kilometres.
   * @param {number} minZ Shallowest zoom (inclusive).
   * @param {number} maxZ Detail floor (inclusive).
   * @param {number} [ceilingMb] What ``over_ceiling`` is measured against;
   *   defaults to ``DOWNLOAD_CEILING_MB``.
   * @returns {{band: [number, number], count: number, mb: number,
   *   over_ceiling: boolean, centre_tile: {z: number, x: number, y:
   *   number}, z: Object<string, Object<string, [number, number]>>}}
   */
  function circleBlob(lat, lon, radiusKm, minZ, maxZ, ceilingMb) {
    const latDelta = radiusKm / KM_PER_DEGREE_LAT;
    const bbox = [
      lon - radiusKm / (KM_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180)),
      lat - latDelta,
      lon + radiusKm / (KM_PER_DEGREE_LAT * Math.cos((lat * Math.PI) / 180)),
      lat + latDelta,
    ];
    const ranges = {};
    let count = 0;
    for (let z = minZ; z <= maxZ; z++) {
      const [, southY] = lonLatToTile(bbox[0], bbox[1], z);
      const [, northY] = lonLatToTile(bbox[0], bbox[3], z);
      const rows = {};
      // Mercator tile y runs southward, so the north edge has the lower y.
      for (let y = northY; y <= southY; y++) {
        const [, tileSouth, , tileNorth] = tileBounds(z, 0, y);
        // The latitude in this row closest to the centre — where the
        // circle is at its widest within the row.
        const nearLat = Math.min(Math.max(lat, tileSouth), tileNorth);
        const dLatKm = Math.abs(nearLat - lat) * KM_PER_DEGREE_LAT;
        if (dLatKm > radiusKm) continue;
        const halfKm = Math.sqrt(radiusKm * radiusKm - dLatKm * dLatKm);
        const cos = Math.cos((nearLat * Math.PI) / 180);
        // A row at the pole would divide by ~0; nothing this control is for
        // goes there, and clamping is cheaper than reasoning about it.
        const halfLon = halfKm / (KM_PER_DEGREE_LAT * Math.max(cos, 1e-6));
        const [xmin] = lonLatToTile(lon - halfLon, nearLat, z);
        const [xmax] = lonLatToTile(lon + halfLon, nearLat, z);
        rows[String(y)] = [xmin, xmax];
        count += xmax - xmin + 1;
      }
      ranges[String(z)] = rows;
    }
    const mb =
      Math.ceil((count * WORST_CASE_BYTES_PER_TILE) / (1024 * 1024)) + DOWNLOAD_DOCUMENTS_MB;
    return {
      band: [minZ, maxZ],
      count: count,
      mb: mb,
      over_ceiling: mb > resolveCeilingMb(ceilingMb),
      centre_tile: centreTile(bbox, maxZ),
      z: ranges,
    };
  }

  /**
   * The largest factor in ``[0, 1]`` by which ``bbox`` may be scaled
   * about its centre while a ``[minZ, maxZ]`` download of the result
   * still fits under ``DOWNLOAD_CEILING_MB``. ``1`` when ``bbox`` already
   * fits, so a caller can treat "1" as "no cap needed".
   *
   * Why closed-form rather than shrinking until ``buildBlob`` comes in
   * under the ceiling: ``buildBlob``'s count is a STEP function of the
   * box, because the tile indices are floored. Two boxes of identical
   * size sitting a few metres apart on the tile grid can differ by a
   * whole row and column of tiles, so inverting that count by search
   * returns a size that depends on where the box is, not just how big it
   * is — the framing rectangle then shimmers as the map pans beneath it
   * and stutters as it zooms (SNOW-566). This models the cost with the
   * floors removed, which makes the answer a smooth, monotone function of
   * the box alone.
   *
   * The model is a strict UPPER bound on the true count, so the box it
   * sizes never exceeds the ceiling. Per axis, a span of ``t`` fractional
   * tiles covers ``floor(a + t) - floor(a) + 1 < t + 2`` whole ones — so
   * summing ``(sx*2**z + 2) * (sy*2**z + 2)`` over the band bounds the
   * blob's count for scale ``s``, and setting that equal to the budget
   * leaves a quadratic in ``s`` to solve directly. Being an upper bound it
   * leaves roughly a tile's worth of headroom per axis (a few percent of
   * the ceiling) unspent, which is the price of a frame that resizes
   * smoothly — and cheap against a per-tile byte estimate that is itself
   * a worst case.
   *
   * Scaling is assumed to scale the ground footprint linearly, which is
   * exact for the un-pitched view this map is used in. Under pitch the
   * footprint grows faster than the frame does, so the linear model
   * OVER-states the cost of a shrunken frame — the answer stays under the
   * ceiling, it just leaves more headroom.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees, at scale 1.
   * @param {number} minZ Shallowest zoom level (inclusive).
   * @param {number} maxZ Deepest zoom level (inclusive).
   * @param {number} [sourceCount] SNOW-843: how many vector sources the
   *   active style fetches per tile. A two-source style costs twice the
   *   ground, so the frame it may draw is the one whose HALVED tile budget
   *   still fits the ceiling. Defaults to 1 — the single-source case, and
   *   the shape the golden vector asserts.
   * @param {number} [ceilingMb] The ceiling to size against; defaults to
   *   ``DOWNLOAD_CEILING_MB``.
   * @param {number} [bytesPerTile] SNOW-868: what one tile of the active
   *   basemap costs, per source — ``bytesPerTileForSources``. Defaults to
   *   ``WORST_CASE_BYTES_PER_TILE``, which leaves the golden vector and
   *   the default basemap untouched.
   *
   *   This MUST move in step with ``sourceScaledMb``: sizing the frame at
   *   50 KB a tile while pricing the same box at 96 KB means the frame the
   *   control lets you draw is always over the ceiling, and
   *   ``map_custom_download.js``'s ``confirmBtn.disabled = overCeiling ||
   *   …`` then latches Download off for good on every national basemap.
   * @returns {number} A factor in ``(0, 1]``.
   */
  function budgetScaleForBBox(bbox, minZ, maxZ, sourceCount, ceilingMb, bytesPerTile) {
    const [west, south, east, north] = bbox;
    // Whole tiles, not MB: buildBlob rounds bytes UP to the next MB, so a
    // count at exactly this budget is the largest that still reports
    // ``mb <= ceiling``. The documents come off the ceiling first — they
    // are fetched whatever the area's size, so they are not part of what
    // scaling the box can trade away.
    const sources = Number.isFinite(sourceCount) && sourceCount > 1 ? sourceCount : 1;
    const tileBudgetMb = Math.max(1, resolveCeilingMb(ceilingMb) - DOWNLOAD_DOCUMENTS_MB);
    const perTile =
      Number.isFinite(bytesPerTile) && bytesPerTile > 0 ? bytesPerTile : WORST_CASE_BYTES_PER_TILE;
    const budget = (tileBudgetMb * 1024 * 1024) / (perTile * sources);
    // World-fraction spans. Longitude is linear in the projection and
    // latitude is not, hence the Mercator y difference rather than a
    // degree one.
    const spanX = Math.abs(east - west) / 360.0;
    const spanY = Math.abs(lonLatToWorld(west, south)[1] - lonLatToWorld(west, north)[1]);
    // Coefficients of the cost quadratic a*s^2 + b*s + c, summed over the
    // band: a from the two spans together, b from each span against the
    // per-axis slack, c from the slack alone (the cost of a box small
    // enough to be a point — one to four tiles at every level).
    let a = 0;
    let b = 0;
    let c = 0;
    for (let z = minZ; z <= maxZ; z++) {
      const n = Math.pow(2, z);
      a += spanX * n * spanY * n;
      b += 2 * (spanX * n + spanY * n);
      c += 4;
    }
    if (a + b + c <= budget) return 1;
    if (a <= 0) {
      // A degenerate (zero-area) box can only exceed the budget through
      // its slack term, which no amount of scaling removes.
      return b > 0 ? Math.max(0, Math.min(1, (budget - c) / b)) : 0;
    }
    const scale = (-b + Math.sqrt(b * b + 4 * a * (budget - c))) / (2 * a);
    return Math.max(0, Math.min(1, scale));
  }

  /**
   * SNOW-568: whether a download of ``mb`` megabytes fits in the origin's
   * remaining storage quota.
   *
   * Takes the already-resolved ``navigator.storage.estimate()`` result
   * rather than calling it, so this stays a pure function of its
   * arguments (and unit-testable without a Storage API).
   *
   * The estimate is deliberately conservative on both sides. ``mb`` is the
   * download's own worst case (``WORST_CASE_BYTES_PER_TILE`` per tile —
   * real vector tiles are far smaller), and it is required to fit inside
   * ``STORAGE_HEADROOM_FACTOR`` of what's left rather than all of it: a
   * browser starts evicting an origin's storage as it approaches the
   * quota, and an area download that lands exactly at the limit would be
   * the first thing evicted. Refusing early costs the user a smaller
   * frame; not refusing costs them a download that appears to succeed and
   * is gone by the time they are offline and need it.
   *
   * Returns true when the estimate is unusable (Storage API absent, or a
   * browser reporting no quota) — an unknown quota must not block a
   * download that would have worked. The run's own ``QuotaExceededError``
   * handling is the backstop for that case.
   *
   * @param {{quota?: number, usage?: number}|null|undefined} estimate
   * @param {number} mb Estimated download size in megabytes.
   * @returns {boolean}
   */
  /**
   * The largest download this device may hold, in megabytes.
   *
   * The ceiling, and the answer to "how big may one download be": as much
   * as the device can take. Nothing else about the app has an opinion —
   * an app-chosen cap either refuses a download a phone had room for, or
   * waves through one it did not, and only the device knows which.
   *
   * This is the exact inverse of ``hasStorageHeadroom`` below, deliberately
   * so: the largest ``mb`` that function will pass is the number this one
   * returns, which is what keeps the frame a user is offered and the
   * pre-flight that accepts it from ever disagreeing. ``STORAGE_HEADROOM_
   * FACTOR`` is not an app cap in disguise — it is the margin a browser
   * needs before it starts evicting the origin, and a download that lands
   * exactly at the quota is the first thing it takes back.
   *
   * Pure, like its inverse: takes the resolved estimate rather than calling
   * ``navigator.storage.estimate()``.
   *
   * Falls back to ``DOWNLOAD_CEILING_MB`` when the device will not say
   * (no Storage API, or a browser reporting no quota) — an unknown device
   * gets the old constant rather than an unbounded download.
   *
   * Floored at 1 MB: a device with nothing left yields a ceiling that no
   * area could ever meet, and every surface that reads this would have to
   * grow a second empty state to say so. The quota pre-flight refuses that
   * download on its own, with a message about storage, which is the honest
   * place for it.
   *
   * @param {{quota?: number, usage?: number}|null|undefined} estimate
   * @returns {number} Megabytes.
   */
  function deviceCeilingMb(estimate) {
    if (!estimate) return DOWNLOAD_CEILING_MB;
    const quota = Number(estimate.quota);
    const usage = Number(estimate.usage);
    if (!Number.isFinite(quota) || quota <= 0) return DOWNLOAD_CEILING_MB;
    if (!Number.isFinite(usage) || usage < 0) return DOWNLOAD_CEILING_MB;
    const free = Math.max(0, quota - usage) * STORAGE_HEADROOM_FACTOR;
    return Math.max(1, Math.floor(free / (1024 * 1024)));
  }

  /**
   * Read a caller-supplied ceiling, falling back to the constant.
   *
   * One place, so ``buildBlob`` and ``budgetScaleForBBox`` can never
   * disagree about what a missing, zero or nonsense ceiling means — the
   * frame a user is offered and the flag that disables the button are the
   * same question asked twice.
   *
   * @param {number|null|undefined} ceilingMb
   * @returns {number} Megabytes.
   */
  function resolveCeilingMb(ceilingMb) {
    const mb = Number(ceilingMb);
    return Number.isFinite(mb) && mb > 0 ? mb : DOWNLOAD_CEILING_MB;
  }

  function hasStorageHeadroom(estimate, mb) {
    if (!estimate) return true;
    const quota = Number(estimate.quota);
    const usage = Number(estimate.usage);
    if (!Number.isFinite(quota) || quota <= 0) return true;
    if (!Number.isFinite(usage) || usage < 0) return true;
    const needed = Number(mb) * 1024 * 1024;
    if (!Number.isFinite(needed) || needed <= 0) return true;
    return needed <= (quota - usage) * STORAGE_HEADROOM_FACTOR;
  }


  /**
   * ``bbox`` as a GeoJSON Polygon, wound anticlockwise from its
   * south-west corner and explicitly closed.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @returns {{type: string, coordinates: number[][][]}}
   */
  function bboxPolygon(bbox) {
    const [west, south, east, north] = bbox;
    return {
      type: 'Polygon',
      coordinates: [
        [
          [west, south],
          [east, south],
          [east, north],
          [west, north],
          [west, south],
        ],
      ],
    };
  }

  /**
   * Which tiles of ``cachedURLs`` belong to ``template``, as ``{z, x, y}``.
   *
   * The inverse of the substitution ``rangesToTileURLs`` performs: the
   * cache stores URLs, and the "cached tiles" overlay needs the tile
   * indices back so it can draw each one's footprint. Reading them out of
   * the URL is what makes the overlay honest — it renders what Cache
   * Storage actually holds, not what a download record claims.
   *
   * Matching is per-template, so switching basemap changes the answer:
   * tiles cached for one basemap's origin are genuinely not cached for
   * another's.
   *
   * The placeholder order is read from each template rather than assumed —
   * see ``_tileMatcher`` below.
   *
   * SNOW-843: a tile counts as cached only when EVERY source holds it. A
   * two-source style whose relief tiles are down but whose base tiles are
   * not has nothing usable on that ground, and a square drawn for it would
   * be the overlay telling the same lie the download record used to.
   *
   * @param {string | string[][]} spec The style's tile sources — a template
   *   string or the ``string[][]`` shape ``tileSources`` normalises to.
   * @param {Iterable<string>} cachedURLs URLs present in the cache.
   * @param {number} [zoom] Keep only tiles at this zoom. Omit for all of
   *   them — but note a download spans a whole band, so an unfiltered
   *   result overlaps itself five deep.
   * @returns {Array<{z: number, x: number, y: number}>}
   */
  function cachedTilesFromURLs(spec, cachedURLs, zoom) {
    const out = [];
    const sources = tileSources(spec);
    if (!sources.length || !cachedURLs) return out;
    // One matcher per URL of every source; a source whose templates are all
    // unusable can never be satisfied, so the whole answer is empty — the
    // same answer as "no tiles cached for this basemap".
    const matchers = [];
    for (const source of sources) {
      const forSource = [];
      for (const template of source) {
        const matcher = _tileMatcher(template);
        if (matcher) forSource.push(matcher);
      }
      if (!forSource.length) return out;
      matchers.push(forSource);
    }
    // key -> {tile, sources: Set<number>}: a tile is kept once every source
    // index has matched it.
    const seen = new Map();
    for (const url of cachedURLs) {
      for (let i = 0; i < matchers.length; i++) {
        for (const matcher of matchers[i]) {
          const tile = matcher(url);
          if (!tile) continue;
          if (typeof zoom === 'number' && tile.z !== zoom) continue;
          const key = tile.z + '/' + tile.x + '/' + tile.y;
          let entry = seen.get(key);
          if (!entry) {
            entry = { tile: tile, sources: new Set() };
            seen.set(key, entry);
          }
          entry.sources.add(i);
        }
      }
    }
    for (const entry of seen.values()) {
      if (entry.sources.size === matchers.length) out.push(entry.tile);
    }
    return out;
  }

  /**
   * A function reading ``{z, x, y}`` back out of a URL that ``template``
   * could have produced, or ``null`` for a template that cannot be turned
   * into a pattern at all.
   *
   * The placeholder ORDER is read from the template rather than assumed.
   * Most templates are ``{z}/{x}/{y}``, but an ESRI VectorTileServer source
   * is ``{z}/{y}/{x}`` (see ``map.js``'s style normalisation), and reading
   * those transposed would draw every square in the wrong place.
   *
   * @param {string} template
   * @returns {(function(string): ({z: number, x: number, y: number}|null))|null}
   */
  function _tileMatcher(template) {
    if (typeof template !== 'string' || !template) return null;
    const order = [];
    template.replace(/\{(z|x|y)\}/g, (match, key) => {
      order.push(key);
      return match;
    });
    if (order.length !== 3) return null;
    const pattern = template
      .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\\\{(z|x|y)\\\}/g, '(\\d+)');
    let re;
    try {
      re = new RegExp('^' + pattern + '$');
    } catch (_e) {
      return null;
    }
    return (url) => {
      const match = re.exec(url);
      if (!match) return null;
      const tile = {};
      for (let i = 0; i < order.length; i++) tile[order[i]] = Number(match[i + 1]);
      return tile;
    };
  }

  /**
   * The ``[west, south, east, north]`` bounds of one Web Mercator tile.
   *
   * The inverse of ``lonLatToTile``: that floors a projected position to
   * a tile index, this returns the ground the whole index covers. Round
   * tripping is therefore one-way-exact — ``lonLatToTile`` of any point
   * inside these bounds gives back ``(x, y)``, but the bounds are the
   * tile's full extent, not the point that produced it.
   *
   * Client-only, like the rest of the tile-grid group: the server
   * enumerates tile INDICES and never needs to know where they sit.
   *
   * @param {number} z Zoom level.
   * @param {number} x Tile x index.
   * @param {number} y Tile y index.
   * @returns {[number, number, number, number]} ``[west, south, east,
   *   north]`` in degrees.
   */
  function tileBounds(z, x, y) {
    const n = Math.pow(2, z);
    const west = (x / n) * 360.0 - 180.0;
    const east = ((x + 1) / n) * 360.0 - 180.0;
    // Mercator y runs southward, so y+1 is the SOUTHERN edge.
    const north = _mercatorYToLat(y / n);
    const south = _mercatorYToLat((y + 1) / n);
    return [west, south, east, north];
  }

  /**
   * A Web Mercator world y (``[0, 1]``, southward) back to a latitude.
   *
   * The inverse of ``lonLatToWorld``'s y term, factored out because
   * ``tileBounds`` needs it for both of a tile's horizontal edges.
   *
   * @param {number} y World y in ``[0, 1]``.
   * @returns {number} A latitude in degrees.
   */
  function _mercatorYToLat(y) {
    return (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
  }

  /* ------------------------------------------------------------------ *
   * SNOW-924: what an area's boundary CONTAINS.
   *
   * A download stopped at the tiles; everything else a user needs in the
   * field arrived only if they happened to tap the overlay that draws it.
   * These five functions answer "which bulletins and which weather sit
   * inside this area", and they are all here rather than in `map.js`
   * because they are pure functions of geometry — the same reason
   * `bboxPolygon` and `tileGridPlan` are.
   *
   * THE CONTRACT, and the reason this group looks cruder than it could:
   * inside the boundary, everything; outside, whatever happens to be
   * there. Under-fetching is the only defect — a bulletin the user needed
   * and does not have. Over-fetching is not: the content half is
   * kilobytes against a tile half measured in megabytes.
   *
   * So the tests below are RECTANGLES, and deliberately so. A rectangle
   * test can only ever select a SUPERSET of what true geometry would,
   * which is the correct side to fail on, and it costs no
   * point-in-polygon, no polygon clipping, no shared-edge or antimeridian
   * cases. `tests/js/test_basemap_download_core.js` pins the superset
   * property against real region boundaries; a future reader "fixing"
   * this into exact geometry would be trading a free over-selection for
   * a class of bug that leaves someone without a bulletin.
   * ------------------------------------------------------------------ */

  /**
   * The lon/lat box a GeoJSON Polygon or MultiPolygon covers.
   *
   * SNOW-924 moved this out of `map.js`, where it had been since SNOW-811
   * serving the region popup's fit and `pwaMapFocus.region()`. It is a
   * pure function of coordinates, so it belongs with the rest of the
   * geometry group; `map.js` keeps a one-line adapter because MapLibre's
   * `fitBounds` wants the nested `[[w, s], [e, n]]` pair while everything
   * here speaks the flat `[west, south, east, north]` this file uses
   * throughout.
   *
   * @param {Object|null} feature A GeoJSON Feature.
   * @returns {[number, number, number, number]|null} ``null`` for a
   *   feature with no usable geometry, which every caller reads as
   *   "cannot say" rather than "empty".
   */
  function featureBBox(feature) {
    const geometry = feature && feature.geometry;
    if (!geometry || !Array.isArray(geometry.coordinates)) return null;
    const rings =
      geometry.type === 'Polygon'
        ? geometry.coordinates
        : geometry.type === 'MultiPolygon'
          ? geometry.coordinates.flat()
          : null;
    if (!rings) return null;
    let w = Infinity;
    let s = Infinity;
    let e = -Infinity;
    let n = -Infinity;
    for (const ring of rings) {
      if (!Array.isArray(ring)) continue;
      for (const position of ring) {
        const lon = position && position[0];
        const lat = position && position[1];
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        if (lon < w) w = lon;
        if (lon > e) e = lon;
        if (lat < s) s = lat;
        if (lat > n) n = lat;
      }
    }
    return Number.isFinite(w) && Number.isFinite(s) ? [w, s, e, n] : null;
  }

  /**
   * Whether two ``[west, south, east, north]`` boxes touch or overlap.
   *
   * INCLUSIVE at the edges, which is the one thing separating it from
   * `intersectBBox` above — that one uses a strict `<` because it returns
   * the overlapping REGION, and a zero-area overlap is not a region. Here
   * the question is only "might this region have anything in the area",
   * and a shared edge costs one HTML page to include and a missing
   * bulletin to exclude. The contract picks the page.
   *
   * @param {number[]} a
   * @param {number[]} b
   * @returns {boolean} ``false`` when either is not a well-formed box —
   *   an unanswerable question is not an overlap.
   */
  function bboxesOverlap(a, b) {
    const ok = (box) => Array.isArray(box) && box.length === 4 && box.every(Number.isFinite);
    if (!ok(a) || !ok(b)) return false;
    return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
  }

  /**
   * Whether a point sits in a ``[west, south, east, north]`` box.
   *
   * Inclusive at the edges, for the same reason as `bboxesOverlap`.
   *
   * @param {number} lon
   * @param {number} lat
   * @param {number[]} bbox
   * @returns {boolean}
   */
  function pointInBBox(lon, lat, bbox) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return false;
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return false;
    return lon >= bbox[0] && lon <= bbox[2] && lat >= bbox[1] && lat <= bbox[3];
  }

  /**
   * The ground a blob's tile ranges cover, as a lon/lat box.
   *
   * The way to a rectangle for a REGION area, which stores none: SNOW-583
   * replaced a region record's `bbox` with `z`, the row spans its tiles
   * were clipped to, on the reasoning that the region id is the whole
   * definition. True for re-fetching it; not enough for asking what is
   * inside it, which is what this ticket needs.
   *
   * Reads the DEEPEST zoom present, matching `gridZoomFor`'s choice and
   * for the same reason — the finest tiles give the tightest box. Any
   * zoom would be safe, since they all cover the same ground and a
   * coarser one only over-states it; deepest is simply the least
   * over-inclusive answer still on the correct side of the contract.
   *
   * Goes through `zoomRows`, as everything walking a blob's `z` must, so
   * both the rectangle and the clipped row-span shapes work here.
   *
   * @param {Object|null} z A blob's ``z`` ranges.
   * @returns {[number, number, number, number]|null} ``null`` for ranges
   *   that yield no tiles.
   */
  function bboxFromZoomRanges(z) {
    if (!z || typeof z !== 'object') return null;
    const zooms = Object.keys(z)
      .map((key) => parseInt(key, 10))
      .filter(Number.isFinite);
    if (zooms.length === 0) return null;
    const zoom = Math.max(...zooms);
    const rows = zoomRows(z[String(zoom)] !== undefined ? z[String(zoom)] : z[zoom]);
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const key of Object.keys(rows || {})) {
      const y = parseInt(key, 10);
      const span = rows[key];
      if (!Number.isFinite(y) || !Array.isArray(span)) continue;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
      if (span[0] < minX) minX = span[0];
      if (span[1] > maxX) maxX = span[1];
    }
    if (!Number.isFinite(minX) || !Number.isFinite(minY)) return null;
    // Mercator y runs southward, so the NORTHERN edge comes from the
    // smallest y and the southern from the largest.
    const [west, , , north] = tileBounds(zoom, minX, minY);
    const [, south, east] = tileBounds(zoom, maxX, maxY);
    return [west, south, east, north];
  }

  /**
   * The rectangle an area covers, from its stored record alone.
   *
   * Two area kinds, one answer. A custom or drop-zone area IS its `bbox`;
   * a region area has only `z`, so it goes through `bboxFromZoomRanges`.
   * Taking the stored box first matters beyond being cheaper: it is the
   * box the user actually framed, where the derived one is the tiles that
   * box happened to land on, which is a hair larger.
   *
   * @param {Object|null} area A stored area record, or anything carrying
   *   a `bbox` or a `z`.
   * @returns {[number, number, number, number]|null}
   */
  function areaBBox(area) {
    if (!area || typeof area !== 'object') return null;
    const stored = area.bbox;
    if (Array.isArray(stored) && stored.length === 4 && stored.every(Number.isFinite)) {
      return [stored[0], stored[1], stored[2], stored[3]];
    }
    return bboxFromZoomRanges(area.z);
  }

  /**
   * What an area's boundary contains: bulletin pages and weather sheets.
   *
   * The two sets that cannot be fetched wholesale. There are 461
   * micro-regions across the estate and ~550 public weather locations, so
   * unlike the four overlay feeds — one small request each, taken whole
   * and unfiltered — these have to be narrowed to the area. They are
   * narrowed by rectangle, per the contract at the top of this group.
   *
   * A bulletin URL is `/<region_id>/<slug>/<date>/`, built from the two
   * properties `regions.geojson` carries for exactly this purpose. The id
   * is LOWERCASED because `bulletin_detail` is wrapped in
   * `@lowercase_region_id` and 301s a mixed-case one — a redirect the
   * service worker would cache as the entry for a URL nothing ever
   * requests again.
   *
   * A weather sheet is ONE undated URL per location, not one per day, and
   * that is a correctness point rather than a saving: `?date=` selects
   * which `Weather` ROW the page reads, and only today's row exists.
   * The forward days live inside that row's `forecast[]` and the day
   * picker selects among them client-side — see
   * `docs/decisions/weather-day-picker-is-a-selector-not-navigation.md`.
   * Fetching a URL per day would cache six "no weather was recorded here
   * for this day" pages out of every seven.
   *
   * @param {Object} options
   * @param {number[]} options.bbox The area's rectangle.
   * @param {Array<Object>} [options.regionFeatures] `regions.geojson`
   *   features. Absent or empty yields no bulletins — "cannot say", which
   *   the caller reports rather than treating as "none inside".
   * @param {Array<Object>} [options.weatherFeatures] `weather.geojson`
   *   point features.
   * @param {string[]} [options.days] Date keys to take a bulletin for,
   *   supplied by the caller from the same forward bound the scrubber and
   *   calendar use (SNOW-927), never from a clock in here.
   * @param {string} [options.weatherDetailTemplate] A URL carrying
   *   ``__SHORTID__``, as `#map`'s `data-weather-detail-url` does.
   * @returns {{regionIds: string[], bulletinUrls: string[],
   *   weatherDetailUrls: string[]}} Each list deduplicated and stable in
   *   input order, so a run's URL list is reproducible.
   */
  function areaContentPlan(options) {
    const opts = options || {};
    const bbox = opts.bbox;
    const out = { regionIds: [], bulletinUrls: [], weatherDetailUrls: [] };
    if (!Array.isArray(bbox) || bbox.length !== 4) return out;

    const days = Array.isArray(opts.days) ? opts.days.filter(Boolean) : [];
    const seenRegion = new Set();
    for (const feature of opts.regionFeatures || []) {
      const properties = (feature && feature.properties) || {};
      const regionId = properties.id || properties.regionID;
      if (!regionId || seenRegion.has(regionId)) continue;
      if (!bboxesOverlap(bbox, featureBBox(feature))) continue;
      seenRegion.add(regionId);
      out.regionIds.push(regionId);
      const slug = properties.slug;
      if (!slug) continue;
      for (const day of days) {
        out.bulletinUrls.push('/' + String(regionId).toLowerCase() + '/' + slug + '/' + day + '/');
      }
    }

    const template = opts.weatherDetailTemplate;
    if (template) {
      const seenLocation = new Set();
      for (const feature of opts.weatherFeatures || []) {
        const shortId = feature && feature.properties && feature.properties.short_id;
        const position = feature && feature.geometry && feature.geometry.coordinates;
        if (!shortId || seenLocation.has(shortId) || !Array.isArray(position)) continue;
        if (!pointInBBox(position[0], position[1], bbox)) continue;
        seenLocation.add(shortId);
        out.weatherDetailUrls.push(template.replace('__SHORTID__', shortId));
      }
    }

    return out;
  }

  /**
   * The zoom level to draw a download's progress grid at: the deepest in
   * the blob.
   *
   * The download spans a whole band (z10-14 — see ``MICRO_BAND``) and
   * every level covers the SAME ground, so the grid has to pick one zoom
   * or it would paint the same area five times over. It picks the band's
   * detail floor, which makes each square a REAL tile — the finest unit
   * the run actually fetches (~1.7 km across in the Alps at z14), and
   * mostly one tile per square.
   *
   * Deepest rather than a "give me at least N squares" rule: cell count
   * quadruples per level, so any such threshold lands on a different
   * level for different-sized areas — a big region drew 27 km blocks
   * while a small one drew 3 km ones, which read as an inconsistent
   * animation rather than a scale. Deepest is both the finest grid
   * available and the only choice that needs no tuning constant.
   *
   * @param {{z?: Object<string, number[]>}} blob A full download blob.
   * @returns {number | null} A zoom level, or ``null`` for a blob with no
   *   ranges to draw.
   */
  function gridZoomFor(blob) {
    if (!blob || !blob.z) return null;
    const zooms = Object.keys(blob.z)
      .map(Number)
      .filter((z) => Number.isFinite(z));
    if (!zooms.length) return null;
    return Math.max.apply(null, zooms);
  }

  /**
   * The grid cell a tile belongs to, in cell-index space at ``gridZ``.
   *
   * A tile deeper than the grid sits inside exactly one cell, found by
   * shifting its indices right. A tile SHALLOWER than the grid (every
   * level above the band's floor, which is where the grid is drawn) spans
   * many cells; it is assigned to the north-westmost one it covers, so
   * every tile lands in exactly one cell and the per-cell totals sum to
   * the run's tile count.
   *
   * That assignment is deliberately not "the cells this tile covers".
   * The grid is a progress indicator, not a coverage map — one tile
   * completing several cells would let squares light up for ground whose
   * own detail tiles have not been fetched yet.
   *
   * ``gridRows`` clamps the result into the grid's own footprint. A
   * coarse tile starts WEST and NORTH of the area it was fetched for (its
   * indices floor to a wider grid), so its north-westmost fine cell can
   * fall outside the rows the grid draws — which showed up as a lattice
   * of stray squares scattered off the edge of the download area, over
   * ground the run does not cover. Clamping folds those onto the edge
   * cell the tile actually overlaps.
   *
   * SNOW-583: a clipped region blob's grid rows are no longer necessarily
   * CONTIGUOUS in y (a concave boundary can leave a gap between two
   * present rows), so the old single clamp — bound ``cx``/``cy`` each
   * independently into the grid zoom's own rectangle — could land a cell
   * on a row that has no tiles at all. The clamp is therefore two steps,
   * row first: ``cy`` snaps to the nearest row ``gridRows`` actually has,
   * THEN ``cx`` clamps into that row's own ``[xmin, xmax]`` span. For a
   * rectangular grid (every row present, one shared span) this reduces to
   * exactly the old single clamp — the ``buildBlob``/custom-area path is
   * untouched in substance, only in shape of the arithmetic.
   *
   * @param {number} z The tile's zoom level.
   * @param {number} x The tile's x index.
   * @param {number} y The tile's y index.
   * @param {number} gridZ The grid's zoom level.
   * @param {Object<string, [number, number]>} gridRows The grid zoom's own
   *   ``{"<y>": [xmin, xmax]}`` rows (``zoomRows(blob.z[gridZ])``).
   * @returns {[number, number]} ``[cellX, cellY]``.
   */
  function _cellForTile(z, x, y, gridZ, gridRows) {
    const shift = z - gridZ;
    let cx;
    let cy;
    if (shift >= 0) {
      const step = Math.pow(2, shift);
      cx = Math.floor(x / step);
      cy = Math.floor(y / step);
    } else {
      const scale = Math.pow(2, -shift);
      cx = x * scale;
      cy = y * scale;
    }
    cy = _nearestRow(cy, gridRows);
    const [xmin, xmax] = gridRows[String(cy)];
    cx = Math.max(xmin, Math.min(cx, xmax));
    return [cx, cy];
  }

  /**
   * The row (``y``) in ``gridRows`` closest to ``cy`` — exact when ``cy``
   * is itself present, otherwise the nearest neighbour. Ties (equidistant
   * above and below) resolve to the smaller ``y``, i.e. the row further
   * NORTH — an arbitrary but deterministic choice; a boundary narrow
   * enough to produce one is already a rare, single-row sliver.
   *
   * @param {number} cy A candidate row index, possibly absent from
   *   ``gridRows``.
   * @param {Object<string, [number, number]>} gridRows The grid zoom's
   *   own rows.
   * @returns {number} A row index guaranteed present in ``gridRows``.
   */
  function _nearestRow(cy, gridRows) {
    let best = null;
    let bestDistance = Infinity;
    for (const key of Object.keys(gridRows)) {
      const y = Number(key);
      const distance = Math.abs(y - cy);
      if (distance < bestDistance || (distance === bestDistance && y < best)) {
        best = y;
        bestDistance = distance;
      }
    }
    return best;
  }

  /**
   * Plan a download as a grid of cells plus the tile URLs that fill them.
   *
   * Returns the URL list to hand to the service worker AND the squares
   * the map draws, with the two tied together: ``urls[i]`` belongs to
   * ``cells[cellOfURL[i]]``, and a cell is complete once all ``total`` of
   * its tiles have settled.
   *
   * **The URLs come back grouped by cell**, which is the whole point.
   * ``rangesToTileURLs`` emits them zoom-major, so a run works through
   * every z10 tile, then every z11, and so on — five passes over the same
   * ground, with no cell finishing until the last pass reaches it.
   * Grouping instead means a cell's tiles are fetched consecutively and
   * the square lights up when they land, so the grid fills in cell by
   * cell. The SET of URLs is identical either way, so ordering costs the
   * download nothing; it is purely what makes the progress legible.
   *
   * Cells are ordered **bottom-up in a boustrophedon**: rows from the
   * south northwards, and each row runs the opposite way to the one below
   * it — west to east, then east to west, and so on. That matches the
   * download roundel, which fills from its bottom edge upwards; the map
   * and the icon are two readouts of one run, so they fill in the same
   * direction.
   *
   * Alternating rather than restarting every row at the west edge because
   * a plain raster scan jumps the length of the area at each row end,
   * which reads as a repeating wipe. Serpentine keeps consecutive cells
   * adjacent, so the filled area grows like something pouring in.
   *
   * Note rows are ordered by DESCENDING cell y: Web Mercator tile y
   * increases southward, so the southernmost row is the highest y and has
   * to come first. Deterministic, so a re-run of the same area fills in
   * the same order.
   *
   * SNOW-843: a cell's ``total`` counts one tile PER SOURCE, and the URL
   * list carries them all, so the grid fills as the ground is actually
   * covered rather than reporting a two-source area complete at half.
   *
   * @param {string | string[][]} spec The style's tile sources — a template
   *   string or the ``string[][]`` shape ``tileSources`` normalises to.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   A full download blob — fetched from
   *   ``/api/region-basemap-tiles/?id=...`` (rectangle or clipped row
   *   spans — either shape, via ``zoomRows``) or built by ``buildBlob``
   *   (always a rectangle).
   * @returns {{gridZ: number, cells: Array<{x: number, y: number, bbox:
   *   [number, number, number, number], total: number}>, urls: string[],
   *   cellOfURL: number[]} | null} ``null`` when there is nothing to draw.
   */
  function tileGridPlan(spec, blob) {
    const gridZ = gridZoomFor(blob);
    const sources = tileSources(spec);
    if (!sources.length || gridZ === null) return null;

    // Bucket every tile in the blob by the cell it lands in, keyed on the
    // cell's own indices so the two loops below agree on identity.
    const gridRows = zoomRows(blob.z[String(gridZ)]);
    const buckets = new Map();
    for (const key of Object.keys(blob.z)) {
      const z = Number(key);
      if (!Number.isFinite(z)) continue;
      const rows = zoomRows(blob.z[key]);
      for (const rowKey of Object.keys(rows)) {
        const y = Number(rowKey);
        const [xmin, xmax] = rows[rowKey];
        for (let x = xmin; x <= xmax; x++) {
          const [cx, cy] = _cellForTile(z, x, y, gridZ, gridRows);
          const cellKey = cx + ':' + cy;
          let bucket = buckets.get(cellKey);
          if (!bucket) {
            bucket = { x: cx, y: cy, tiles: [] };
            buckets.set(cellKey, bucket);
          }
          bucket.tiles.push([z, x, y]);
        }
      }
    }
    if (!buckets.size) return null;

    // Group into rows, then walk them south to north, reversing every
    // other row so the sweep never jumps back across the area.
    const byRow = new Map();
    for (const bucket of buckets.values()) {
      if (!byRow.has(bucket.y)) byRow.set(bucket.y, []);
      byRow.get(bucket.y).push(bucket);
    }
    // Descending y: the southernmost row is the highest tile index.
    const rowKeys = Array.from(byRow.keys()).sort((a, b) => b - a);
    const ordered = [];
    rowKeys.forEach((y, rowIndex) => {
      const row = byRow.get(y).sort((a, b) => a.x - b.x);
      if (rowIndex % 2 === 1) row.reverse();
      for (const bucket of row) ordered.push(bucket);
    });
    const cells = [];
    const urls = [];
    const cellOfURL = [];
    ordered.forEach((bucket, index) => {
      for (const [z, x, y] of bucket.tiles) {
        // SNOW-843: one URL per SOURCE, all attributed to the same cell —
        // a square is a patch of ground, and the ground is not covered
        // until every layer over it is down.
        for (const url of tileURLs(sources, z, x, y)) {
          urls.push(url);
          cellOfURL.push(index);
        }
      }
      cells.push({
        x: bucket.x,
        y: bucket.y,
        bbox: tileBounds(gridZ, bucket.x, bucket.y),
        total: bucket.tiles.length * sources.length,
      });
    });
    return { gridZ: gridZ, cells: cells, urls: urls, cellOfURL: cellOfURL };
  }

  /**
   * Whether EVERY tile in ``blob``'s own ``z`` is present in ``cached`` —
   * "is this download actually available offline?" (SNOW-570; widened by
   * SNOW-583 from a bbox rectangle to whatever tile set the blob itself
   * carries, via ``zoomRows`` — a clipped region blob is checked against
   * exactly the tiles its download fetched, not a bbox super-set of them).
   *
   * Deliberately not the centre-tile proxy an earlier version of the
   * roundel's own done-probe used. That proxy is fair only for a question
   * that only ever asks about a download the user made AS THAT DOWNLOAD:
   * the centre tile is then a witness that that particular run completed.
   * It is NOT fair when two download shapes write to one pinned cache over
   * the same zoom band with the same URL template, so their tiles are
   * indistinguishable strings — a custom-area download whose frame merely
   * crosses a region's centre caches that region's centre tile, and a
   * centre-tile probe would then read the whole region as downloaded on
   * the strength of one tile it never covered (and would equally MISS a
   * region almost entirely covered whose centre falls outside the frame).
   *
   * Full coverage is the honest question and it is nearly free: the caller
   * has already paid for the answer with one ``cache.keys()`` pass, and
   * every tile after the first is a Set lookup. A download is at most a
   * few hundred tiles across the micro band, so the whole check costs
   * single-digit milliseconds — which is why this checks all of them
   * rather than sampling. It is also the RIGHT answer whoever cached the
   * tiles: an area wholly inside a larger download genuinely is available
   * offline, and should say so.
   *
   * @param {string | string[][]} spec The ACTIVE basemap's tile sources —
   *   which is what makes the answer per-basemap. A template string or the
   *   ``string[][]`` shape ``tileSources`` normalises to.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   A full download blob — fetched from
   *   ``/api/region-basemap-tiles/?id=...`` or built locally by
   *   ``buildBlob`` (custom area).
   * @param {Set<string> | string[]} cached The pinned cache's URLs.
   * @returns {boolean} ``false`` for a falsy template or blob (or one with
   *   no ``z``), and for an empty tile set — "nothing is cached" must
   *   never read as "all of nothing is cached, so yes".
   */
  function blobFullyCached(spec, blob, cached) {
    const sources = tileSources(spec);
    if (!sources.length || !blob || !blob.z) return false;
    const cachedSet = cached instanceof Set ? cached : new Set(cached || []);
    let seen = 0;
    for (const zKey of Object.keys(blob.z)) {
      const z = Number(zKey);
      const rows = zoomRows(blob.z[zKey]);
      for (const yKey of Object.keys(rows)) {
        const y = Number(yKey);
        const [xmin, xmax] = rows[yKey];
        for (let x = xmin; x <= xmax; x++) {
          // SNOW-843: every source, at the host MapLibre will ask for it.
          // One layer of a multi-source style being present is not the area
          // being available offline.
          for (const url of tileURLs(sources, z, x, y)) {
            if (!cachedSet.has(url)) return false;
            seen++;
          }
        }
      }
    }
    return seen > 0;
  }

  /**
   * Which of ``depURLs`` are NOT in ``cached`` — an area's missing render
   * dependencies (SNOW-844).
   *
   * ``blobFullyCached`` above answers "are the tiles here?", and until
   * this ticket that was the ONLY question any surface asked about a
   * downloaded area. It is not the question. A pinned area renders offline
   * only if its bucket also holds the style JSON, the TileJSON document
   * each vector source is declared by (SNOW-843 — without it MapLibre
   * cannot learn a single tile URL, so a perfect tile set is unreachable),
   * and the sprite JSON+PNG at 1x and 2x. A download fetches all four; it
   * is the VERIFICATION that was tile-only, which is why an area
   * downloaded before SNOW-843 — one that never fetched its TileJSON at
   * all — still reads ``done`` today.
   *
   * Returns the missing URLs rather than a boolean because the caller
   * repairs from them: ``basemap_download_runner.js``'s ``repair`` warms
   * exactly this list into the area's own bucket, and warming the whole
   * dependency list instead would re-fetch documents already on disk.
   *
   * Glyph ranges are deliberately NOT dependencies here. MapLibre requests
   * only the unicode ranges its labels use, so the honest list is not
   * derivable without re-deriving MapLibre's own glyph logic — SNOW-742
   * PROMOTES whatever was already cached instead, which means the set in
   * the bucket is legitimately partial and any check over it would report
   * a permanent, unrepairable failure. Pinning the ranges an area actually
   * needs is SNOW-847's job; see this ticket's decision doc.
   *
   * @param {string[]} depURLs The area's dependency URLs — from its own
   *   stored record, or derived live from the active style
   *   (``activeBasemapRenderDependencyURLs``, map_basemap_downloads.js).
   * @param {Set<string> | string[]} cached The pinned cache's URLs, in
   *   either shape — same contract as ``blobFullyCached``'s own argument.
   * @returns {string[]} The subset of ``depURLs`` that is absent, in the
   *   order given, deduplicated. ``[]`` for an empty or unusable
   *   ``depURLs`` — "nothing was claimed", which callers read as UNKNOWN
   *   and must never paint as a fault; see the three-row resolution rule
   *   in docs/decisions/a-downloaded-area-is-verified-by-what-it-renders.md.
   */
  function missingRenderDependencies(depURLs, cached) {
    if (!Array.isArray(depURLs) || depURLs.length === 0) return [];
    var cachedSet = cached instanceof Set ? cached : new Set(cached || []);
    var missing = [];
    var seen = new Set();
    for (var i = 0; i < depURLs.length; i += 1) {
      var url = depURLs[i];
      if (typeof url !== 'string' || !url) continue;
      if (seen.has(url)) continue;
      seen.add(url);
      if (!cachedSet.has(url)) missing.push(url);
    }
    return missing;
  }

  /**
   * Decide whether a warm-cache result counts as a completed download.
   *
   * This is the "green offline circle" predicate. Both download controls
   * (``map_region_download.js`` and ``map_custom_download.js``) spelled it
   * out themselves inside their ``finish`` callbacks — the seam
   * ``basemap_download_runner.js``'s header flagged as still untested when
   * the run itself was extracted. Two copies of a four-clause boolean is
   * exactly the shape that drifted in SNOW-607, so it lives here once
   * (SNOW-649).
   *
   * Every clause earns its place:
   *
   * - **absent result** — the worker never ran, or the warm run rejected;
   *   the runner settles with ``null``. Nothing was cached.
   * - **``cancelled``** — SNOW-632. A cancelled run always reports
   *   ``failed: 0``, so without this clause an abort reads as a clean
   *   success and the area is marked available offline when it is not.
   * - **``ok > 0``** — a vacuous run (no tiles, e.g. a feeds-only run when
   *   the style has not settled) must never claim the area is downloaded.
   * - **``failed === 0``** — a partial download is not a download. The
   *   missing tiles are exactly the ones the user would hit offline.
   *
   * @param {{ok: number, failed: number, bytes: number, cancelled?: boolean}
   *   | null | undefined} result The warm-cache worker's reply.
   * @returns {boolean} True only for a complete, uncancelled, non-empty run.
   */
  function downloadSucceeded(result) {
    return !!(result && !result.cancelled && result.ok > 0 && result.failed === 0);
  }

  self.pwaBasemapDownloadCore = Object.freeze({
    downloadSucceeded: downloadSucceeded,
    zoomRows: zoomRows,
    tileSources: tileSources,
    tileSourceCount: tileSourceCount,
    tileSourcesKey: tileSourcesKey,
    tileURLs: tileURLs,
    basemapKeyForTileSources: basemapKeyForTileSources,
    bytesPerTileForBasemap: bytesPerTileForBasemap,
    bytesPerTileForSources: bytesPerTileForSources,
    sourceScaledMb: sourceScaledMb,
    rangesToTileURLs: rangesToTileURLs,
    lonLatToTile: lonLatToTile,
    tileRangesForBBox: tileRangesForBBox,
    tileCount: tileCount,
    centreTile: centreTile,
    buildBlob: buildBlob,
    circleBlob: circleBlob,
    budgetScaleForBBox: budgetScaleForBBox,
    hasStorageHeadroom: hasStorageHeadroom,
    deviceCeilingMb: deviceCeilingMb,
    bboxPolygon: bboxPolygon,
    tileBounds: tileBounds,
    featureBBox: featureBBox,
    bboxesOverlap: bboxesOverlap,
    pointInBBox: pointInBBox,
    bboxFromZoomRanges: bboxFromZoomRanges,
    areaBBox: areaBBox,
    areaContentPlan: areaContentPlan,
    cachedTilesFromURLs: cachedTilesFromURLs,
    gridZoomFor: gridZoomFor,
    tileGridPlan: tileGridPlan,
    blobFullyCached: blobFullyCached,
    missingRenderDependencies: missingRenderDependencies,
    styleFontstacks: styleFontstacks,
    glyphURLs: glyphURLs,
    slopeTileURLs: slopeTileURLs,
    areaIdForRegion: areaIdForRegion,
    generateCustomAreaId: generateCustomAreaId,
    isCustomAreaId: isCustomAreaId,
    areaIdForBaseLayer: areaIdForBaseLayer,
    isBaseLayerAreaId: isBaseLayerAreaId,
    baseLayerBasemapKey: baseLayerBasemapKey,
    intersectBBox: intersectBBox,
    baseLayerBBox: baseLayerBBox,
    baseLayerBand: baseLayerBand,
    baseLayerBlob: baseLayerBlob,
    baseLayerTileURLs: baseLayerTileURLs,
    isTileEntryURL: isTileEntryURL,
    baseLayerStaleEntries: baseLayerStaleEntries,
    pinnedCacheName: pinnedCacheName,
    planEviction: planEviction,
    MICRO_BAND: MICRO_BAND,
    BASE_LAYER_BAND: BASE_LAYER_BAND,
    BASE_LAYER_BANDS: BASE_LAYER_BANDS,
    WORST_CASE_BYTES_PER_TILE: WORST_CASE_BYTES_PER_TILE,
    BYTES_PER_TILE_BY_BASEMAP: BYTES_PER_TILE_BY_BASEMAP,
    DOWNLOAD_CEILING_MB: DOWNLOAD_CEILING_MB,
    DOWNLOAD_DOCUMENTS_MB: DOWNLOAD_DOCUMENTS_MB,
    STORAGE_HEADROOM_FACTOR: STORAGE_HEADROOM_FACTOR,
    DOWNLOAD_BUDGET_MB: DOWNLOAD_BUDGET_MB,
    PINNED_CACHE_PREFIX: PINNED_CACHE_PREFIX,
    CUSTOM_AREA_ID: CUSTOM_AREA_ID,
    GLYPH_RANGES: GLYPH_RANGES,
  });
})();
