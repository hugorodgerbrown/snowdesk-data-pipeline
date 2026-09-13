---
name: a-downloaded-area-is-verified-by-what-it-renders
description: missingRenderDependencies, incomplete, repair, contentIncomplete, refreshAreaContent — an area needs its style, TileJSON, sprite, bulletins
status: current
last-reviewed: 2026-09-13
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

**Glyph ranges — no longer excluded (SNOW-847, 2026-09-09).** This section
used to exclude them, and the reasoning held for exactly as long as glyphs
arrived by PROMOTION. MapLibre requests only the unicode ranges its labels
actually use, so the honest per-area list is not derivable without
re-deriving MapLibre's own glyph logic — which SNOW-492 declined and
SNOW-742 also declined. SNOW-742's answer was to copy whatever ranges
ordinary browsing had already cached into the pinned bucket, so they
survived the passive cache's FIFO trim. That set is legitimately partial —
ranges never browsed were never covered — so a completeness check over it
would have reported a permanent fault no repair could clear.

SNOW-847 changes the input rather than the check. The download now FETCHES
a fixed range set for every fontstack the style declares (`GLYPH_RANGES`
and `glyphURLs`, `basemap_download_core.js`), so both sides of the
comparison name the same list by value and a missing range is a real,
repairable gap. The set is fixed rather than derived because neither
derivation works: a style says which FONTS its labels use but never which
CODEPOINTS, and every one of the four basemap hosts answers HTTP 200 for
all 256 ranges — an unpublished range is a 29–45 byte stub rather than a
404, so only body size distinguishes it, and finding that out costs the
whole download (OpenFreeMap's `Noto Sans Regular` is 33.7 MB across the
full space). The chosen set is Latin-1 through Latin Extended-B and
combining diacritics, Latin Extended Additional, and General Punctuation
through Mathematical Operators, costing 0.81–2.11 MB per download
depending on the style.

Promotion survives as a second line, for ranges outside that set — see
`_promoteGlyphs` in `static/js/sw.js`.

**The layers menu** (`static/js/map_layer_sync_status.js`). Its dots report
the live cached/uncached/partial state of a whole basemap, not one area's
completeness — a different question with a different subject. Wiring this
probe into it would make a dot answer about an area the menu never names.

## The shared base layer is verified the same way (SNOW-929)

**It is not an exception, and it used to be treated as one.** The wide band
`warmBaseLayerWideBand` fetches the first time a basemap is shown pinned
tiles and nothing else. Two surfaces —
`basemap_downloaded_areas.js`'s base-layer row and `offline_audit.js`'s —
hardcoded `deps: []` on it, each under a comment asserting that a base
layer is tiles only because the area downloads sharing it carry the
documents between them. That had the sharing backwards. A user who never
downloads an area has no area download to carry anything: the style,
TileJSON, sprite and glyph ranges lived only in the unpinned
`snowdesk-basemap-v1` passive cache, which is FIFO-trimmed and evictable,
and on a first visit the style and sprite were not cached at all, because
MapLibre requests them before the service worker is in control. So the
device held a band it could not render, and the report called it ready.

`resolveBaseLayerPlan` now plans `activeBasemapRenderDependencyURLs(MAP)`
alongside the band and records the list as `deps`, so `areaState` verifies
a base layer on exactly the terms above. It costs 0.7–1.5 MB against bands
of 2.7–12.6 MB, and it is the difference between holding a map and holding
tiles nothing can read.

**The band dedupes against every bucket; the documents dedupe against
this one.** The asymmetry is deliberate and the review of #902 caught the
first cut getting it wrong. A TILE is available offline whichever bucket
holds it — `sw.js`'s `_searchPinnedBuckets` walks them all — so
`resolveBaseLayerPlan` filters the band against `pinnedBasemapCacheURLs()`,
the union, and spends nothing on a second copy of megabytes. A DOCUMENT is
the *same URL* for every area sharing the basemap, so the same union check
reads it as cached whenever any region download exists and copies nothing
into the base layer's bucket. Removing that region then takes the base
layer's only render dependencies with it — this decision's own defect,
reached from the other side, and silent: the record declares the full
list, so `areaState` reads `incomplete`, while a base row has no Repair
control and is filtered out of the manage panel. So the documents are
filtered against `_baseLayerBucketURLs(areaId)`, the bucket's own
contents. Around 1 MB buys the promise that this bucket renders on its own
and outlives any one area.

That read also replaced the old `_baseLayerBucketIsStale` predicate, which
opened and enumerated the same bucket to return a boolean. The plan needs
the entries themselves — for the staleness verdict and for the missing
documents — and two reads of one bucket is two chances for the answers to
disagree.

`areaState`'s `deps.length === 0 && area.kind !== 'base'` carve-out stays.
Its reason has changed rather than gone: an empty list on a base layer is
now a record written before SNOW-929, and that layer is re-warmed on the
next switch to its basemap with no user action — unlike an area, which
needs a repair. Reading it as `unverifiable` would put a warning on the
report that clears itself, for a bucket nobody chose and nobody can
repair.

