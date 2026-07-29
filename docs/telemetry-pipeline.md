---
name: telemetry-pipeline
description: First-party PWA telemetry — /api/telemetry receiver, event allowlist, sendBeacon, PWA_TELEMETRY_ENABLED off switch, pwa.*/map.* event names
status: current
last-reviewed: 2026-07-19
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

## User identity — `Account.uuid` (cutover 2026-07-29)

The public-facing identifier for a signed-in user is `Account.uuid`,
resolved through [`accounts/identity.py`](../accounts/identity.py)
(`user_identity` / `request_identity`). It is what every PostHog
`distinct_id` carries, and what `base.html` renders into the
`pwa-user-id` meta tag that the PWA mutation queue reads as its
principal.

It replaced `str(request.user.pk)` — Django's sequential `auth.User`
primary key — in SNOW-549. A small incrementing integer leaks the size
and growth rate of the user base, is trivially enumerable, and made
spoofing a client-supplied `user_id` a one-guess exercise. `Account`
inherits `BaseModel`, which already carries a `unique`,
`editable=False` `uuid`, so this needed no new field, migration, or
backfill. A stored identifier was chosen over a derived
`HMAC-SHA256(pk, SECRET_KEY)` because it stays reversible in the admin
(support can map a `distinct_id` back to a user) and survives a
`SECRET_KEY` rotation.

**Two consequences of the cutover, both deliberate:**

* **PostHog history does not join across it.** Events emitted before
  2026-07-29 key on the integer PK; events after it key on the uuid.
  There is no alias migration — pre-launch volume made the
  discontinuity cheap. Read any funnel or retention query spanning that
  date with the break in mind.
* **Queued PWA mutations were flushed once.** Every existing user's
  principal changed on their first load after deploy, so
  `mutation_queue.js`'s principal-mismatch check discarded whatever they
  had queued. That is the fail-safe direction (discard rather than
  misattribute) and matches SNOW-462's intent, but any observation
  queued offline across the deploy boundary was lost.

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

Two disjoint namespaces: `pwa.*` (PWA shell lifecycle) and a small
`map.*` namespace (SNOW-414, map-surface interactions) — both flow
through the same client-emitted / server-emitted split below.

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
- **Map favourites** (SNOW-414 — deliberately `map.*`, not `pwa.*`, since
  these describe a map-surface interaction rather than PWA shell
  lifecycle): `map.favourite.created` (`static/js/favourites.js`, a
  create-form submit whose response carries a favourite row),
  `map.favourite.deleted` (same file, a delete response with an empty
  body), `map.favourite.overlay_toggled` with `properties.visible`
  (`static/js/map.js`'s basemap-menu overlay-toggle handler, fired only
  for `data-overlay-key="favourites"`).
- **Community reports** (SNOW-419 — same `map.*` namespace):
  `map.community_reports.overlay_toggled` with `properties.visible`
  (`static/js/map.js`'s basemap-menu overlay-toggle handler, fired only
  for `data-overlay-key="community_reports"`), and
  `map.community_reports.marker_tapped` with
  `properties.observation_type` (the `community-reports-point` layer's
  click handler — deliberately carries no location or identity data,
  mirroring the anonymisation contract of `api:community_reports_geojson`).

### Server-emitted (via `emit_server_signal`)

Emitted directly from the Python code path — bypasses the receiver so
they cannot be silenced by a broken client. All fire under the
synthetic PostHog distinct_id `_server`.

| Signal | Call site | Fires when |
|--------|-----------|------------|
| `pwa.version.endpoint.hit` | `public/api.py::version` | Client fetches `/api/version` |
| `pwa.sw_config.hit` | `public/api.py::sw_config` | Client fetches `/api/sw-config` |
| `pwa.push.sent` | `accounts/push_service.py::dispatch_push` | Push service returns 2xx |
| `pwa.push.gone_410` | Same | Push service returns 410 (subscription lost) |
| `pwa.idempotency.replay` | `core/idempotency.py::IdempotencyMiddleware` | Cache hit — same `Idempotency-Key` seen twice |

Every server-side signal (SNOW-384) carries a `client_version` property
read from the `X-Client-Version` request header
(`request.headers.get("X-Client-Version", "")`), or threaded in by the
caller when no request is in scope — `push_service.py::dispatch_push`
runs inside the `_worker_dispatch_push` background task, so
`push_views.py::push_test` reads the header and passes it through
`enqueue_push(sub, payload, client_version)`. `static/js/pwa_client_version.js`
(SNOW-388) stamps the header on every same-origin `fetch` and HTMX
request, so the property is populated for all five call sites above.
`navigator.sendBeacon` cannot carry custom headers, but the events it
sends already carry `client_version` inside the telemetry envelope body
instead; SW-initiated navigation fetches remain uninstrumented (out of
scope for SNOW-388). `pwa.sw_config.hit` additionally carries a `reason`
property when `kill=true`, sourced from `settings.SW_KILL` (the only
kill trigger today — a fixed string `"env_kill_switch"`; a future
second trigger should add its own reason string).

