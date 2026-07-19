---
name: mutation-queue
description: Client mutation queue — window.pwaMutationQueue, queue:mutations shape, Idempotency-Key, backoff, Background Sync, sync badge, failure toast
status: current
last-reviewed: 2026-07-19
---

# Client mutation queue

Client-side outbox for state-changing requests — spec §7 / §12.4. Sits
behind the `window.pwaMutationQueue` surface first stubbed in SNOW-384
(`static/js/mutation_queue.js`); this ticket (SNOW-376) fills in the real
queue with no call-site changes for any adopter.

**Scope**: this ticket ships the machinery only. The existing HTMX
(`hx-post`) subscription/push call sites are NOT converted to JS calls —
the real consumer is future field-observation submission (SNOW-330).

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

`window.pwaMutationQueue.markFailed(operation, reason)` is the same
surface for a caller-driven report (a mutation attempted outside the
queue) — it fires the same telemetry event and reveals the same
toast/badge state without needing a stored row.

## Idempotency-Key

Server contract: `core/idempotency.py::IdempotencyMiddleware`. Every
enqueued row mints its key once, at enqueue time
(`crypto.randomUUID()`), and sends the IDENTICAL value on every replay
via the `Idempotency-Key` header — this is what lets the server
deduplicate a retried mutation rather than re-executing it. See
[`offline-first.md`](offline-first.md#idempotency-snow-371) for the
server-side cache/TTL contract.

### CSRF on replay

The queue does not add a CSRF token automatically. A future JSON-mutation
consumer (SNOW-330) is responsible for including whatever the target view
requires in its own `operation.headers` — the queue only ever adds
`Idempotency-Key` at replay time.

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
| `static/js/mutation_queue.js` | `window.pwaMutationQueue` — enqueue/drain/markFailed, lifecycle triggers, toast + badge wiring, Background Sync registration. |
| `static/js/sw.js` | `sync` event listener — delegate-to-tab or self-drain. |
| `static/js/sw_register.js` | `drain-mutations` message bridge (tab-open fast path). |
| `templates/includes/_toast_banner.html` | Full-width top-of-page permanent-failure toast. |
| `templates/includes/nav.html` | `[data-sync-badge]` pill. |
| `core/idempotency.py` | Server-side `Idempotency-Key` dedup contract. |

## Tests

`tests/e2e/test_mutation_queue.py` — Playwright, simulated-SW pattern
(see [`client-side-tests.md`](client-side-tests.md)). Covers offline
enqueue → online replay, identical Idempotency-Key across retries,
permanent-4xx immediate failure (toast + telemetry), backoff scheduling
and the 20-attempt ceiling, the nav badge, and feature-detected
Background Sync registration. `tests/templates/includes/test_toast_banner.py`
covers the toast partial's render contract.

## See also

- [`offline-first.md`](offline-first.md) — the umbrella non-negotiables
  index (§12.4 row points here).
- [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md) — the
  `queue:mutations` store definition and the shared `window.pwaDb` API.
- [`telemetry-pipeline.md`](telemetry-pipeline.md) — the
  `pwa.mutation.*` event family.
