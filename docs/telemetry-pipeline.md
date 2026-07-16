---
name: telemetry-pipeline
description: First-party PWA telemetry — /api/telemetry receiver, event allowlist, sendBeacon envelope, server-side pwa.* signals, PostHog forwarding
status: current
last-reviewed: 2026-07-16
---

# Telemetry pipeline

First-party client observability for the Snowdesk PWA — spec §16
(non-negotiable §12.11). Broken service worker, corrupt IndexedDB,
evicted cache, or dead push subscription must not leave a user unable
to phone home, so the pipeline is deliberately owned end-to-end
(no third-party `posthog-js`).

The umbrella ticket is
[SNOW-368](https://linear.app/hugorodgerbrown/issue/SNOW-368); the
server-side receiver + five §16.2 signal emitters landed with SNOW-381
(this doc). The client-side buffer + `sendBeacon` fast path is a
follow-up blocked on SNOW-375's IndexedDB scaffolding.

## Server components (SNOW-381, this ticket)

| Component | File |
|-----------|------|
| Receiver view | `analytics/views.py::telemetry_receive` |
| Envelope schema + allowlist | `analytics/schema.py` |
| Server-side signal helper | `analytics/signals.py::emit_server_signal` |
| URL routing (namespace `analytics:`) | `analytics/urls.py` mounted at `/api/telemetry` in `config/urls.py` |
| PostHog client (shared with `analytics.track`) | `analytics/apps.py::AnalyticsConfig.ready` |

## Receiver contract — `POST /api/telemetry`

`analytics.views.telemetry_receive` accepts one of two shapes:

```json
{"events": [envelope, envelope, ...]}   // batched — normal buffer flush
```

```json
envelope                                 // single — sendBeacon fast path
```

The eight-field envelope per spec §16.1:

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `event` | yes | string | Must appear in `analytics.schema.ALLOWED_EVENTS`. |
| `timestamp` | yes | string | ISO-8601. Preserved verbatim as `client_timestamp` on the PostHog event. |
| `client_version` | yes | string | Non-empty. Lifted into `properties`. |
| `session_id` | no | string / null | Preferred as PostHog `distinct_id` when `user_id` is absent. |
| `user_id` | no | string / null | Wins over `session_id`. |
| `platform` | no | string | e.g. `web`, `ios`, `android`. Lifted into `properties`. |
| `install_state` | no | string | e.g. `installed`, `browser`. Lifted into `properties`. |
| `sw_state` | no | string | e.g. `activated`, `redundant`. Lifted into `properties`. |
| `connection` | no | string | e.g. `wifi`, `cellular`, `offline`. Lifted into `properties`. |
| `properties` | no | object | Free-form; **must not** contain `email` / `ip` / `token` / `credential_id` (existing PII guard rejects at the boundary). |

Response codes:

- `204 No Content` on success, on empty ratelimit-dropped requests, and
  on PostHog client exceptions (analytics failure must never break the
  request cycle).
- `400 invalid_json` / `invalid_envelope` / `pii_property` — the caller
  is broken and should stop retrying.
- `413 payload_too_large` — over 32 KiB. The client buffer must chunk.
- `415 unsupported_media_type` — body must be `application/json` (any
  `+json` subtype is also accepted for future flexibility).
- `405` — GET / HEAD / etc. rejected by `@require_POST`.

Rate limit: 60 requests / minute per source IP via `@ratelimit`;
excess is dropped silently (204, no forwarding) so a client back-off
timer does not spiral. CSRF-exempt because `sendBeacon` cannot attach
CSRF tokens — the endpoint has no side effect beyond forwarding to
PostHog, so the CSRF risk surface is empty.

Server-side IP address is never captured into an event property.
PostHog's own IP-derived GeoIP enrichment continues to run (see
`analytics/apps.py`).

## Event catalogue

Two disjoint namespaces, both under the `pwa.` prefix:

### Client-emitted (via the receiver)

Declared in `analytics.schema.ALLOWED_EVENTS`. Any event name not in
this frozenset returns `400 invalid_envelope`. Adding an event =
change the frozenset + document the property shape here.