## Rollout / off-switch

### Master switch — `PWA_TELEMETRY_ENABLED` (default `True`)

A single operator-level env var silences the **whole** pipeline, not just
PostHog forwarding. Set `PWA_TELEMETRY_ENABLED=False` (via `.env` /
python-decouple; `config/settings/base.py`) and:

- **Client** — `static/js/telemetry.js` becomes an inert no-op. The
  server renders `<meta name="pwa-telemetry-enabled" content="0">`
  (via `public.context_processors.pwa_telemetry`); the script reads it
  once at load and skips all buffering, `sendBeacon`, `/api/telemetry`
  POSTs, and the flush-trigger listeners. `window.pwaTelemetry` still
  exists (its `emit`/`flush` just return early) so every
  `window.pwaTelemetry?.emit(...)` call site stays safe.
- **Receiver** — `/api/telemetry` accepts and drops before parsing,
  still returning `204` so a shell rendered before the flag flipped
  drains its local `queue:events` cleanly instead of retrying.
- **Server signals** — `emit_server_signal` no-ops.

This is the switch to reach for when running locally. It is **distinct**
from the two gates below (which only stop *forwarding* to PostHog) and
from the per-user opt-in (spec §16.6): the master switch silences the
operational-safety "critical" events too, because the operator has
turned the pipeline off entirely rather than a user declining
identification.

### PostHog key gate (forwarding only)

- `emit_server_signal` (`analytics/signals.py`) checks
  `settings.POSTHOG_API_KEY` itself before calling `analytics.track()`
  — logged at DEBUG, no-op when unset. The `/api/telemetry` receiver
  (`analytics/views.py::_posthog_configured`) does the same immediately
  after schema validation: dev / test environments still accept
  `POST /api/telemetry` and 204 without contacting PostHog, but the
  per-event forward loop (and the PII check normally performed inside
  `analytics.track()`) is skipped entirely rather than relying solely on
  `analytics.track()`'s own internal no-op — see SNOW-384.
- To halt event forwarding in production without a deploy, unset
  `POSTHOG_API_KEY` in the Render environment; the receiver continues
  to accept and 204 (client sees no error), but nothing reaches PostHog.
- The five server-side signal emitters follow the same rule — no key,
  no forwarding.
- Neither gate affects the client's ability to write to `queue:events`
  locally — that buffering is entirely client-side
  (`static/js/telemetry.js`), independent of whether the server
  forwards anything.

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

### Master-switch guard

Before any of the below runs, `emit()`, `flush()`, and the lifecycle
wiring short-circuit when the `pwa-telemetry-enabled` meta tag reads
`"0"` (server-set from `settings.PWA_TELEMETRY_ENABLED` — see the
off-switch section). A missing tag is treated as enabled, so pages that
don't render it keep the historical behaviour.

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
admin pages too where telemetry.js is not. Every SW-originated event
(`sw.js`, `sw-kill.js`) is wired via a message bridge rather than a
direct call, since a service worker has no `window` — see the note
below the table.

