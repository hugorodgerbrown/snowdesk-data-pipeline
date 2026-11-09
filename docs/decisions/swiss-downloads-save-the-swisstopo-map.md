---
name: swiss-downloads-save-the-swisstopo-map
description: Swiss downloads save the Swisstopo map whatever is on screen; offline the map switches to it (SNOW-1478, basemapsForGround)
status: current
last-reviewed: 2026-11-09
---

# A Swiss download saves the Swisstopo map

**Decision.** Which map a download saves is decided by its ground, not by
the map on screen (SNOW-1478). `basemapsForGround`
(`static/js/basemap_download_core.js`) answers from
`groundInSwitzerland(bbox, pwaSwisstopoExtent.EXTENT)`:

| ground | maps saved |
|---|---|
| inside Switzerland | Swisstopo winter |
| across the border | Swisstopo winter, then the foreign map |
| outside | the foreign map |
| cannot be placed | the map on screen |

The foreign map is the one on screen when it draws that country
(`BASEMAP_COUNTRIES`), otherwise OpenFreeMap. The answer is never empty.

The record keeps every map as `basemaps: [{key, template, deps}]`, primary
first. The old top-level `basemapKey`, `template` and `deps` stay, as a copy
of the primary entry, so every reader that predates the list keeps working;
`recordBasemaps(record)` reads either shape. Account sync still carries only
`basemap_key`, so the list stays on the device.

Sync (`syncArea` → `_addMissingBasemaps`) adds Swisstopo to an area saved
before this, fetching only the tiles its bucket lacks, under the same budget
and quota checks as a download.

Offline with the centre inside an area that holds Swisstopo, a reader on
another map sees Swisstopo, with one line saying so
(`#offline-basemap-banner`, `static/js/map_offline_basemap.js`). The
reader's own choice is never written. Leaving every such area or getting
signal back restores it. The decision is
`map_offline_basemap_core.js` `offlineDecision`.

**Why.** On 8 Nov 2026 Hugo tested a saved Swiss area in airplane mode.
The area said it was available offline, and the map drew only what
browsing had left in the cache, a sharp line at z13, because the area had
been saved under a different map from the one on screen. A download bound
to whatever map happened to be on screen made "available offline" depend
on a choice the reader had forgotten making. Swisstopo is the map the product is built on in
Switzerland, and the one 3D draws on, so it is the one worth holding.

**Why not save every map.** A second copy of the same ground costs roughly
the same again against the 500 MB budget, for a map the reader can switch
away from in one tap once back online.

**Why not switch the reader's choice.** Offline is a state, not a
preference. Writing `snowdesk.map.basemap` would leave the reader on
Swisstopo after landing, with no memory of choosing it.

## Consequences

- This amends [one-basemap-per-region-download.md](one-basemap-per-region-download.md):
  a cross-border region holds two maps in one bucket. A region still holds
  one map per key, and a second download under a different foreign map
  still replaces the first.
- A Swiss region saved on Swisstopo while OpenFreeMap is on screen reads as
  done, not `other-basemap` (`_probeDone`).
- 3D is not part of "available offline". Elevation tiles are pinned when
  the run's primary map is Swisstopo winter, which a Swiss download now
  always is.
- `swisstopo_extent.js` loads for every reader, not only behind
  `map_beyond_switzerland`, because the download reads it.
