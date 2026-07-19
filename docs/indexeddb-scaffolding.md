---
name: indexeddb-scaffolding
description: IndexedDB wrapper (window.pwaDb, static/js/db.js) — schema versioning, queue:mutations/events, meta:app, data:favourites, Reset Required
status: current
last-reviewed: 2026-07-19
---

# IndexedDB scaffolding

Client-side storage foundation for the Snowdesk PWA — spec §3.8, §7.2.
Every subsequent client-side spec ticket (mutation queue SNOW-376,
event buffer SNOW-385, cached bulletin data) writes to the same
per-app IndexedDB database, keyed by a small set of long-lived object
stores.

Loaded on every public page from `public/templates/public/base.html`
as the first PWA script (deferred). Exposes exactly one surface:
`window.pwaDb`.

## Database

- Name: **`snowdesk-pwa-v1`** — the `v1` suffix is a namespace, not a
  schema version. Bumped **only** if the store namespace itself changes
  (e.g. a fundamental rework); store additions are handled by
  incrementing `DB_VERSION` inside the wrapper.
- Current schema version: **2**.

## Object stores

Declared statically in `static/js/db.js::STORES`. Every store present
in the source is created on the first open() at that version and
never removed.

| Store              | keyPath         | autoIncrement | Consumer                     |
|--------------------|-----------------|---------------|------------------------------|
| `queue:mutations`  | `id`            | true          | SNOW-376 mutation queue (`window.pwaMutationQueue`) |
| `queue:events`     | `id`            | true          | SNOW-385 telemetry buffer    |
| `meta:sync`        | `resource`      | false         | last-sync timestamps         |
| `meta:app`         | `key`           | false         | install ts, first-launch, opt-in, `push.subscribed_before` |
| `data:favourites`  | `uuid`          | false         | SNOW-418 favourites offline cache |

`data:*` is a reserved namespace for cached server-data copies.
`data:favourites` (v2) is its first occupant — see
[`docs/offline-first.md`](offline-first.md) §12.6 for the
cached-with-explicit-staleness contract it follows. When a further
consumer adds a store, bump `DB_VERSION` + add a migration branch in
`_runMigrations`.

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

`tests/e2e/test_pwa_db.py` covers:

1. Fresh open — all four static stores exist at version 1.
2. Round-trip — `put/get/delete/getAll/count/clear` on `queue:events`.
3. `context()` returns the expected seven envelope-context keys with
   sane defaults.
4. Reset Required state — arrange a pre-existing higher-version DB,
   assert `pwaDb.open()` rejects, the flag flips, and the overlay is
   revealed.

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