- **SW lifecycle**: `pwa.sw.installed`, `pwa.sw.activated`,
  `pwa.sw.activation_failed`, `pwa.sw.update_available`,
  `pwa.sw.update_applied`, `pwa.sw.fetch_undefined`.
- **Kill switch + forced update**: `pwa.kill_switch.activated`,
  `pwa.forced_update.triggered`.
- **Install funnel**: `pwa.install.prompted`, `pwa.install.accepted`,
  `pwa.install.dismissed`, `pwa.install.completed`,
  `pwa.install.notifications_granted`.
- **Push (client half)**: `pwa.push.received`, `pwa.push.shown`,
  `pwa.push.opened`, `pwa.push.subscription_lost`.
- **Mutation queue**: `pwa.mutation.enqueued`, `pwa.mutation.drained`,
  `pwa.mutation.failed_permanent`.
- **Reset flow**: `pwa.reset.user_initiated`, `pwa.reset.forced`.
- **Freshness classifier**: `pwa.freshness.fresh`, `pwa.freshness.stale`,
  `pwa.freshness.unsafe`.
- **Storage pressure**: `pwa.storage.evicted_probable`.

### Server-emitted (via `emit_server_signal`)

Emitted directly from the Python code path — bypasses the receiver so
they cannot be silenced by a broken client. All fire under the
synthetic PostHog distinct_id `_server`.

| Signal | Call site | Fires when |
|--------|-----------|------------|
| `pwa.version.endpoint.hit` | `public/api.py::version` | Client fetches `/api/version` |
| `pwa.sw_config.hit` | `public/api.py::sw_config` | Client fetches `/api/sw-config` |
| `pwa.push.sent` | `subscriptions/push_service.py::dispatch_push` | Push service returns 2xx |
| `pwa.push.gone_410` | Same | Push service returns 410 (subscription lost) |
| `pwa.idempotency.replay` | `core/idempotency.py::IdempotencyMiddleware` | Cache hit — same `Idempotency-Key` seen twice |

## Rollout / off-switch

- The endpoint is a no-op when `POSTHOG_API_KEY` is unset — dev / test
  environments accept `POST /api/telemetry` and 204 without contacting
  PostHog. This is the same behaviour as the existing
  `analytics.track()` wrapper.
- To halt event forwarding in production without a deploy, unset
  `POSTHOG_API_KEY` in the Render environment; the receiver continues
  to accept and 204 (client sees no error), but nothing reaches PostHog.
- The five server-side signal emitters follow the same rule — no key,
  no forwarding.

## Client-side (SNOW-385)

Shipped in the SNOW-375 + SNOW-385 PR alongside the IndexedDB
scaffolding. `static/js/telemetry.js` — loaded after `db.js` in
`public/templates/public/base.html`. Public API:

```js
window.pwaTelemetry = {
  emit(event, properties = {}),   // enqueue + critical sendBeacon
  flush(),                        // manual drain (Promise)
  setOptIn(bool),                 // persist to meta:app
  isOptIn(),                      // Promise<bool>
  CRITICAL_EVENTS,                // read-only Set
  SAMPLE_RATES,                   // read-only object
};
```

### Buffer + flush

- Events are written to the SNOW-375 `queue:events` object store.
- Flush triggers (any of): `online` event, `visibilitychange → visible`,
  30-second interval while visible, `pagehide`, or queue depth >= 50.
- Batch of up to 50 rows POSTed as `{"events": [envelope, ...]}` with
  `keepalive: true` so the request survives a foreground-to-background
  transition.
- On 2xx or 4xx, the drained rows are deleted (4xx = server rejected;
  retrying identical bad payloads only wastes the rate limit).
- On 5xx / network failure, rows stay in place for the next trigger.
- Flush is serialised — a call while one is in flight returns the same
  promise so overlapping triggers can't double-POST.

### Critical events — sendBeacon fast path

