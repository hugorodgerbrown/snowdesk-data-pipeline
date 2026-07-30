---
name: client-side-custom-area-tile-math
description: Custom-area basemap download re-ports basemap_tiles.py's tile math into JS instead of a new endpoint; golden-vector parity guard
status: current
last-reviewed: 2026-07-30
---

# Client-side tile math for the custom-area basemap download

**Decision.** SNOW-522's "Download a custom area" control computes its
"up to N MB" size estimate entirely client-side, in
`static/js/basemap_download_core.js`, rather than adding a new API
endpoint that mirrors `apps/regions/services/basemap_tiles.py`'s
`build_blob`. This is a **deliberate re-port** of that module's pure
functions (`lon_lat_to_tile` → `lonLatToTile`, `tile_ranges` →
`tileRangesForBBox`, `tile_count` → `tileCount`, `centre_tile` →
`centreTile`, `build_blob` → `buildBlob`), not independent drift — kept
honest against the Python by a shared golden vector (an identical bbox
asserted against identical expected ranges/count/mb/centre_tile) in both
`tests/js/test_basemap_download_core.js` and
`tests/regions/services/test_basemap_tiles.py`.

**Why this looks like a reversal.** SNOW-521 moved this exact class of
math OUT of the browser and onto the server, specifically because a
*region's* tile coverage is precomputable once (region boundaries are
fixed reference data; the tile grid is static) and re-deriving the same
numbers in every client on every viewport move was wasted work — see
`basemap_tiles.py`'s module docstring. SNOW-522 is not undoing that: it
solves a genuinely different problem.

**Why re-port instead of a new endpoint.** A user-drawn bbox — the area
under a fixed framing rectangle the user pans/zooms the map underneath —
has no stable ID to precompute against server-side. There is no row to
store a `basemap_download` blob on, and the bbox changes on every 'move'
event while the user is framing. Two options were considered:

1. **A new endpoint**, `POST /api/custom-area-basemap-tiles/` accepting a
   bbox and returning a blob — mirrors the existing
   `/api/region-basemap-tiles/` shape. Rejected: the readout has to track
   a MOVING frame live, with no perceptible lag, as the user pans/zooms.
   A network round trip per 'move' event (dozens per second during a
   drag) is both wasteful and slow enough to read as janky; debouncing it
   trades jank for staleness (the readout lagging behind the frame).
2. **Client-side re-port** (chosen). The tile math is ~80 lines of pure
   arithmetic with no database dependency — `lon_lat_to_tile` doesn't
   touch a region's boundary at all, only the bbox it's handed. Running
   it in the browser on every 'move' is cheap (microseconds) and needs no
   network at all.

**Why a golden vector, not a shared implementation.** JS and Python
cannot share one source file. A hand-checked bbox/band pair, asserted to
produce byte-identical output in both languages' test suites (each
naming the other as its twin), is the practical parity guard — but it is
a **convention, not a mechanism**: nothing in CI fails a change to
`basemap_tiles.py` that isn't mirrored in
`basemap_download_core.js`, or vice versa, beyond that shared vector
happening to still pass. A reviewer changing the Python tile math is
responsible for checking whether the JS mirror needs the same change.

**Consequences.**

- `static/js/basemap_download_core.js`'s module docstring documents both
  reasons its tile math exists — moved server-side for the region
  download (SNOW-521), re-ported client-side for the custom-area download
  (SNOW-522) — so a reader doesn't mistake the JS copy for regression.
- `(lon, lat)` argument order is preserved in the JS port, matching
  `basemap_tiles.py`'s own deliberate carve-out from the project's usual
  `(lat, lon)` convention — see that module's docstring. Keeping the axis
  order identical means porting between the two never requires mentally
  swapping arguments.
- `MICRO_BAND`, `WORST_CASE_BYTES_PER_TILE`, and `DOWNLOAD_CEILING_MB` are
  duplicated as constants in both languages. A change to any of the three
  in `basemap_tiles.py` needs a matching edit in
  `basemap_download_core.js` — there is no single source of truth to
  import from across the language boundary.
- The custom-area download and the region download can therefore report
  slightly different worst-case byte budgets only if the two constant
  sets are allowed to drift; keeping them in lock-step is a review
  discipline, not an enforced invariant.
