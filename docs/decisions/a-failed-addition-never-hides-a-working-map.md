---
name: a-failed-addition-never-hides-a-working-map
description: Sync steps — failed Swisstopo or terrain addition is a verified.steps reason, not tiles failed; amber notice, onStage counts, Stop
status: current
last-reviewed: 2026-11-10
---

# A failed addition never hides a working map

SNOW-1487.

**Decision.** When a sync tries to ADD something to an area — the Swisstopo
map to a Swiss area downloaded on OpenFreeMap (`_addMissingBasemaps`,
SNOW-1478), or the terrain data to an area saved without it
(`_addMissingTerrain`, SNOW-1483) — and the addition fails, the map the area
already holds is not marked as failed. It still draws. So:

- `syncArea`'s `tiles` answer is the one the render check gives for the map
  the area holds. A failed addition no longer sets `tiles = 'failed'`.
- The failure is a **step**: `{step, reason, done, total, neededMb, freeMb}`
  in `syncArea`'s `steps`, where `step` is `basemap`, `terrain`, `deps` or
  `content` and `reason` is one of
  - `network` — the fetch fell short (the connection dropped);
  - `interrupted` — the reader pressed Stop, or the page went out of sight;
  - `budget` — the addition would take the downloads over the reader's limit
    (a sync never evicts another area to make room);
  - `quota` — the phone's storage cannot take it;
  - `plan` — the map's own documents, or the area's content plan, could not
    be read.
  Only failed steps are listed. `verifiedFromSync` copies them onto the
  record's `verified` stamp as `verified.steps`, and the next sync replaces
  them; a sync that finishes everything clears them.
- The verdict (`stagesFromRecord`, `verdictFor`) does not read `basemap` or
  `terrain` steps. The row's dot stays as the map reads (green for a map
  that works), and the Downloads sheet shows an amber notice under the row
  instead, one per step, saying why and offering the one thing to do: Try
  again (network, plan), Carry on (interrupted), Change limit (budget, which
  focuses the limit in the sheet's header), or nothing for a phone that is
  nearly full. A `deps` or `content` step already shows in the map and
  conditions answers, so it has no notice of its own.
- What landed before a failure stays in the area's bucket, and its bytes are
  counted on the record. The retry asks only for what the bucket still lacks
  (`missingFromAreaBucket`).

A sync also says what it is doing. `syncArea(areaId, onStage, signal)` tells
`onStage(step, {done, total})` each stage (`map`, `terrain`, `bulletins`,
`weather`, `things`) with the files landed of the files needed, and stops
when `signal` aborts: the fetch in flight is cancelled through the worker's
own cancel (`pwaWarmCacheCancel`), no later stage starts, and the step in
flight is recorded as `interrupted`. A stopped step is never a map fault,
and a sync stopped before it read the content stamps no content answer.

## Why

Hugo, 10 Nov 2026: a Swiss area on OpenFreeMap whose Swisstopo addition ran
out of signal turned red, "Map data missing", while its OpenFreeMap map drew
perfectly well offline. The reader could not tell that anything worked, nor
what had gone wrong. The run gave no sign of what it was doing while it ran,
and nothing could stop it.

The addition is an improvement on a working download, not part of what makes
it work. Folding its failure into the map answer made the dot claim the map
was broken, which was false, and lost the reason, which was the one thing the
reader could act on: wait for a signal, raise the limit, or free space on the
phone.

## Consequences

- `offline_ready_core.areaOutcome` still reads `tiles` and `content`, so an
  area whose addition failed counts as ready in the run's tally; the closing
  toast reads the steps (`pwaDownloadsSyncAll.outcomeOf`) and names the area
  as "didn't finish".
- The foot line under Sync all ("Last synced 14:02 · 1 area didn't finish")
  is derived from the records — the newest `verified.at` and the count with
  non-empty `verified.steps` — so it says the same after a reload.
- The copied offline report lists each area's `verified.steps`
  (`offline_audit_core.reportText`), the only place the `budget` and `quota`
  words and the MB pair appear.
- The inventory's Terrain line reads its own tiles (`measureAreaInventory`'s
  `terrainHeld`), not the map's answer, and its sizes are measured from the
  bucket by URL class, so a map never reads "0 MB".
- See [downloads-is-the-home-for-offline.md](downloads-is-the-home-for-offline.md)
  for the sheet this paints on, and
  [swiss-downloads-save-the-swisstopo-map.md](swiss-downloads-save-the-swisstopo-map.md)
  for the addition itself.
