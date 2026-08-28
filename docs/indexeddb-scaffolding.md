---
name: indexeddb-scaffolding
description: IndexedDB wrapper (window.pwaDb, static/js/db.js) — schema, queue:mutations/events, meta:app, data:favourites, log:sync, data:map_overlays
status: current
last-reviewed: 2026-08-28
---

# IndexedDB scaffolding

Client-side storage foundation for the Snowdesk PWA — spec §3.8, §7.2.
Every subsequent client-side spec ticket (mutation queue SNOW-376,
event buffer SNOW-385, cached bulletin data) writes to the same
per-app IndexedDB database, keyed by a small set of long-lived object
stores.

Loaded on every public page from `apps/public/templates/public/base.html`
as the first PWA script (deferred). Exposes exactly one surface:
`window.pwaDb`.

## Database

- Name: **`snowdesk-pwa-v1`** — the `v1` suffix is a namespace, not a
  schema version. Bumped **only** if the store namespace itself changes
  (e.g. a fundamental rework); store additions are handled by
  incrementing `DB_VERSION` inside the wrapper.
- Current schema version: **4** (SNOW-492 added `data:map_overlays`;
  SNOW-482 added `log:sync`; v2 added `data:favourites`).

## Object stores

Declared statically in `static/js/db.js::STORES`. Every store present
in the source is created on the first open() at that version and
never removed.

