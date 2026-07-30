---
name: mutation-queue
description: Client mutation queue — window.pwaMutationQueue, queue:mutations shape, Idempotency-Key, backoff, Background Sync, principal partitioning
status: current
last-reviewed: 2026-07-20
---

# Client mutation queue

Client-side outbox for state-changing requests — spec §7 / §12.4. Sits
behind the `window.pwaMutationQueue` surface first stubbed in SNOW-384
(`static/js/mutation_queue.js`); this ticket (SNOW-376) fills in the real
queue with no call-site changes for any adopter.

**Scope**: this ticket ships the machinery only. The existing HTMX
(`hx-post`) subscription/push call sites are NOT converted to JS calls.

## Consumers

- **`static/js/report.js`** (SNOW-420) — the first real consumer.
  Field-report submission (`apps.observations.views.report_submit`) is routed
  through `window.pwaMutationQueue.enqueue()` instead of `hx-post`, so a
  report tapped offline is captured immediately, persisted, and replayed
  on reconnect. `report.js` stamps a hidden `observed_at` input with the
  tap-time instant (`new Date().toISOString()`) before enqueuing — this is
  what lets a report submitted offline record when the user actually
  observed the problem rather than whenever the queued mutation happens to
  replay; `report_submit` validates it (shape + plausibility window) and
  falls back to the model's `timezone.now` default when absent. The
  optimistic confirmation is rendered client-side immediately, cloned from
  a `<template>` embedded in the form partial (server-rendered so
  copy/i18n/design-tokens live in one place), and reveals a "will sync
  when you're back online" line when the tap happened offline. See
  `apps/observations/views.py`'s module docstring for the server-side
  `observed_at` validation contract, and
  `tests/e2e/test_offline_observation_submit.py` for the full offline →
  reconnect journey test.