**The staleness check judges tile entries only.** SNOW-863 evicts a
base-layer bucket whole when it holds an entry the current band does not
ask for, because SNOW-856 shipped z0-9 against a default band of z0-7 and
the old set is a superset — the missing-url plan finds nothing, so nothing
else can free the bytes. Putting the documents in the same bucket ends the
all-entries form of that check: every document is outside the tile set by
construction, so it would evict the band on the very warm that fetched it.

Folding the document list into the expected set is the other obvious
answer and is worse. The expected documents are derived from the LIVE
style, so they move whenever the provider moves them, while the tile set
is a pure function of band, camera and style. A provider renaming a sprite
path or adding a fontstack would then cost the user a 21 MB re-download.
So the judgement is `pwaBasemapDownloadCore.baseLayerStaleEntries(entries,
expectedTiles)` — the tile entries not in the band, `isTileEntryURL`
deciding which entries are tiles from the URL path alone. A document the
current plan happens not to name is left where it is: it is tens of
kilobytes, and it may well be the thing making the band renderable.

`isTileEntryURL` is pure, exported and truth-tabled
(`tests/js/test_basemap_base_layer.js`) because both ways of being wrong
are silent. Read a glyph range (`…/fonts/Noto%20Sans%20Bold/0-255.pbf` —
one segment, not a numeric triple) as a tile and every warm evicts the
band it just fetched; read a raster tile (OpenFreeMap's natural-earth
source serves `.png`) as a document and SNOW-863's migration quietly stops
firing.

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

## Addendum (SNOW-932): the same rule for the CONTENT half

SNOW-924 gave a download a second, independent half — the bulletins and
weather inside its boundary. It is the perishable one: tiles are a fixed
grid over fixed ground and never go stale, bulletins are new every day. It
added a roundel state, **`partial`**, for "tiles landed, content did not".

That state was *painted* and never *stored*. `_probeDone` returned eight
states and `partial` was not among them, so the next `renderControl()`
repainted `done` from the probe — and `renderControl()` fires on
`snowdesk:connectivity-changed`, which is exactly what a flapping signal
fires. The amber state was erased precisely when it had been earned.

**The rule above is the fix, applied to the second half.** An area is
verified by stored fact, not by probe alone — and content completeness is
the strongest case for it, because unlike `deps` it cannot be probed *at
all*. A bulletin absent from the bucket because the boundary contains none
is indistinguishable from one absent because the fetch fell over. Only the
run that assembled the plan knows, so the run writes it down:
**`contentIncomplete: true`** on the area's record, read back by
`_probeDone` and painted by `_renderControl` ahead of its `done` branch.

### Absence means fine

`contentIncomplete` is written `true` or **not written at all**. It is
**deleted** on a successful refresh, never set to `false`.

This is the whole backwards-compatibility story and it is worth stating
plainly, because the obvious alternative quietly breaks the estate. Every
record written before SNOW-924 carries neither `contentAt` nor
`contentIncomplete`. If absence read as a shortfall, the deploy that
introduced the field would turn every existing area amber overnight, each
with no shortfall behind it. So absence is the *only* representation of
"nothing wrong", and every reader — `_probeDone`, `manageRows`, `buildRow`,
the downloaded-areas reader — coerces with `!!` rather than comparing
against `false`. The delete-on-success path is the one to get exactly
right: a stale `true` strands an area amber forever, with the control that
would clear it reporting success each time.

### Three states, not two

`pwaBasemapDownloadCore.contentOutcome(content)` answers with a pair,
`{complete, incomplete}`, and `incomplete` is **not** the negation of
`complete`. The third state is a run with nothing to fetch — an area whose
boundary contains no bulletins, or a shell whose deps bundle predates the
content phase. It records neither field, because a stamp would claim a
freshness it never had and a flag would strand it amber.

`complete` also requires the PLAN to have been whole. SNOW-931 found that a
country the client never loaded is never *listed*, so its bulletins are
missing from a tally that reads `ok === total` over everything that was
listed — a shortfall no count over that list can see. `assembleAreaContentURLs`
returns `{urls, short}` for exactly this, and `short` makes a run incomplete
whatever its tally says.

### The remedy, and where it lives

A region has a roundel that refreshes on a tap. A **custom area does not** —
its framing overlay closes and never comes back — so the remedy for both
lives on the Manage downloads sheet:
`window.pwaBasemapDownloads.refreshAreaContent(areaId)`, which resolves the
record for either kind and goes through **`repair`, never `run`**. No tiles
are re-fetched. That is a user-visible promise rather than an
implementation detail: a refresh costs kilobytes of HTML over the same thin
connection the download existed for, where `run` would cost megabytes and
could raise an eviction confirm that destroys another area to make room for
a day-old bulletin.

### What the sheet must NOT say

A content-incomplete row is **not** dimmed, and does not reuse the
"Incomplete" line. Dimming on that sheet means "listed, but not available
offline", and this area *is* available offline — its tiles are whole and
its map draws. What is behind is the news on it. The row says **"Bulletins
out of date"** and carries a Refresh, which is a different condition from
an orphan or a row that cannot render, and must keep reading as one.
