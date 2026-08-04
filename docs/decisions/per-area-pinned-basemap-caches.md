---
name: per-area-pinned-basemap-caches
description: One Cache Storage bucket per downloaded basemap area; recorded bytes are a post-run measurement (SNOW-632), not an accumulation
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
- **A region downloaded under two different basemaps shares one bucket**
  (the bucket is keyed on area id alone, not on basemap), so a basemap
  switch genuinely adds new tiles (different URLs, different origin) to
  it, and an eviction of that area removes both basemaps' tiles together.
  The roundel's `done` state still stays per-basemap (a real, unrelated
  probe against the active template) — downloading on Standard and
  switching to Swisstopo still reads `idle` even though the bucket now
  holds both. How the bucket's *recorded* size reflects that sharing is
  the next bullet, amended by SNOW-632.
- **Byte totals are measured off the bucket after every completed run,
  not accumulated (SNOW-632, amending this decision's original design).**
  The original design recorded `previousBytes + result.bytes` on every
  successful run — reasoning that a basemap switch, per the bullet above,
  genuinely adds new tiles to the shared bucket, so the recorded total
  had to grow with it. That arithmetic could not tell a basemap switch
  apart from a same-basemap RETRY, which re-fetches the identical URLs
  into the SAME bucket — `cache.put` OVERWRITES each key rather than
  adding to it, so the bucket does not grow — and so it doubled the
  recorded total on every such repeat with nothing new on disk to show
  for it. `planBasemapDownloadBudget` plans evictions off that recorded
  total, so the inflation was not cosmetic: it evicted other areas
  early — an 8× same-basemap repeat of one 61 MB area was observed
  reading as 488 MB used against the 500 MB default budget.
  `measurePinnedBucketBytes` (`static/js/map.js`) fixes this by reading
  the bucket's real on-disk size once the run settles, which gets every
  case right for free — a same-basemap retry's unchanged bucket measures
  unchanged, a basemap switch's real new bytes are measured — falling
  back to the run's own reported `bytes` only when the measurement reads
  0 (an unreadable bucket, not an empty one; see that function's
  docstring). A `meta:app` write that still fails after a successful
  `_warmCache` run still leaves an orphan bucket the budget doesn't know
  about until `basemap_manage_core.js`'s reconciliation measures it the
  same way — that gap is unchanged by this amendment.
- **`Content-Length` under-reads a gzipped tile's true on-disk size** —
  `responseBytes` falls back to measuring a cloned blob when the header
  is absent, which is exact, but the header (when present) reports the
  *compressed* transfer size. Acceptable for a budget whose purpose is a
  predictable ceiling, not a byte-exact disk audit.
- **`DOWNLOAD_CEILING_MB` (200, per run) stays separate from
  `DOWNLOAD_BUDGET_MB` (500, standing).** They are now the same unit,
  which is the reconciliation this ticket set out to make, but collapsing
  them into one number was explicitly out of scope.
