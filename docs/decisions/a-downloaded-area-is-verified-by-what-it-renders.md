---
name: a-downloaded-area-is-verified-by-what-it-renders
description: missingRenderDependencies, the incomplete download state, repair — a pinned area needs its style, TileJSON and sprite too (SNOW-844)
status: current
last-reviewed: 2026-09-05
---

# A downloaded area is verified by what it renders, not by its tiles

**Decision.** "Is this area available offline?" is two questions, and every
surface asks both. Tile coverage is
`pwaBasemapDownloadCore.blobFullyCached`, unchanged. Render coverage is
`pwaBasemapDownloadCore.missingRenderDependencies(depURLs, cached)` — the
style document, the TileJSON each vector source is declared by, and the
sprite JSON+PNG at 1x and 2x. An area that passes the first and fails the
second gets its own state, **`incomplete`**, and a **repair** that refetches
only the missing documents.

The dependency list is built once, in
`activeBasemapRenderDependencyURLs` (`static/js/map_basemap_downloads.js`),
and `assembleBasemapDownloadFeedURLs` calls it — so what a download fetches
and what the probe checks cannot drift. Each download records that list on
its own record as `deps`.

## Why

SNOW-843 fixed three defects that shared one property: every surface agreed
the area was downloaded, and the map was blank offline. They shared it
because every surface asked the same question — tile coverage — and tile
coverage is not what makes an area render. Without the TileJSON, MapLibre
offline cannot learn a single tile URL, so a perfect pinned tile set is
unreachable. An area downloaded before SNOW-843 never fetched that document
at all, and still read `done`.

The download already fetched all four. Nothing ever re-verified them.

**Two functions, not one widened one.** The two answers drive different
states. Missing tiles means the area is not downloaded — offer a download.
Complete tiles with a missing sprite means the area is nearly there — offer
a repair, four small documents rather than four hundred tiles. Folding them
into one boolean would collapse that distinction at the only place it
matters.

**Repair is not a download.** `basemap_download_runner.js`'s `repair` does
not call `run`. `run`'s sequence — quota pre-flight, budget plan, and the
eviction confirm that destroys another area's bucket for good — exists to
guard a several-hundred-tile download. Putting a four-document repair
through it could ask the user to delete a whole downloaded region to make
room for a sprite, which is a worse outcome than the fault being repaired.

## The three-row resolution rule

Which dependency list an area is judged against
(`areaRenderDependencyURLs`, applied by both roundels and the Manage
downloads sheet):

| record's `deps` | area's basemap | list used |
|---|---|---|
| present | any | the record's own |
| absent | **is** the active one | derived live from the loaded style |
| absent | not the active one | **none — skip the check** |

The third row is load-bearing. The sheet lists rows for basemaps that are
not on screen, and a style that is not loaded cannot be asked what its
sprite is. For such a record we genuinely cannot answer, and reporting
`incomplete` would be the same class of lie as the false `done` this
decision removes, pointing the other way. It resolves itself the moment the
user switches to that basemap — the roundel then probes live and heals the
record — or repairs.

`missingRenderDependencies` answers `[]` for an empty list, so "unknown"
and "nothing to check" are the same value, and no caller can accidentally
read either as a fault.

## What is deliberately excluded

**Glyph ranges.** MapLibre requests only the unicode ranges its labels
actually use, so the honest list is not derivable without re-deriving
MapLibre's own glyph logic — which SNOW-492 declined to do and SNOW-742
still declines. SNOW-742's answer is **promotion**: copy whatever ranges
ordinary browsing already cached into the pinned bucket, so they survive
the passive cache's FIFO trim. That set is legitimately partial — ranges
never browsed were never covered — so a completeness check over it would
report a permanent fault no repair could clear. Pinning the ranges an area
actually needs is [SNOW-847](https://linear.app/hugorodgerbrown/issue/SNOW-847).

**The layers menu** (`static/js/map_layer_sync_status.js`). Its dots report
the live cached/uncached/partial state of a whole basemap, not one area's
completeness — a different question with a different subject. Wiring this
probe into it would make a dot answer about an area the menu never names.

## Consequences

- Every download record carries `deps`. Legacy records do not, and are
  healed — by `_healRegionRecord`, on the same terms as `template` and
  `basemapKey` — only from a list the cache has just been found to hold.
- The `incomplete` roundel state is actionable (a warning the user cannot
  act on is worse than no warning) and is **not** behind the sign-in gate:
  the area is already on the device, and finishing it is not starting a new
  download. It *is* suppressed while offline, like every other state that
  invites a fetch — the honest state returns with the signal that makes it
  actionable.
- On the Manage downloads sheet an `incomplete` row reuses the orphan's
  "Incomplete" line but, unlike an orphan, gets a Repair control: there is a
  record behind it naming exactly what to fetch. SNOW-612's remove-only
  treatment of orphans is unaffected.
- A new sync-status token, `--color-sync-partial`, joins `--color-sync-ok`
  and `--color-sync-blocked`. It is the offline-availability family, not the
  flash-message severity scale.
- **An area's completeness is contingent on its neighbours.** The probe
  reads `pinnedBasemapCacheURLs()`, which unions every pinned bucket
  (SNOW-586), and unlike tiles a basemap's style/sprite/TileJSON URLs are
  *identical* across every area sharing that basemap. So an area that never
  fetched its own TileJSON reads complete on a sibling area's copy — which
  is the RIGHT answer while that sibling exists, because `sw.js`'s
  `_searchPinnedBuckets` really will serve the request from it. The cost is
  that removing area B can flip area A to `incomplete` with no visible
  cause. That is inherited from the union-serving design, not introduced
  here, and one tap of Repair resolves it — but a "why did this area
  suddenly go incomplete" report is expected behaviour, not a regression.
- **A repair from the sheet does not heal `deps`; a repair from the roundel
  does.** The roundel's repair re-enters `_probeDone`, which heals the
  record from what the cache has just proven. The sheet's has no probe to
  re-enter, so a legacy record repaired there keeps its empty `deps` and
  falls back to row three ("skip") once the user switches basemap. Safe in
  both directions — skip never accuses and never falsely completes — but the
  two paths are not equally informative, and closing the gap means giving
  the sheet's repair a heal of its own.