The exact set from spec §16 (`static/js/telemetry.js::CRITICAL_EVENTS`):
`pwa.kill_switch.activated`, `pwa.forced_update.triggered`,
`pwa.reset.user_initiated`, `pwa.reset.forced`,
`pwa.sw.fetch_undefined`, `pwa.mutation.failed_permanent`,
`pwa.push.subscription_lost`, `pwa.sw.activation_failed`.

Every critical `emit()`:
1. Fires `navigator.sendBeacon('/api/telemetry', envelope)` immediately
   with the **single-envelope** shape (the receiver's fast path).
2. ALSO enqueues to `queue:events` — sendBeacon has no delivery
   guarantee, so the flush loop is the retry mechanism.
3. Bypasses the sample-rate gate.

### Sample rates (spec §16.3)

Encoded in `SAMPLE_RATES`. Any event not listed sample at 100%.
Currently:
- 10% for `pwa.freshness.fresh/.stale`, `pwa.storage.evicted_probable`.
- 25% for `pwa.sw.installed/.activated/.update_available/.update_applied`
  on repeat launches; 100% on the first launch after install (the
  first-launch bump is scored per session via a sessionStorage marker).
- 100% for everything else — including all critical events and the
  install funnel.

### Opt-in / opt-out (spec §16.6)

- Persisted to `meta:app` under `key='telemetry.opt_in'`.
- First-launch default: `false` when `navigator.language` starts with
  an EU-language prefix
  (`de|fr|it|es|nl|pl|pt|sv|da|fi|el|hu|cs|sk|ro|bg|hr|sl|et|lv|lt|mt|ga`),
  `true` otherwise. Persisted on that first read so a language change
  doesn't retroactively flip the default.
- **Standard events**: opt-out drops them entirely (no enqueue, no
  network).
- **Critical events**: still fire (sendBeacon + enqueue) but with
  `user_id: null` and `session_id: null` — the operational-safety
  signals cannot be silenced (spec §16.6), but they don't identify the
  user.
- The settings-page toggle rendering is a small follow-up ticket; this
  slice ships the `setOptIn` / `isOptIn` API + the persistence.

### Consumer wire-up

Existing PWA scripts call `window.pwaTelemetry?.emit(...)` at their
event points. Optional chaining because these scripts are loaded on
admin pages too where telemetry.js is not.

| Call site | Event(s) |
|-----------|----------|
| `static/js/pwa_reset.js::resetLocalData` | `pwa.reset.user_initiated` |
| `static/js/push_demo.js::reverifyPushSubscription` | `pwa.push.subscription_lost` |
| — future SNOW-374 hook — | `pwa.forced_update.triggered` |
| — future SNOW-379 hook — | `pwa.install.prompted/.accepted/.dismissed/.completed` |
| — future SNOW-377 hook — | `pwa.freshness.fresh/.stale/.unsafe` |
| — future SNOW-79 hook — | `pwa.sw.installed/.activated/.update_available/.update_applied` |

`pwa_reset.js` and `push_demo.js::reverifyPushSubscription` (SNOW-380) are
wired in this slice — the other one-liners land alongside their respective
consumer tickets so the emit points match the design of each interaction.
`reverifyPushSubscription` fires on every page load where `meta:app`'s
`push.subscribed_before` flag is true, whether re-verification recovers the
subscription or not — see [`push-notifications.md`](push-notifications.md#mechanism-field-lifecycle)
for the `reason` values it sends.

### Tests

`tests/e2e/test_pwa_telemetry.py` covers:
1. `emit()` writes to `queue:events` with the eight-field envelope shape.
2. `flush()` POSTs the batch and clears rows on 2xx.
3. Critical event fires `sendBeacon` immediately.
4. Opt-out drops standard events; critical events fire with null ids.
5. First `isOptIn()` call persists the computed default to `meta:app`.
6. `setOptIn(true)` writes through.

## See also

- [`offline-first.md`](offline-first.md) — full offline-first PWA
  compliance index and non-negotiables checklist.
- [`push-notifications.md`](push-notifications.md) — Web Push mechanics
  (the source of the two server-side `pwa.push.*` signals).
- [SNOW-384](https://linear.app/hugorodgerbrown/issue/SNOW-384) —
  PostHog dashboards + alerts backed by this pipeline.