| Call site | Event(s) |
|-----------|----------|
| `static/js/pwa_reset.js::resetLocalData` | `pwa.reset.user_initiated` (default) / `pwa.reset.forced` (called with `forced=true`) |
| `static/js/db.js::_enterResetRequired` (Reset Required overlay CTA) | Calls `resetLocalData(true)` → `pwa.reset.forced` |
| `static/js/push_demo.js::reverifyPushSubscription` | `pwa.push.subscription_lost` |
| `static/js/pwa_version_check.js::showBlockingModal` | `pwa.forced_update.triggered` — wired at `analytics.schema` §16.2, fired from both the min-version-mismatch and 24h-escalation callers; `properties.trigger` distinguishes them |
| `static/js/pwa_install.js` | `pwa.install.prompted` (`revealBanner`) / `.accepted` (accepted `userChoice`) / `.dismissed` (`dismissBanner`) / `.completed` (`onInstalled`) / `.notifications_granted` (the iOS `Notification.permission` check inside `onInstalled`) |
| `templates/includes/_freshness_indicator.html` (inline `<script>`) | `pwa.freshness.fresh` / `.stale` / `.unsafe` — sourced from the partial's own `state` render context (server-computed by `core.freshness.freshness_state`); the partial isn't included on a live page yet (design-system component), so the hook fires wherever/whenever a future template adopts it |
| `static/js/sw.js` (via the message bridge) | `pwa.sw.installed` (`install`) / `.activated` (`activate`) / `.activation_failed` (caught `activate` error) / `.fetch_undefined` (`_guardedRespond` — a strategy function resolving to a non-`Response`) / `pwa.push.received` (`push`) / `.shown` (after `showNotification()` resolves) / `.opened` (`notificationclick`) |
| `static/js/sw_register.js` | `pwa.sw.update_available` (`showUpdateBanner`, once per distinct waiting worker) / `.update_applied` (the SW-driven `controllerchange` reload path) / `pwa.kill_switch.activated` with `properties.mechanism: 'a'` (the pre-register kill fetch — `fetchSwConfig()` returning `kill: true`) |
| `static/js/sw-kill.js` (via the message bridge) | `pwa.kill_switch.activated` with `properties.mechanism: 'b'` (Mechanism B's own activate-time wipe, just ahead of the per-client `navigate()` calls) |
| `static/js/mutation_queue.js` (real queue, SNOW-376) | `pwa.mutation.enqueued` (`enqueue`) / `.drained` with the real count of rows a 2xx removed (`drain`) / `.failed_permanent` with `properties.attempts` reflecting the attempt that just failed — a permanent 4xx or the 20th retry (`_markRowFailed`, and `markFailed` for a caller-driven report outside the queue) |
| `static/js/db.js::_checkStorageEstimate` (cold-start, inside `open()`'s `onsuccess`) | `pwa.storage.evicted_probable` — conservative heuristic (implausibly low `navigator.storage.estimate()` quota, or zero usage alongside a surviving `pwa.install.installed_at` localStorage marker); `TODO(SNOW-XXX)` in the source marks it for tightening once real eviction cases surface |
| `static/js/favourites.js` (SNOW-414) | `map.favourite.created` (create-form `htmx:afterSwap` whose response carries `[data-favourite-uuid]`, gated on a module-level "currently creating" flag so a rename doesn't also fire it) / `map.favourite.deleted` (delete's empty-body `htmx:afterSwap`) |
| `static/js/map.js::basemapPickerInit` (SNOW-414) | `map.favourite.overlay_toggled` with `properties.visible` — the basemap-menu overlay-toggle click handler, only for `data-overlay-key="favourites"` |
| `static/js/map.js::basemapPickerInit` (SNOW-419) | `map.community_reports.overlay_toggled` with `properties.visible` — the basemap-menu overlay-toggle click handler, only for `data-overlay-key="community_reports"` |
| `static/js/map.js` (main IIFE, SNOW-419) | `map.community_reports.marker_tapped` with `properties.observation_type` — the `community-reports-point` layer's click handler, fired before the popup opens |

**SW → page message bridge** (SNOW-384): `sw.js` and `sw-kill.js` run in
a service-worker context with no `window`, so they cannot call
`window.pwaTelemetry` directly. Both post
`{type: 'pwa-telemetry', event, properties}` to every client they
control (`clients.matchAll(...).then(cs => cs.forEach(c => c.postMessage(...)))`,
or a single client via `event.clientId` for fetch-scoped events).
`sw_register.js` carries one `navigator.serviceWorker.addEventListener
('message', ...)` listener that forwards any such message —
regardless of which SW script sent it — to
`window.pwaTelemetry.emit(event, properties)`. The envelope-level
context fields (`platform`, `install_state`, `sw_state`,
`client_version`, …) are attached by `telemetry.js` from
`window.pwaDb.context()` on the page side, so the SW side only needs to
supply the event name and any event-specific properties.

`pwa_reset.js` and `push_demo.js::reverifyPushSubscription` (SNOW-380) were
wired in the SNOW-385 slice; every remaining row landed with SNOW-384.
`reverifyPushSubscription` fires on every page load where `meta:app`'s
`push.subscribed_before` flag is true, whether re-verification recovers the
subscription or not — see [`push-notifications.md`](push-notifications.md#mechanism-field-lifecycle)
for the `reason` values it sends.

### Tests

`tests/js/test_telemetry.js` (SNOW-496, Vitest) covers:
1. `emit()` writes to `queue:events` with the eight-field envelope shape.
2. `flush()` POSTs the batch and clears rows on 2xx.
3. Critical event fires `sendBeacon` immediately.
4. Opt-out drops standard events; critical events fire with null ids.
5. The freshness-indicator sample-rate gate (`pwa.freshness.{fresh,stale,unsafe}`)
   and `db.js`'s storage-eviction heuristic (`pwa.storage.evicted_probable`).

The opt-in default (computed from `navigator.language` on the first-ever
`isOptIn()` call) and the operator kill switch
(`pwa-telemetry-enabled` meta tag) each live in their own file —
`test_telemetry_optin_default.js` / `test_telemetry_master_switch.js` —
because `telemetry.js` memoises both on first call for the life of a
module instance; see `test_telemetry.js`'s own docstring for the full
per-file rationale, including why this only needed a JS-unit harness at
all: no real page, DOM, or service worker is involved in any of the above.

`tests/e2e/test_pwa_client_signals.py` (SNOW-384) covers the consumer
wire-ups that DO need a real page/DOM (other modules calling INTO
telemetry.js, not telemetry.js's own logic) and don't require a real
installed + activated service worker: the message bridge itself
(simulated via a `MessageEvent` dispatched on `navigator.serviceWorker`),
Mechanism-A kill switch, the install funnel, forced-update escalation, and
`pwa_client_version.js`'s `X-Client-Version` header stamping.

SNOW-389 added a second class of test that DOES drive a real, undisabled
service worker (the `pwa_page` fixture in `tests/e2e/conftest.py`),
closing most of the "not covered" gap this section used to describe:

| File | Covers |
|------|--------|
| `tests/e2e/test_pwa_lifecycle_install.py` | `pwa.sw.installed`, `.activated` (first install) |
| `tests/e2e/test_pwa_lifecycle_update.py` | `pwa.sw.update_available` (a byte-different `sw.js`, via a server-side monkeypatch of `public.views._serve_sw_file` — Playwright cannot intercept a SW's own script fetch); the header-drift and forced-update paths (no dedicated `pwa.sw.*` event, but the banner/modal/reset behaviour itself) |
| `tests/e2e/test_pwa_lifecycle_kill_and_reset.py` | Mechanism A's pre-register kill gate and `pwa.reset.user_initiated` (best-effort) |
| `tests/e2e/test_pwa_push_journey.py` | `pwa.push.received`, `.shown` |

**Still not covered by Playwright**:

- `pwa.sw.activation_failed` / `.fetch_undefined` — both require
  provoking a genuine SW-internal failure (a thrown `activate` handler, a
  strategy function resolving to a non-`Response`); there's no seam to
  trigger either without editing `static/js/sw.js`, which SNOW-389 kept
  out of scope.
- `pwa.push.opened` (`notificationclick`) — headless Chromium's
  `Notification.permission` reports `'denied'` regardless of the granted
  `notifications` permission, and `showNotification()` resolves as a
  silent no-op rather than actually displaying anything;
  `registration.getNotifications()` is unconditionally empty afterwards,
  so there is no queryable `Notification` instance to build a synthetic
  `notificationclick` event against.
- `pwa.sw.update_applied` — `sw_register.js`'s own comment documents the
  emit as a best-effort race against the reload tearing the page down
  before the IndexedDB write settles; asserting on it would encode a
  known flake rather than catch one, so `test_pwa_lifecycle_update.py`
  deliberately doesn't.
- Mechanism B's `pwa.kill_switch.activated` (`mechanism: 'b'`,
  `sw-kill.js`) — a real-SW test for this was written and initially
  looked solid, but a wider anti-flake pass surfaced a genuine
  intermittent failure in the underlying
  install → skipWaiting → activate → wipe → unregister chain (not a
  timing-margin issue — a longer deadline didn't fix it). Dropped per
  the SNOW-389 scope's fallback ladder; stays manual — see
  `docs/testing-scenarios.md` Scenario P11.

Full findings, including the two Playwright/Chromium quirks discovered
along the way (a SW's own script fetch is invisible to `page.route()`;
a second `wait_for_function()`/`evaluate()` call issued while an earlier
SW-driven promise is still settling can read back empty), live in
[`tests/e2e/_spike_results.py`](../tests/e2e/_spike_results.py).

The real SW lifecycle triggers for the still-uncovered events should be
spot-checked manually (devtools Application → Service Workers,
`queue:events` inspection) before relying on the dashboards for those
specific events.

## See also

- [`offline-first.md`](offline-first.md) — full offline-first PWA
  compliance index and non-negotiables checklist.
- [`push-notifications.md`](push-notifications.md) — Web Push mechanics
  (the source of the two server-side `pwa.push.*` signals).
- [SNOW-384](https://linear.app/hugorodgerbrown/issue/SNOW-384) —
  PostHog dashboards + alerts backed by this pipeline.