| Store              | keyPath         | autoIncrement | Consumer                     |
|--------------------|-----------------|---------------|------------------------------|
| `queue:mutations`  | `id`            | true          | SNOW-376 mutation queue (`window.pwaMutationQueue`) |
| `queue:events`     | `id`            | true          | SNOW-385 telemetry buffer    |
| `meta:sync`        | `resource`      | false         | last-sync timestamps         |
| `meta:app`         | `key`           | false         | install ts, first-launch, opt-in, `push.subscribed_before`, `mutations.principal` (SNOW-462 — last-seen principal for mutation-queue partitioning), `basemap.origins` (SNOW-487 — durable mirror of the SW's `_basemapOrigins` allowlist, written by `static/js/map.js` and lazily rehydrated by `static/js/sw.js`'s `_hydrateBasemapOrigins()` after an idle worker restart), `basemap.customAreas` (SNOW-522, array shape since SNOW-635 — every persisted custom-area basemap download, written/read by `static/js/map.js`'s `mapCustomDownloadControlInit`; see below), `basemap.regions` (SNOW-570 — one row per downloaded region, written/read by `static/js/map.js`'s `mapDownloadControlInit`; see below), `basemap.budgetMb` (SNOW-586 — device-local override of the standing pinned-download byte budget, `DOWNLOAD_BUDGET_MB` (500) if absent; read-only from this ticket, SNOW-588's managed-downloads UI is what will ever write it) |
| `data:favourites`  | `uuid`          | false         | SNOW-418 favourites offline cache |
| `log:sync`         | `id`            | true          | SNOW-482 sync-log panel — rolling record of recent real (un-cached) server round-trips, trimmed to the newest 100 rows |
| `data:map_overlays`| `key`           | false         | SNOW-492 map overlay offline cache — one row per resource (`'favourites'` / `'community_reports'`), written/read by `static/js/map_overlay_offline_cache.js` (`window.pwaMapOverlayCache`) |

`data:*` is a reserved namespace for cached server-data copies.
`data:favourites` (v2) was its first occupant; `data:map_overlays` (v4,
SNOW-492) is the second — see
[`docs/offline-first.md`](offline-first.md) §12.6 for the
cached-with-explicit-staleness contract it follows. When a further
consumer adds a store, bump `DB_VERSION` + add a migration branch in
`_runMigrations`.

### `data:map_overlays` row shape (SNOW-492)

```js
{
  key,        // 'favourites' | 'community_reports'
  geojson,    // the last successfully-fetched FeatureCollection, verbatim
  cached_at,  // ISO 8601 timestamp — observability only, not a store-level
              // expiry cutoff (favourites never expire; community reports
              // apply the existing 48h age-fade window at read-back time,
              // in static/js/map.js's dropExpiredCommunityReports)
}
```

### `meta:app` row shape — `basemap.customAreas` (SNOW-522, SNOW-586, SNOW-635)

Every persisted custom-area basemap download — see
[`offline-map.md`](offline-map.md#custom-area-download-snow-522) for the
full feature. SNOW-635 turned this from a single row into an ARRAY, one
entry per downloaded custom area — a confirmed download previously
replaced the one row outright; it now APPENDS a new entry under its own
freshly minted `id` (`generateCustomAreaId`), so more than one custom
area can exist at once:

```js
{
  key: 'basemap.customAreas',
  value: [
    {
      id,           // 'custom-<uuid>' (generateCustomAreaId), or the
                    // reserved legacy 'custom' for an area migrated in
                    // place from a pre-SNOW-635 device's single row (see
                    // below) — this is also the area's pinned Cache
                    // Storage bucket's own name suffix
      ordinal,      // SNOW-635 — one above the highest ordinal already
                    // stored when this area was confirmed (1 for the
                    // very first). No persisted counter; gappy after a
                    // delete, deliberately (see map.js's own comment)
      bbox,         // [west, south, east, north] in degrees — the framed area
      band,         // [minZ, maxZ], currently always [10, 14] (MICRO_BAND)
      centre_tile,  // {z, x, y} — stored, but no longer what the done-probe
                    // checks (see below); kept alongside bbox/band as the
                    // full basemap_tiles.py centre_tile shape
      name,         // SNOW-635 — set ONLY by a rename
                    // (static/js/map_downloads_manager.js's Rename
                    // control, via window.pwaBasemapDownloads.rename()).
                    // ABSENT on the STORED record until then — the default
                    // "Custom area N" label is filled in on every READ
                    // instead (map.js's basemapDownloadedAreas(), from
                    // `ordinal`), never written back here, which is what
                    // keeps it translatable. (Through SNOW-634 this was
                    // always set at download time, off the control's own
                    // data-area-label attribute — that attribute is gone.)
      template,     // SNOW-632 — the tile URL template this run actually
                    // fetched, recorded so a later run can tell whether
                    // the active basemap has changed since
      bytes,        // this run's own reported on-disk size, recorded
                    // outright — never accumulated, and (SNOW-635) never
                    // needing to be, since every area now owns a bucket
                    // no other run ever writes into
      savedAt,      // ISO 8601 timestamp of the confirmed download
    },
    // ...one entry per downloaded custom area
  ],
}
```

**Migration.** Lazy, on first read, inside `basemapDownloadedAreas()`
(`static/js/map.js`'s `_readCustomAreas`) — the same boot-path read the
roundel's own probe already makes. If the legacy single-row
`basemap.customArea` is present and `basemap.customAreas` is not, it is
wrapped as a one-entry array (`id: 'custom'`, `ordinal: 1`) and the old
key is deleted. Best-effort: a failed write degrades to reading the
legacy row as a one-entry list for that call, never throws — this sits on
the same read path both the roundel and the manage sheet depend on. The
legacy area keeps id `'custom'` rather than a freshly minted one, because
Cache Storage has no rename and its existing
`snowdesk-basemap-pinned-custom` bucket has to keep resolving under the
same name.

Each entry only records *where* the frame was — whether it is actually
downloaded is never read off it directly, always re-probed against real
pinned-cache contents — every per-area bucket, unioned (SNOW-586,
`pinnedBasemapCacheURLs`). The code has checked FULL coverage — every tile
of `buildBlob(bbox, ...band)`, via
`static/js/basemap_download_core.js`'s `blobFullyCached` — since SNOW-570,
not a `centre_tile` proxy: a single cached tile is not evidence the whole
area is available offline (a neighbouring download can cache one tile of
an area without covering it). This is the same "layers menu is a live
cache-state dashboard" invariant every download control follows — see
[`offline-map.md`](offline-map.md#downloaded-tiles-overlay-snow-570-rings-removed-snow-587-sheet-bound-snow-645).

### `meta:app` row shape — `basemap.regions` (SNOW-570, SNOW-583, SNOW-586)

One entry per region the user has deliberately downloaded — see
[`offline-map.md`](offline-map.md) for the full "Download basemap"
feature. Written/read by `static/js/map.js`'s `mapDownloadControlInit`;
a re-download of the same region replaces only that region's own entry:

```js
{
  key: 'basemap.regions',
  value: [
    {
      region_id,  // e.g. 'CH-4115'
      z,          // SNOW-583 — the blob's own clipped tile ranges
                  // ({"<z>": [xmin, xmax, ymin, ymax]}), i.e. the tile set
                  // the run ACTUALLY fetched (region plus a tile of
                  // margin), not the region's bbox. `_probeDone` reads
                  // this back directly and hands it to blobFullyCached,
                  // so coverage is checked against what was downloaded
      band,       // [minZ, maxZ] the run actually fetched
      name,       // SNOW-586 — the region's display name
                  // (FEATURE_BY_REGION_ID[region_id].properties.name),
                  // recorded so eviction copy never depends on
                  // regions.geojson still being loaded
      bytes,      // SNOW-586 — accumulated, not replaced, across repeat
                  // downloads: this region's pinned bucket is keyed on
                  // region_id ALONE, not per-basemap, so a download under
                  // a SECOND basemap adds genuinely new bytes to the one
                  // shared bucket
      savedAt,    // ISO 8601 timestamp of the confirmed download
    },
    // ...one entry per downloaded region
  ],
}
```

Like `basemap.customAreas`, this only records *what was asked for*. The
download roundel still re-probes real pinned-cache contents before reading
`done` — `z` tells it *which* tiles to look for, never *whether* they are
there. `bytes` is the one field read for its own sake rather than as a
probe input: `planEviction` sums it to decide what a new download would
displace (see [`offline-map.md`](offline-map.md)).

### `log:sync` row shape (SNOW-482)

Written by `static/js/pwa_offline.js`'s `appendSyncLogEntry` for every
same-origin response that did NOT carry `X-SW-Cache: hit` (i.e. a real
network round-trip, not a Cache-Storage replay served by
`static/js/sw.js`) and whose path `isLoggableSyncPath` accepts — that
excludes static assets, and `/api/telemetry`, which the page POSTs to
itself every 30 seconds and would otherwise be most of the panel.
`static/js/sync_log.js` filters the same paths again on read, so a
device that already banked telemetry rows under the old rule shows a
clean panel before those rows age out:

```js
{
  id,     // autoIncrement primary key
  at,     // ISO 8601 timestamp
  path,   // request pathname, e.g. "/api/ratings/"
}
```

`appendSyncLog(entry)` writes the row then trims the store to the
newest 100 by deleting the lowest ids via a cursor — `getAll(store,
limit)` would return the wrong end (lowest ids first), so trim and the
newest-first `getSyncLog(limit)` read both walk a cursor instead. Read
out by the manage-page sync-log panel (`static/js/sync_log.js`) behind
the `sync_log` waffle flag.

### `queue:mutations` row shape (SNOW-376)

No indexes — the consumer filters/orders rows in memory over
`getAll('queue:mutations')`, exactly as the `queue:events` telemetry
buffer does.

```js
{
  id, idempotency_key, method, url, headers, body, created_at,
  attempts, status,          // 'queued' | 'retry-scheduled' | 'failed'
  next_attempt_at,           // epoch ms
}
```

Full row-shape rationale, backoff schedule, and Background Sync
integration: [`mutation-queue.md`](mutation-queue.md).

## Public API

```js
window.pwaDb = {
  open(),                     // Promise<IDBDatabase>. Memoised per page.
  get(store, key),            // Promise<value | undefined>
  put(store, value),          // Promise<key>
  delete(store, key),         // Promise<void>
  getAll(store, limit),       // Promise<value[]>  (limit optional)
  count(store),               // Promise<number>
  clear(store),               // Promise<void>
  appendSyncLog(entry),        // Promise<void> — put + trim to newest 100 (log:sync, SNOW-482)
  getSyncLog(limit),           // Promise<value[]> — newest first (log:sync, SNOW-482)
  context(),                  // eight-field envelope context (see below)
  isResetRequired(),          // boolean — true after a migration failure
  DB_NAME, DB_VERSION, STORE_NAMES,   // read-only introspection
};
```

Errors from every method are surfaced as rejected promises; callers
`.catch()` and decide their own fallback. The wrapper does NOT swallow
errors — that's the caller's contract (unlike `pwa_reset.js`, which is
a fire-and-forget wipe).

## Migrations

Every schema bump is applied inside `onupgradeneeded`, which is
**idempotent** — re-running against a DB already at the target version
is a no-op because `createObjectStore` is guarded by
`objectStoreNames.contains()`. Add a new store by adding it to `STORES`
and bumping `DB_VERSION`; existing installations upgrade on next open.

## Reset Required state

A migration that throws is fatal for the session:

1. The upgrade transaction is aborted.
2. `pwaDb.isResetRequired()` flips to `true`.
3. Every subsequent `open()` returns a rejected promise.
4. The full-screen `#pwa-reset-required` overlay
   (`templates/includes/_pwa_reset_required.html`) is revealed.
5. The overlay's "Reset now" CTA is bound at reveal-time to
   `window.pwaResetLocalData` — the SNOW-378 six-step wipe. No
   confirm() prompt because the overlay itself is already the
   unmissable confirmation.

`VersionError` (from opening the DB at an older version than the one
on disk — usually a rollback) routes through the same path.

## Eight-field envelope context helper

`pwaDb.context()` returns the fixed session/device context every
telemetry envelope will lift (spec §16.1). Cached per page load — a
fresh call is essentially free.

```js
{
  session_id,        // sessionStorage-scoped UUID (random per tab)
  user_id,           // <meta name="pwa-user-id">, or null for anon
  client_version,    // <meta name="pwa-app-version">
  platform,          // "ios" | "android" | "web"
  install_state,     // "standalone" | "browser"
  sw_state,          // "activated" | "none"
  connection,        // navigator.connection.effectiveType or "unknown"
}
```

The three envelope fields the helper does NOT cover are the caller's
responsibility on each `emit()`: `event`, `timestamp`, `properties`.

## Interaction with SNOW-378 (Reset Local Data)

`pwa_reset.js` keeps the six-step wipe (SW → caches → IndexedDB →
web-storage → reload). Its `KNOWN_DB_NAMES` fallback list — used on
browsers without `indexedDB.databases()` — contains this DB's name so
the wipe covers it even without the enumeration API.

## Tests

`tests/js/test_db.js` (Vitest + jsdom + fake-indexeddb — `tox -e js`,
see [`client-side-tests.md`](client-side-tests.md)) covers:

1. Fresh open — all static stores exist at the current version
   (currently 4), including `log:sync` and `data:map_overlays`.
2. Round-trip — `put/get/delete/getAll/count/clear` on `queue:events`.
3. `context()` returns the expected seven envelope-context keys with
   sane defaults, and is stable within a page load.
4. v1→v4, v2→v4, and v3→v4 migrations — open an older-version DB,
   upgrade, and assert the new store(s) exist without disturbing
   existing rows.
5. `appendSyncLog`/`getSyncLog` — newest-100 trim and newest-first read
   order.

`tests/js/test_db_reset_required.js` covers the Reset Required state —
arrange a pre-existing higher-version DB, assert `pwaDb.open()` rejects,
the flag flips, and the overlay is revealed. It lives in its own file
because `_resetRequired` is a one-way per-module latch (Vitest isolates
each test file, giving a fresh module).

## See also

- [`telemetry-pipeline.md`](telemetry-pipeline.md) — the first consumer
  of `queue:events` and `context()` (SNOW-385).
- [`offline-first.md`](offline-first.md) — the umbrella non-negotiables
  index.
- [`push-notifications.md`](push-notifications.md) — consumer of
  `meta:app` (`push.subscribed_before` key, SNOW-380) for launch-time
  Web Push subscription re-verification.
- [`mutation-queue.md`](mutation-queue.md) — the real consumer of
  `queue:mutations` (SNOW-376).
