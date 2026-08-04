---
name: per-area-pinned-basemap-caches
description: One Cache Storage bucket per downloaded basemap area; a run records its own bytes; evicted on bbox OR basemap-template change (SNOW-632)
status: current
last-reviewed: 2026-08-04
---

# One pinned Cache Storage bucket per downloaded area

**Decision.** SNOW-586 gives every deliberate "Download basemap" run its
own Cache Storage bucket — `snowdesk-basemap-pinned-<areaId>` — rather
than writing every download into the one shared
`snowdesk-basemap-pinned-v1` cache SNOW-521/522 used. The shared cache's
`BASEMAP_PINNED_CACHE_MAX_ENTRIES` (5000) entry-count FIFO trim is
removed outright, not raised or made cleverer. A standing byte budget
(`DOWNLOAD_BUDGET_MB = 500`, device-local, overridable via `meta:app`'s
`basemap.budgetMb`) replaces it, and exceeding it evicts whole AREAS —
`caches.delete(bucketName)` — oldest `savedAt` first, only after naming
them in a confirm banner and getting a yes.

**Why.** The old design's trim deleted the oldest-*inserted* cache
ENTRIES with no record of which download they belonged to. A new
download's trim could therefore delete tiles from the middle of an
earlier, unrelated area — that area was not removed, it was
**perforated**, silently. This was invisible while the roundel's
done-probe checked a single centre tile as a proxy for "this download
completed"; SNOW-570 made that a full-coverage check, so a perforated
region honestly started reverting to undownloaded, which is what exposed
the bug. Two structural problems compounded it: the largest Swiss
micro-region (Westliches Mittelland, 5,879 tiles across z10–14) is larger
than the whole 5000-entry cap, so it evicted its own tiles mid-run and
could never be held complete; and the two limits spoke different
languages — `DOWNLOAD_CEILING_MB` gates one run in megabytes, the cache
cap counted entries — so a run could pass the ceiling check and still be
impossible to keep.

**Why a bucket per area rather than an index inside one cache.** An
index (a JS-side map of which cache keys belong to which download,
kept in `meta:app`) can go stale independently of the cache it describes
— exactly the class of bug this ticket exists to remove. Cache Storage
is already built for what this needs: `caches.keys()` enumerates every
bucket, `caches.open(name)` opens one, and `caches.delete(name)` drops
one **atomically** — no per-entry loop, no partial-delete race, no
separate bookkeeping that could disagree with what is actually on disk.
Evicting an area becomes one call that cannot leave half a region behind,
and the "one micro-region is bigger than the whole cap" special case
disappears because the cap is no longer per-cache at all.

**Why bytes, not entries.** `DOWNLOAD_CEILING_MB` (the per-run ceiling)
and the old entry cap spoke different units, so a run passing the first
check could still be un-keepable under the second. `DOWNLOAD_BUDGET_MB`
puts the standing budget in the same unit as the per-run ceiling for the
first time — `planEviction` (`basemap_download_core.js`) sums each area's
recorded `bytes` (measured as `_warmCache` writes, via
`basemap_cache_core.js`'s `responseBytes`) against the budget, the same
arithmetic shape the ceiling already uses.

**Why the budget is device-local, not on `Subscriber`.** Cache Storage is
per-browser — a value on the account would describe storage that does
not exist on every device the account is used from. `meta:app`'s
`basemap.budgetMb` (read, never written, by this ticket — SNOW-588's
managed-downloads UI is what will ever change it) keeps the setting where
the thing it describes actually lives.

**Why 500 MB is a fixed number, not derived from
`navigator.storage.estimate()`.** SNOW-568's storage-quota pre-flight
(`hasStorageHeadroom`) already answers "will the browser let this download
succeed right now" — a live, per-device, per-moment number. `DOWNLOAD_BUDGET_MB`
answers a different question: "how much is Snowdesk itself willing to
hold onto, so the user has a predictable, explainable ceiling on how much
of their device this one feature claims" — independent of how much quota
happens to be free at any given moment. The two checks run at different
times for different reasons and are not meant to collapse into one.

**Why no migration.** Nobody has a completed per-area download yet (this
scoping question was settled before the feature had shipped its first
real user data), so `sw.js`'s `activate` sweep drops the old shared
`snowdesk-basemap-pinned-v1` cache outright rather than trying to split
its contents up after the fact — there is no way to recover which tiles
in that cache belonged to which download, since that is exactly the
information the old design never recorded.

## Consequences

- **Overlapping areas duplicate tiles on disk, deliberately.** A tile
  shared by two adjacent regions occupied one entry in the old single
  shared cache; with a bucket per area it is stored once per bucket that
  covers it. This is accepted, not overlooked: a self-sufficient bucket
  is exactly what makes "evicting A can never perforate B" true, which is
  the whole point of this change. The overlap is real at z10–z12 (a z10
  tile spans ~40 km, so neighbouring micro-regions certainly share tiles
  at that zoom) but the tile count is dominated by z14, where areas
  overlap only along their boundary margin. Downloads have never
  deduplicated *bandwidth* either — `_warmCache`'s worker fetches every
  URL in its list unconditionally, with no already-cached check — so this
  change costs storage, not extra network requests.
  **Candidate future optimisation, not built:** source an already-held
  tile from a sibling bucket rather than re-fetching it, while still
  keeping a copy in each bucket that needs it (so eviction stays safe).
- **A bucket is evicted outright on a bbox OR a basemap-template change,
  so it always holds exactly one basemap's tiles (SNOW-632, amending this
  decision's original design).** The bucket is keyed on area id alone, not
  on basemap, so a region or custom area re-downloaded under a DIFFERENT
  basemap at the SAME ground would otherwise leave the previous basemap's
  tiles sitting in the bucket alongside the new run's — bloating it, and
  leaving whatever gets recorded for it wrong either way (see the next
  bullet). Both download controls' `beforeWarm` (run by
  `basemap_download_runner.js` as the last step before the warm-cache
  call — see its own module header for why the ordering lives there)
  compares the ACTIVE tile template against the one the existing record
  was downloaded with, stored alongside it as `template`, and calls
  `evictBasemapAreas` first when they differ — the same unconditional,
  no-confirmation bucket delete the custom-area control already used for a
  bbox change alone, now covering a template change too and extended to
  the region control, which previously had no `beforeWarm` at all. A
  record with no `template` (written before this ticket) is treated as a
  mismatch — "unknown" reads safer as "different", costing one redundant
  re-download rather than an unaccounted-for stale bucket. This doesn't
  change what the roundel's `done` state means (it was always a real probe
  against the active template, per-basemap already); it changes what the
  bucket holds and what gets recorded for it.
