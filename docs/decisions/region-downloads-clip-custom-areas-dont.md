---
name: region-downloads-clip-custom-areas-dont
description: SNOW-583 clips region basemap downloads to their boundary; custom areas stay rectangular; drops the downloaded-areas per-region ring
status: current
last-reviewed: 2026-08-02
---

# Region downloads clip to the boundary; custom areas don't

**Decision.** A region's offline-basemap download
(`apps.regions.services.basemap_tiles.build_region_blob`) is clipped to
the region's real boundary plus one z14 margin tile (~1.7 km), rather than
the whole bounding-box rectangle `build_blob` computes. The custom-area
download (`static/js/basemap_download_core.js`'s `buildBlob`) is
UNCHANGED and stays rectangular. The "Downloaded areas" layers-menu
overlay's per-region ring is removed; its custom-area ring and the
cached-tiles squares survive.

**Why clip regions.** Across the 149 Swiss micro-regions, 44% of a
region's bbox-rectangle tiles touched no part of the region — measured,
not estimated. Clipping to the real boundary plus a margin (so the
boundary itself, where people actually look, stays usable) saves 28.5% of
every region download and compounds against the pinned cache's 5,000-entry
cap.

**Why not clip the custom area too.** A custom-area download's "area" IS a
user-drawn rectangle — the framing overlay is a fixed-size box the user
pans/zooms the map underneath, with no polygon to clip to. There is
nothing non-rectangular about it to save by clipping.

**Why the row-span shape, not run-length runs.** The clipped candidate set
is built by testing every candidate tile's dilated footprint against the
boundary (`clip_ranges`), so a zoom level's surviving tiles need not form
a rectangle. The obvious data-preserving shape is one run-length list of
`[x0, x1]` runs per row, matching a boundary's true (possibly-gapped)
intersection with that row exactly. Measured across all 149 CH regions ×
5 zooms: 4,220 rows, of which only 55 (1.3%) have any gap at all, and a
plain `[xmin, xmax]` span over-includes just 148 tiles out of 36,511
(0.4%) versus the exact run list. That's not worth the extra shape
complexity everywhere a blob's `z` is consumed — the JS accessor, the
progress-grid cell clamp, and this module's own tile counter all stay
trivial with a single span per row. `z` is therefore
`{"<zoom>": {"<y>": [xmin, xmax]}}`, not
`{"<zoom>": {"<y>": [[x0, x1], …]}}`.

**Why `basemap.regions` survives, repurposed.** The IndexedDB record a
successful region download writes used to carry the region's bbox, so both
the per-region roundel's done-probe and the "Downloaded areas" overlay
could cheaply recompute the download's tile set client-side from
`FEATURE_BY_REGION_ID`'s geometry — correct only because a region's
download WAS its bbox. That recomputation cannot reproduce a CLIPPED tile
set (the clip depends on the boundary polygon, which the client only has
an unbuffered version of), so the record's shape changes to `{region_id,
band, z, savedAt}` — the run's own `z`, read back directly by the
roundel's probe with no recomputation. A record written before this ticket
carries `bbox` and no `z`; the probe treats that as "no record" and falls
back to a fresh server fetch, which is memoised for the rest of the
session.

**Why the overlay's ring goes, not just its tiles.** The "Downloaded
areas" overlay's per-region ring was the OTHER consumer of the client-side
bbox recomputation — it painted `downloaded` feature-state onto whichever
regions `downloadedIds` reported as covered, checked against the same
recomputed rectangle. With that recomputation gone, the ring has no cheap
way to ask "is this region's clipped tile set fully cached?" for every
loaded region at once (the roundel's fetch-and-memoise trick doesn't scale
to hundreds of regions on screen). The cached-tiles squares — one per tile
Cache Storage actually holds, read straight back out of the cache's own
URLs, attributing nothing to any particular download — already answer
"what do I have offline?" for the whole map without needing any recomputed
tile set at all, so they become the sole per-region answer this overlay
gives. The custom-area ring is untouched: a user-drawn rectangle stays
exactly enumerable client-side (`tileRangesForBBox`), so its full-coverage
check never depended on the thing that broke.

**Consequences.**

- `apps/regions/services/basemap_tiles.py`'s `build_blob` (rectangle) and
  `build_region_blob` (clipped) now coexist deliberately — `build_blob`
  stays the twin of `buildBlob` in `basemap_download_core.js` for the
  golden-vector parity guard (see
  [`client-side-custom-area-tile-math.md`](client-side-custom-area-tile-math.md)),
  and `build_region_blob` is region-only, with no client-side twin (the
  client never enumerates a region's tiles from scratch — it always reads
  the server-computed blob).
- `static/js/basemap_download_core.js`'s `zoomRows(zEntry)` is the single
  point every consumer of a blob's `z` (`rangesToTileURLs`, `tileCount`,
  `tileGridPlan`, `blobFullyCached`) routes through, so both shapes — the
  clipped row-span map and the unchanged rectangle — are handled once.
  This is required, not optional: `/api/region-basemap-tiles/` is
  `Cache-Control: public, max-age=86400` with no ETag, so a returning
  client can be served an old rectangle blob for up to 24h after this
  ships.
- The progress grid's `_cellForTile` clamp becomes two steps — snap the
  row first (to the nearest one the clipped grid actually has), then clamp
  x into that row's own span — because a clipped grid's rows are no longer
  guaranteed contiguous in y. A single rectangular clamp measured 26% of
  coarse tiles (2,940 of 11,284) landing on cells outside the download's
  own footprint.
- Candidate tiles for a region's clip always come from `tile_ranges` over
  the region's UNDILATED bbox — the margin is applied by dilating each
  CANDIDATE TILE's own footprint when testing intersection, never by
  enumerating a buffered bbox. This keeps the clipped set a guaranteed
  subset of the old rectangle's tiles (a tile is only ever dropped, never
  added), which is what lets an already-downloaded region stay `done`
  after this ships rather than reading "not downloaded" forever.