- **`static/js/favourites.js`** (SNOW-479) — the second consumer.
  Favourite *creation* (`apps.favourites.views.favourite_create`) is routed
  through `window.pwaMutationQueue.enqueue()` instead of `hx-post`, so a
  pin saved offline is captured immediately and replayed on reconnect
  (rename/delete stay online-only `hx-post`). On enqueue `favourites.js`
  dispatches `snowdesk:favourite-pending {lat, lon, name}`; `static/js/map.js`
  draws a synthetic half-opacity `pending` marker (no uuid) so the save is
  visible at once. That pending pin becomes the authoritative server pin
  when the drain re-dispatches `snowdesk:favourites-changed` (see "Drain →
  favourites refresh" below). Because *all* creates now go through the
  queue via `fetch`, the favourites-cap case can no longer render its
  `_favourite_limit.html` inline: `favourite_create` returns **409** at the
  cap (a non-retry 4xx, so the queue treats it as an immediate permanent
  failure — see the state machine), which fires the standard failure toast
  + nav badge; `favourites.js`'s `pwa:mutation-failed-permanent` listener
  drops the now-doomed pending pin. See
  `tests/e2e/test_offline_favourite_submit.py` for the full journey +
  cap-failure test.

### Drain → favourites refresh (SNOW-479)

`drain()` dispatches a `snowdesk:favourites-changed` DOM event whenever a
pass syncs ≥1 row. This is how an offline-created favourite's optimistic
`pending` pin is swapped for the real server pin: `map.js` re-fetches the
authoritative favourites collection (now online) and `setData`s it,
replacing the whole feature set. The event is deliberately queue-neutral —
it fires for *any* successful drain, not just favourite rows — so a
report-only drain harmlessly triggers one cheap, eligible-gated favourites
refetch rather than the queue needing to know which consumer owned each row.

## Row shape (`queue:mutations`)

Declared in `static/js/db.js::STORES` (SNOW-375, keyPath `id`,
autoIncrement; no indexes — rows are filtered/ordered in memory over
`getAll('queue:mutations')`).

```js
{
  id,                 // autoIncrement primary key
  idempotency_key,     // minted once at enqueue (crypto.randomUUID()),
                        // sent on EVERY replay of this row, unchanged
  method,               // 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  url,
  headers,              // caller-supplied; Idempotency-Key is added at
                        // replay time, not stored in this object
  body,                 // caller-supplied — must be structured-cloneable
                        // (a JSON string is the expected shape; the
                        // caller is responsible for CSRF headers too —
                        // see "CSRF on replay" below)
  created_at,           // ISO 8601 timestamp, set at enqueue
  attempts,             // starts at 0; incremented for every attempt that
                        // did NOT get a 2xx — a retry OR a permanent 4xx
                        // (an attempt was made either way)
  status,               // 'queued' | 'retry-scheduled' | 'failed'
  next_attempt_at,       // epoch ms; row is eligible for replay once
                        // Date.now() >= next_attempt_at
  principal,             // <meta name="pwa-user-id"> value (or null for
                        // anonymous) at ENQUEUE time — see "Account-change
                        // partitioning" below (SNOW-462)
}
```

## State machine

| Response                          | Outcome                                                          |
|------------------------------------|-------------------------------------------------------------------|
| 2xx                                | Success — row deleted; counts toward the drain's reported count. |
| `{408, 429}`, any 5xx, network error | Retry — `attempts += 1`, `next_attempt_at = now + backoff(attempts)`, `status = 'retry-scheduled'`. |
| Any other 4xx (400/401/403/404/409/410/422/…) | Permanent failure on the FIRST attempt — `attempts += 1` (the attempt that just failed), `status = 'failed'`, no further retry. |
| Retry count reaches `MAX_ATTEMPTS` (20) without a 2xx | Permanent failure — `status = 'failed'`, same as above. |

`attempts` always counts attempts actually made, never attempts still
pending — a row marked `failed` on the first permanent-4xx response
carries `attempts: 1`, not `0`, and the `pwa.mutation.failed_permanent`
telemetry event's `attempts` property reflects the same value (so a
PostHog query can distinguish "failed on the first attempt" from "never
attempted").

Classification lives in `static/js/mutation_queue_core.js::classifyStatus`
— shared, byte-for-byte, between the page drain loop and the service
worker's Background Sync self-drain path (see below), so a row is never
classified differently depending on which one processes it.

## Backoff schedule

`backoffDelayMs(attempts)` (`mutation_queue_core.js`): `2^attempts`
seconds, capped at 300s — 2s, 4s, 8s, 16s, 32s, then 300s from attempt 6
onward. `MAX_ATTEMPTS = 20`.

## Permanent failure — toast + nav badge

A row that lands in `status: 'failed'` (either branch above):

1. Emits the critical `pwa.mutation.failed_permanent` telemetry event
   (`static/js/telemetry.js`'s `CRITICAL_EVENTS` — fires
   `navigator.sendBeacon` immediately regardless of the opt-in state).
2. Reveals `templates/includes/_toast_banner.html` (`#mutation-queue-toast`)
   — a full-width toast pinned to the TOP of the page (not an overlay,
   distinct from the bottom-centred `_toast.html`). Auto-dismisses after
   10s; the "×" button dismisses immediately. The reveal is a pure
   Tailwind-utility class toggle (`hidden` / `-translate-y-full opacity-0`
   → `translate-y-0 opacity-100`) — no custom CSS.
3. Refreshes the nav sync badge (`templates/includes/nav.html`,
   `[data-sync-badge]`) — text shows the live non-`failed` row count
   (singular/plural), hidden entirely when that count is zero, and swaps
   to the `bg-status-error-bg` / `text-status-error-text` tokens when any
   row in the store has `status: 'failed'`.
4. Dispatches a queue-neutral `pwa:mutation-failed-permanent` DOM event
   (`{method, url, reason}`) so a consumer can undo its optimistic UI for
   the failed mutation (SNOW-479: `favourites.js` filters on the create
   URL and drops the pending pin when a queued favourite-create is
   permanently rejected — notably the 409 at the favourites cap).

`window.pwaMutationQueue.markFailed(operation, reason)` is the same
surface for a caller-driven report (a mutation attempted outside the
queue) — it fires the same telemetry event and reveals the same
toast/badge state without needing a stored row.

## Account-change partitioning (SNOW-462)

Offline mutations carry no principal binding by default: if user A
queues a mutation offline, signs out, and user B signs in on the same
browser before reconnect, a naive replay would attribute A's mutation to
B. Every row is stamped `principal` at enqueue time
(`<meta name="pwa-user-id">`, `null` for anonymous — see the row shape
above), and two defence-in-depth layers keep a stale principal from ever
replaying:

1. **Reconcile on load** — `_reconcilePrincipal()` runs first thing in
   `_wireLifecycle()` (fire-and-forget, so it never blocks wiring the
   rest of the lifecycle). It compares the current principal against the
   last-seen one persisted in the `meta:app` store under the key
   `mutations.principal` (`{key: 'mutations.principal', value: <pk-or-
   null>}`). On a mismatch it calls `clear()` — wiping the ENTIRE queue,
   not just rows belonging to the old principal, since a queue with no
   principal binding predates this ticket and any leftover row is
   untrustworthy — and persists the new principal.
2. **Drain-guard** — `_processRow()` re-checks each row's stamped
   `principal` against the CURRENT principal immediately before replay.
   A mismatch deletes the row without a network request. This is the
   race backstop for the window between an account change and the next
   reconcile — notably a Background Sync firing from the service worker
   with no tab open, where `static/js/sw.js`'s `_selfDrainMutations()`
   applies the same guard best-effort (it reads the `mutations.principal`
   `meta:app` row directly, since a worker has no `<meta>` tag to read;
   skipped entirely if that store is unavailable on a worker-created DB
   rather than guessing). The SW-side guard runs ONLY when no tab is
   open (that's the whole point of Background Sync's self-drain path —
   see `static/js/sw.js`'s header docstring), so it never emits
   `pwa.mutation.discarded`: `_postTelemetry` posts to
   `self.clients.matchAll(...)`, which is a guaranteed no-op with zero
   open clients. It silently deletes the mismatched row instead.

The page-side drain-guard and the reconcile-on-load clear both emit the
`pwa.mutation.discarded` telemetry event; the SW-side guard does not,
for the reason above. Three `reason` values:

| `reason`                  | Emitted from     | Meaning                                                                 |
|----------------------------|------------------|--------------------------------------------------------------------------|
| `principal_uninitialised`  | Reconcile on load | The `mutations.principal` `meta:app` row didn't exist yet (first load after this ticket shipped, or a fresh DB) — any pre-existing, principal-less row is untrustworthy and cleared on general principle, not because an account change was actually observed. |
| `account_change`           | Reconcile on load | A `mutations.principal` row DID exist and its value differs from the current principal — a genuine account change. |
| `principal_mismatch`       | Drain-guard       | A single row's own stamped `principal` doesn't match the current one at replay time (the race backstop). |

Both reconcile-path reasons carry a `count` of rows cleared; the
drain-guard reason carries the row's `method`/`url`/`idempotency_key`.

`window.pwaMutationQueue.clear()` empties `queue:mutations` and
refreshes the nav badge — used internally by the reconcile above, and
available to any other caller that needs to discard the whole queue.

This client-side partitioning is a best-effort layer that reduces the
attack surface for a shared-device account change; SNOW-463
(`apps/core/idempotency.py`'s method/path/principal/body fingerprint) is the
airtight server-side backstop that holds regardless of client state.

## Idempotency-Key

Server contract: `apps/core/idempotency.py::IdempotencyMiddleware`. Every
enqueued row mints its key once, at enqueue time
(`crypto.randomUUID()`), and sends the IDENTICAL value on every replay
via the `Idempotency-Key` header — this is what lets the server
deduplicate a retried mutation rather than re-executing it. See
[`offline-first.md`](offline-first.md#idempotency-snow-371) for the
server-side cache/TTL contract.

### CSRF on replay

The queue does not add a CSRF token automatically — each caller is
responsible for including whatever the target view requires in its own
`operation.headers` or `operation.body`; the queue only ever adds
`Idempotency-Key` at replay time. `report.js` (SNOW-420) carries CSRF in
the urlencoded `operation.body` (`csrfmiddlewaretoken`, read off the
rendered form by `FormData`) rather than a header — `fetch`'s default
`credentials: 'same-origin'` sends the session cookie alongside it, which
is what `report_submit`'s `CsrfViewMiddleware` check needs.

## Drain triggers

Mirrors `static/js/telemetry.js`'s `_wireLifecycle` pattern: `online`,
`visibilitychange` → visible, a periodic timer (30s, only while the tab is
visible), and immediately after `enqueue()` when `navigator.onLine` is
true. All triggers funnel through one in-flight guard (`_drainInFlight`)
so concurrent triggers can never double-POST the same row.

## Background Sync (Android Chromium)

A single shared tag, `mutation_queue_core.js::SYNC_TAG` (`'mutation-queue'`)
— every mutation uses the same tag rather than one per operation type.
`enqueue()` feature-detects and registers it:

```js
navigator.serviceWorker.ready.then((registration) => {
  if ('sync' in registration) return registration.sync.register(SYNC_TAG);
});
```

Wrapped end-to-end in a swallowed rejection — browsers without
`SyncManager` (notably **iOS Safari**) skip this silently, with no error,
relying entirely on the page-lifecycle drain triggers above instead.

`static/js/sw.js`'s `sync` event listener handles the tag two ways:

- **A tab is open** — `postMessage({type: 'drain-mutations'})` to every
  open client; `sw_register.js`'s message bridge calls the real
  `window.pwaMutationQueue.drain()`, reusing its already-wired
  `window.pwaDb` / `window.pwaTelemetry`.
- **No tab is open** (the actual Background Sync case) — the worker
  self-drains directly against IndexedDB (`indexedDB.open('snowdesk-pwa-v1', 1)`,
  no `window.pwaDb` available in a worker), reusing the same
  classification/backoff helpers via `importScripts('/static/js/mutation_queue_core.js')`.
  If any row still needs a further retry after the pass, the handler
  throws so `event.waitUntil` rejects — a fulfilled `waitUntil` tells the
  browser the sync succeeded and it stops retrying, which would silently
  strand a row still in backoff.

## Files

| File | Role |
|------|------|
| `static/js/mutation_queue_core.js` | Pure backoff/classification/eligibility helpers, shared by the page and the SW (`self.pwaMutationQueueCore`). |
| `static/js/mutation_queue.js` | `window.pwaMutationQueue` — enqueue/drain/markFailed/clear, principal reconcile-on-load + drain-guard, lifecycle triggers, toast + badge wiring, Background Sync registration. |
| `static/js/sw.js` | `sync` event listener — delegate-to-tab or self-drain, plus the best-effort principal discard guard. |
| `static/js/sw_register.js` | `drain-mutations` message bridge (tab-open fast path). |
| `templates/includes/_toast_banner.html` | Full-width top-of-page permanent-failure toast. |
| `templates/includes/nav.html` | `[data-sync-badge]` pill. |
| `apps/core/idempotency.py` | Server-side `Idempotency-Key` dedup contract. |

## Tests

`tests/js/test_mutation_queue.js` (SNOW-496, Vitest — see
[`client-side-tests.md`](client-side-tests.md)) covers the queue's own
logic: offline enqueue → online replay, identical Idempotency-Key across
retries, permanent-4xx immediate failure (toast + telemetry), backoff
scheduling and the 20-attempt ceiling, the nav badge, and feature-detected
Background Sync registration. The SNOW-462 principal-stamping and
reconcile-on-load scenarios (account change / uninitialised baseline)
live in their own files —
`test_mutation_queue_principal.js` / `_reconcile_account_change.js` /
`_reconcile_uninitialised.js` — because `db.js`'s `context()` memoises
`<meta name="pwa-user-id">` once per module instance; see
`test_mutation_queue.js`'s own docstring. `tests/templates/includes/test_toast_banner.py`
covers the toast partial's render contract. `tests/e2e/test_offline_observation_submit.py`
(SNOW-420) and `test_offline_favourite_submit.py` (SNOW-479) cover the
real consumers end to end: an offline tap enqueues with no network
round-trip, the optimistic confirmation + sync-pending line + nav badge
render immediately, a reconnect drains the queue against the real
`report_submit` / `favourite_create` view (the latter with the tap-time
`observed_at` preserved for observations), and a replayed duplicate does
not create a second row (`apps.core.idempotency.IdempotencyMiddleware`).
`test_offline_observation_submit.py` additionally covers report.js's
Reset-Required guard (a report tap must not show the optimistic
confirmation when IndexedDB is in the terminal Reset Required state). The
SNOW-462 account-change headline regression against a real
`report_submit` mutation is folded into
`tests/js/test_mutation_queue_reconcile_account_change.js` (a synthetic
mutation exercises the same reconcile-on-load path; the queue's own logic
is what's under test either way).

## See also

- [`offline-first.md`](offline-first.md) — the umbrella non-negotiables
  index (§12.4 row points here).
- [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md) — the
  `queue:mutations` store definition and the shared `window.pwaDb` API.
- [`telemetry-pipeline.md`](telemetry-pipeline.md) — the
  `pwa.mutation.*` event family.