- **Byte totals are a run's own reported figure, recorded outright —
  never accumulated onto the previous record, and never re-measured from
  the bucket.** An earlier version of this fix tried the latter:
  `previousBytes + result.bytes` on every successful run doubled the
  recorded total on every same-bbox, same-basemap RETRY (identical URLs
  land in the identical bucket — `cache.put` OVERWRITES each key rather
  than adding to it, so the bucket does not grow) — `planBasemapDownloadBudget`
  plans evictions off that recorded total, so the inflation was not
  cosmetic: an 8× same-basemap repeat of one 61 MB area was observed
  reading as 488 MB used against the 500 MB default budget. The fix that
  shipped instead — `measurePinnedBucketBytes` reading the bucket's real
  Cache Storage size once a run settled — looked exact but was inert in
  production: a browser always sends `Accept-Encoding: gzip`, so a live
  tile response carries NO `Content-Length` header at all —

      curl -sS -D - -o /dev/null --compressed <tile-url>
        content-encoding: gzip          # no content-length

      curl -sS -D - -o /dev/null -H "Accept-Encoding:" <same tile-url>
        content-length: 45896           # only with compression off

  — and `measurePinnedBucketBytes` sums `Content-Length` only (deliberately:
  `cache.match()` hands back a Response without reading it, so a header sum
  is N cheap lookups where a `blob()` sum would be N decompressions — see
  that function's own docstring). It therefore measured 0 for every real
  bucket and fell back to the run's own reported `bytes` on every single
  run, which is a more roundabout way of arriving at exactly the figure
  this design now records directly. Eviction on a bbox OR template change
  (previous bullet) is what makes recording the run's own figure exact
  rather than merely convenient: since a bucket only ever holds ONE run's
  worth of tiles by the time `finish` runs, that run's own reported total
  IS the bucket's whole total, with no arithmetic needed to reconcile it
  against anything left over from before. A `meta:app` write that still
  fails after a successful `_warmCache` run still leaves an orphan bucket
  the budget doesn't know about until `basemap_manage_core.js`'s
  reconciliation measures it — via the same `Content-Length`-only
  `measurePinnedBucketBytes`, so an orphaned bucket of gzipped tiles reads
  ~0 MB there too. That under-report is real and pre-existing, not
  introduced by this ticket; fixing it would need a blob-based fallback
  the way `responseBytes` (`basemap_cache_core.js`) already has, which is
  future work, not this decision.
- **`Content-Length` under-reads a gzipped tile's true on-disk size** —
  `responseBytes` falls back to measuring a cloned blob when the header
  is absent, which is exact, but the header (when present) reports the
  *compressed* transfer size. Acceptable for a budget whose purpose is a
  predictable ceiling, not a byte-exact disk audit.
- **`DOWNLOAD_CEILING_MB` (200, per run) stays separate from
  `DOWNLOAD_BUDGET_MB` (500, standing).** They are now the same unit,
  which is the reconciliation this ticket set out to make, but collapsing
  them into one number was explicitly out of scope.
