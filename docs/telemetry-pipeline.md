---
name: telemetry-pipeline
description: First-party PWA telemetry — /api/telemetry receiver, event allowlist, sendBeacon, PWA_TELEMETRY_ENABLED off switch, pwa.*/map.* event names
status: current
last-reviewed: 2026-10-30
---

# Telemetry pipeline

First-party client observability for the Snowdesk PWA — spec §16
(non-negotiable §12.11). Broken service worker, corrupt IndexedDB,
evicted cache, or dead push subscription must not leave a user unable
to phone home, so the pipeline is deliberately owned end-to-end
(no third-party analytics script). Since SNOW-1182 nothing goes to
PostHog: see [Where events go](#where-events-go-snow-1182).

The umbrella ticket is
[SNOW-368](https://linear.app/hugorodgerbrown/issue/SNOW-368); the
server-side receiver + five §16.2 signal emitters landed with SNOW-381
(this doc). The client-side buffer + `sendBeacon` fast path is a
follow-up blocked on SNOW-375's IndexedDB scaffolding.

## Server components (SNOW-381, this ticket)

| Component | File |
|-----------|------|
| Receiver view | `apps/analytics/views.py::telemetry_receive` |
| Envelope schema + allowlist | `apps/analytics/schema.py` |
| Server-side signal helper | `apps/analytics/signals.py::emit_server_signal` |
| URL routing (namespace `analytics:`) | `apps/analytics/urls.py` mounted at `/api/telemetry` in `config/urls.py` |
| fivebar goals client (shared with `analytics.track`) | `apps/analytics/fivebar.py` |
| Browser-error forwarding to Sentry | `apps/core/sentry.py::capture_js_error` |

## Receiver contract — `POST /api/telemetry`

`apps.analytics.views.telemetry_receive` accepts one of two shapes:

```json
{"events": [envelope, envelope, ...]}   // batched — normal buffer flush
```

```json
envelope                                 // single — sendBeacon fast path
```

The eight-field envelope per spec §16.1:

| Key | Required | Type | Notes |
|-----|----------|------|-------|
| `event` | yes | string | Must appear in `apps.analytics.schema.ALLOWED_EVENTS`. |
| `timestamp` | yes | string | ISO-8601. Preserved verbatim as `client_timestamp` in the event's properties. |
| `client_version` | yes | string | Non-empty. Lifted into `properties`. |
| `session_id` | no | string / null | Used as the `distinct_id` for anonymous requests. |
| `user_id` | no | string / null | **Ignored** (SNOW-550) — see below. Accepted by the schema for backwards compatibility with clients still sending it. |
| `platform` | no | string | e.g. `web`, `ios`, `android`. Lifted into `properties`. |
| `install_state` | no | string | e.g. `installed`, `browser`. Lifted into `properties`. |
| `sw_state` | no | string | e.g. `activated`, `redundant`. Lifted into `properties`. |
| `connection` | no | string | e.g. `wifi`, `cellular`, `offline`. Lifted into `properties`. |
| `properties` | no | object | Free-form; **must not** contain `email` / `ip` / `token` / `credential_id` (existing PII guard rejects at the boundary). |

### `distinct_id` is derived server-side, never taken from the envelope

`/api/telemetry` is deliberately CSRF-exempt and requires no
authentication — a broken client must still be able to phone home. That
made `event["user_id"]` an open door: any POST with an allowed event
name and a guessed identifier attributed events to that user, corrupting
their history and funnels. The 60/minute rate limit bounds request rate,
not identity.

`_resolve_distinct_id` (SNOW-550) therefore resolves in this order,
ignoring `user_id` entirely:

1. the requester's own identity from `request.user`, when authenticated
   (`apps.accounts.identity.request_identity` — the `Account.uuid` below);
2. the envelope's `session_id`, for anonymous requests;
3. the `_anon` sentinel when the session id is stripped too.

This supersedes spec §16.6's "`user_id` wins over `session_id`"
preference — the server can derive the same value trustworthily, so
there is no reason to accept the client's claim.

## User identity — `Account.uuid` (cutover 2026-07-29)

The public-facing identifier for a signed-in user is `Account.uuid`,
resolved through [`apps/accounts/identity.py`](../apps/accounts/identity.py)
(`user_identity` / `request_identity`). It is what every analytics
`distinct_id` carries (fivebar's `id`), and what `base.html` renders into the
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

* **PostHog history did not join across it.** Events emitted before
  2026-07-29 keyed on the integer PK; events after it on the uuid.
  There was no alias migration — pre-launch volume made the
  discontinuity cheap. (Moot since SNOW-1182 removed PostHog.)
* **Queued PWA mutations were flushed once.** Every existing user's
  principal changed on their first load after deploy, so
  `mutation_queue.js`'s principal-mismatch check discarded whatever they
  had queued. That is the fail-safe direction (discard rather than
  misattribute) and matches SNOW-462's intent, but any observation
  queued offline across the deploy boundary was lost.

Response codes:

- `204 No Content` on success, on empty ratelimit-dropped requests, and
  when forwarding fails (analytics failure must never break the
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
CSRF tokens — the endpoint has no side effect beyond logging and
forwarding, so the CSRF risk surface is empty.

Server-side IP address is never captured into an event property, and
never sent to fivebar or Sentry.

## Event catalogue

Two disjoint namespaces: `pwa.*` (PWA shell lifecycle) and a small
`map.*` namespace (SNOW-414, map-surface interactions) — both flow
through the same client-emitted / server-emitted split below.

### Client-emitted (via the receiver)

Declared in `apps.analytics.schema.ALLOWED_EVENTS`. Any event name not in
this frozenset returns `400 invalid_envelope`. Adding an event =
change the frozenset + document the property shape here.

- **SW lifecycle**: `pwa.sw.installed`, `pwa.sw.activated`,
  `pwa.sw.activation_failed`, `pwa.sw.update_available`,
  `pwa.sw.update_applied`, `pwa.sw.fetch_undefined`, and
  `pwa.sw.content_carried` (SNOW-1311) with `properties.cache_version`,
  `.result` (`complete` / `incomplete`), `.copied` and `.failed` — one per
  activation that found old `snowdesk-shell-*` caches and carried the
  user's downloaded content out of them (`_carryShellContent` in
  `static/js/sw.js`). `incomplete` means a put failed, quota ran out or
  `SHELL_CARRY_BUDGET_MS` expired, and every old shell cache was kept for
  the next activation. Not emitted on an activation with no old shell cache
  (a first install, or one after a complete carry).
- **Kill switch + forced update**: `pwa.kill_switch.activated`,
  `pwa.forced_update.triggered`.
- **Install funnel**: `pwa.install.prompted`, `pwa.install.accepted`,
  `pwa.install.dismissed`, `pwa.install.completed`,
  `pwa.install.notifications_granted`.
- **Push (client half)**: `pwa.push.received`, `pwa.push.shown`,
  `pwa.push.opened`, `pwa.push.subscription_lost`.
- **Mutation queue**: `pwa.mutation.enqueued`, `pwa.mutation.drained`,
  `pwa.mutation.failed_permanent`, and `pwa.mutation.discarded` with
  `properties.reason` (SNOW-462) — a queued row dropped without ever being
  replayed because its stamped principal no longer matches the current one:
  `account_change` clears the whole queue, `principal_mismatch` is one row
  caught by the drain guard.
- **Reset flow**: `pwa.reset.user_initiated`, `pwa.reset.forced`.
- **Freshness classifier**: `pwa.freshness.fresh`, `pwa.freshness.stale`,
  `pwa.freshness.unsafe`.
- **Storage pressure**: `pwa.storage.evicted_probable`.
- **Map favourites** (SNOW-414 — deliberately `map.*`, not `pwa.*`, since
  these describe a map-surface interaction rather than PWA shell
  lifecycle): `map.favourite.created` (`static/js/favourites.js`, a
  create-form submit whose response carries a favourite row),
  `map.favourite.deleted` (same file, a delete response with an empty
  body), `map.favourite.shared` (same file, the row's Share control,
  before the share sheet opens), `map.favourite.overlay_toggled` with
  `properties.visible` (emitted by `window.pwaFavouritesOverlay`'s own
  show/hide in `static/js/map.js`, which the layers menu's Favourites row
  drives — SNOW-904).
- **Community reports** (SNOW-419 — same `map.*` namespace):
  `map.community_reports.overlay_toggled` with `properties.visible`
  (SNOW-658: emitted by `window.pwaCommunityReportsOverlay`'s own
  show/hide in `static/js/map.js`. SNOW-904 returned the control to the
  layers menu's "Field observations" row, which drives that same bridge —
  the emit did not move with it, which is the point of the bridge owning
  it), and
  `map.community_reports.marker_tapped` with
  `properties.observation_type` (the `community-reports-point` layer's
  click handler — deliberately carries no location or identity data,
  mirroring the anonymisation contract of `api:community_reports_geojson`).
- **Saved routes** (SNOW-686/687/764, same `map.*` namespace and the same
  created/deleted pair as favourites, for the same reason):
  `map.route.created` (`static/js/routes.js`, a successful upload to
  `routes:create`), `map.route.deleted` (a panel row's Remove form coming
  back 2xx), `map.route.overlay_toggled` with `properties.visible` (emitted
  by `window.pwaRoutesOverlay` in `static/js/map.js` on each edge),
  `map.route.shared` (a share link successfully **minted** — not sent, since
  what happens after the payload reaches the platform's share sheet is not
  observable from the page and would over-count every cancelled share), and
  `map.route.claimed` (a recipient's Save coming back 2xx). None of the five
  carries a file name, size, geometry, uuid or share token: a GPX track is a
  record of where somebody went, and a share token is a capability.
  SNOW-1230: `map.route.created` carries `properties.after_example: true`
  when the device finished or exited the example route walkthrough first.
- **First-run onboarding** (SNOW-1230): `map.welcome.chosen` with
  `properties.choice` (`resort` / `example` / `map` / `close`, the
  "Explore Snowdesk" card), `map.help_menu.chosen` with `properties.choice`
  (`welcome` / `help-mode` / `pages`, the "?" menu), and the example route
  walkthrough's `map.example.opened`, `map.example.step_completed` with
  `properties.step` (`intro` / `drag` / `zoom` / `colours`),
  `map.example.exited` with the `step` it was left on,
  `map.example.finished` (SNOW-1244: with `kept`, whether the visitor
  chose to keep exploring), and (SNOW-1244) `map.example.show_me`, the
  zoom step's button that flies the camera in, and `map.example.left`,
  Back to today after keeping exploring. The between-seasons note on
  today's empty off-season map (SNOW-1244) emits
  `map.between_seasons.shown`, `.explored` (its sample bulletin) and
  `.dismissed`. No share token on any of them. The welcome
  choice and three of the walkthrough events are fivebar goals; the help
  menu and per-step events stay in the pipeline only.
  SNOW-1231 adds the "Find a resort" walkthrough:
  `map.resort_guide.opened` with `properties.offseason`,
  `map.resort_guide.step_completed` with `properties.step` (`search` /
  `resort` / `region`), `map.resort_guide.exited` with the `step` it was
  left on, `map.resort_guide.finished`, and `map.resort_guide.pinned` (the
  finish card's pin pressed). All but the per-step event are fivebar goals.
- **Uncaught client errors** (SNOW-894 — `js.error`, deliberately its own
  namespace): every uncaught exception and unhandled rejection, from
  `static/js/error_reporting.js`. See the emitter table below for the
  opted-in and opted-out payload shapes; it is the one CRITICAL event that
  fires regardless of opt-in.
- **Basemap download bookkeeping** (SNOW-612, same `map.*` namespace):
  `map.basemap.record_write_failed` with `properties.region_id`
  (`static/js/map_region_download.js`'s `_recordRegionDownload`) — a completed download
  whose `basemap.regions` record could not be written. The run leaves a
  pinned Cache Storage bucket behind with nothing naming it, which then
  reads as an orphan; the bucket is reconciled and deletable either way,
  so this only measures how often it happens.

### Server-emitted (via `emit_server_signal`)

Emitted directly from the Python code path — bypasses the receiver so
they cannot be silenced by a broken client. Each is one INFO log line,
`telemetry.signal event=<name> k=v …` (SNOW-1182).

| Signal | Call site | Fires when |
|--------|-----------|------------|
| `pwa.version.endpoint.hit` | `apps/public/api.py::version` | Client fetches `/api/version` |
| `pwa.sw_config.hit` | `apps/public/api.py::sw_config` | Client fetches `/api/sw-config` |
| `pwa.push.sent` | `apps/accounts/push_service.py::dispatch_push` | Push service returns 2xx |
| `pwa.push.gone_410` | Same | Push service returns 410 (subscription lost) |
| `pwa.idempotency.replay` | `apps/core/idempotency.py::IdempotencyMiddleware` | Cache hit — same `Idempotency-Key` seen twice |

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

## fivebar goals (SNOW-1180)

fivebar (https://fiveb.ar) is the site's page analytics: a cookieless tag
in `public/base.html`'s `<head>`, rendered only when `FIVEBAR_DOMAIN` is
set, which also admits `https://fiveb.ar` to the CSP's `script-src` and
`connect-src` (`csp_defaults(analytics_origin=…)`).

`analytics.track()` also hands every event to
`apps/analytics/fivebar.py::send`, which posts the ones in `GOAL_EVENTS` to
fivebar's server events API with `FIVEBAR_TOKEN` (a send-only token). Each
event name becomes a fivebar goal the first time it is sent; nothing is
configured on the fivebar side. The goals are the seven server-side product
events and the client's `map.*` feature events, less
`map.basemap.record_write_failed`. `pwa.*` and the server signals are never
sent: they are operational and high-volume, and every event is a billable
pageview on fivebar.

* `id` is the distinct_id the caller computed; `ip` is never sent.
* `name` (fivebar's page) is the route PATTERN from `analytics.page_of`,
  never the path, because account and share URLs carry tokens. The
  receiver attributes `map.*` events to `/map/`.
* `props` are cut to fivebar's rules (≤8 lowercase names, scalar values,
  ≤255 characters); `client_timestamp` is dropped.
* The POST runs on a two-thread pool, so a request never waits on fivebar;
  failures log at WARNING.
* Error pages pass their status to the tag as `data-status`
  (`templates/includes/_fivebar_tag.html`), because Safari does not report
  it and a 404 would otherwise count as an ordinary page
  (https://fiveb.ar/docs/error-pages). `errors/page.html` gets it as
  `fivebar_status`; the context-free `errors/_standalone.html` from
  `page.status`, with `FIVEBAR_DOMAIN` in its plain context dict.

## Where events go (SNOW-1182)

PostHog was removed in SNOW-1182. Every event the receiver accepts still
runs the PII guard in `analytics.track()`, then goes to exactly one place:

| Event | Destination |
|-------|-------------|
| `GOAL_EVENTS` (product events, `map.*` feature use) | fivebar goal (above) |
| `js.error` | Sentry, via `apps/core/sentry.py::capture_js_error` (SNOW-1181) |
| every other client event (`pwa.*`, `map.basemap.record_write_failed`) | one log line, `telemetry.client event=<name> k=v …` — WARNING for `FAILURE_EVENTS` in `apps/analytics/views.py`, INFO otherwise |
| server signals (`emit_server_signal`) | one INFO log line, `telemetry.signal event=<name> k=v …` |

The log lines are searchable in Render's log stream; a WARNING on a
failure event is what to alert on. Server exceptions and `logger.error`
calls reach Sentry through its Django and logging integrations when
`SENTRY_DSN` is set.

## Rollout / off-switch

### Master switch — `PWA_TELEMETRY_ENABLED` (default `True`)

A single operator-level env var silences the **whole** pipeline, not just
forwarding. Set `PWA_TELEMETRY_ENABLED=False` (via `.env` /
python-decouple; `config/settings/base.py`) and:

- **Client** — `static/js/telemetry.js` becomes an inert no-op. The
  server renders `<meta name="pwa-telemetry-enabled" content="0">`
  (via `apps.public.context_processors.pwa_telemetry`); the script reads it
  once at load and skips all buffering, `sendBeacon`, `/api/telemetry`
  POSTs, and the flush-trigger listeners. `window.pwaTelemetry` still
  exists (its `emit`/`flush` just return early) so every
  `window.pwaTelemetry?.emit(...)` call site stays safe.
- **Receiver** — `/api/telemetry` accepts and drops before parsing,
  still returning `204` so a shell rendered before the flag flipped
  drains its local `queue:events` cleanly instead of retrying.
- **Server signals** — `emit_server_signal` no-ops.

This is the switch to reach for when running locally. It is **distinct**
from the destination gates below (which only stop *forwarding*) and
from the per-user opt-in (spec §16.6): the master switch silences the
operational-safety "critical" events too, because the operator has
turned the pipeline off entirely rather than a user declining
identification.

### Destination gates (forwarding only)

- fivebar goals go out only when both `FIVEBAR_TOKEN` and
  `FIVEBAR_DOMAIN` are set; Sentry only when `SENTRY_DSN` is. Dev and
  test leave all three empty, so nothing leaves the machine.
- To stop forwarding in production without a deploy, unset the variable
  on Render; the receiver keeps accepting and returning 204, and the log
  lines keep flowing.
- Neither gate affects the client's ability to write to `queue:events`
  locally — that buffering is entirely client-side
  (`static/js/telemetry.js`), independent of whether the server
  forwards anything.

## Client-side (SNOW-385)

Shipped in the SNOW-375 + SNOW-385 PR alongside the IndexedDB
scaffolding. `static/js/telemetry.js` — loaded after `db.js` in
`apps/public/templates/public/base.html`. Public API:

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
`pwa.push.subscription_lost`, `pwa.sw.activation_failed` — plus `js.error`,
which SNOW-894 added to the set (see the emitter table below).

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
  install funnel. `pwa.sw.content_carried` is deliberately left at this
  default (SNOW-1311): at most one event per activation, and the
  `incomplete` rate is the figure worth reading in full.

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
| `static/js/pwa_version_check.js::showBlockingModal` | `pwa.forced_update.triggered` — wired at `apps.analytics.schema` §16.2. One caller since SNOW-609 (`/api/version` answering `update_required: true`), so `properties.trigger` is always `'blocked_build'`; the previous `'min_version'` and `'escalation'` values are retired and will only appear on historic rows |
| `static/js/pwa_install.js` | `pwa.install.prompted` (`revealBanner`) / `.accepted` (accepted `userChoice`) / `.dismissed` (`dismissBanner`) / `.completed` (`onInstalled`) / `.notifications_granted` (the iOS `Notification.permission` check inside `onInstalled`) |
| `templates/includes/_freshness_indicator.html` (inline `<script>`) | `pwa.freshness.fresh` / `.stale` / `.unsafe` — sourced from the partial's own `state` render context (server-computed by `apps.core.freshness.freshness_state`); the partial isn't included on a live page yet (design-system component), so the hook fires wherever/whenever a future template adopts it |
| `static/js/sw.js` (via the message bridge) | `pwa.sw.installed` (`install`) / `.activated` (`activate`) / `.activation_failed` (caught `activate` error) / `.fetch_undefined` (`_guardedRespond` — a strategy function resolving to a non-`Response`) / `.content_carried` (`_carryShellContent`, inside `activate`, SNOW-1311) / `pwa.push.received` (`push`) / `.shown` (after `showNotification()` resolves) / `.opened` (`notificationclick`) |
| `static/js/sw_register.js` | `pwa.sw.update_available` (`revealUpdateBannerIfStuck`, once per page, and only when the stuck-worker banner was actually revealed: SNOW-1025 made a routine update silent, so this counts workers that could not update themselves, not deploys) / `.update_applied` (the `controllerchange` after either a silent activation or the banner's Reload) / `pwa.kill_switch.activated` with `properties.mechanism: 'a'` (the pre-register kill fetch — `fetchSwConfig()` returning `kill: true`) |
| `static/js/sw-kill.js` (via the message bridge) | `pwa.kill_switch.activated` with `properties.mechanism: 'b'` (Mechanism B's own activate-time wipe, just ahead of the per-client `navigate()` calls) |
| `static/js/mutation_queue.js` (real queue, SNOW-376) | `pwa.mutation.enqueued` (`enqueue`) / `.drained` with the real count of rows a 2xx removed (`drain`) / `.failed_permanent` with `properties.attempts` reflecting the attempt that just failed — a permanent 4xx or the 20th retry (`_markRowFailed`, and `markFailed` for a caller-driven report outside the queue) |
| `static/js/db.js::_checkStorageEstimate` (cold-start, inside `open()`'s `onsuccess`) | `pwa.storage.evicted_probable` — conservative heuristic (implausibly low `navigator.storage.estimate()` quota, or zero usage alongside a surviving `pwa.install.installed_at` localStorage marker); a `TODO` in the source (no ticket filed) marks it for tightening once real eviction cases surface |
| `static/js/favourites.js` (SNOW-414) | `map.favourite.created` (create-form `htmx:afterSwap` whose response carries `[data-favourite-uuid]`, gated on a module-level "currently creating" flag so a rename doesn't also fire it) / `map.favourite.deleted` (delete's empty-body `htmx:afterSwap`) / `map.favourite.shared` (the row's Share control, emitted before `pwaShare.shareOrCopy` runs) |
| `static/js/map.js` — `window.pwaFavouritesOverlay` (SNOW-414) | `map.favourite.overlay_toggled` with `properties.visible` — emitted by the bridge's own `show()`/`hide()`, whatever drives them (the layers menu's Favourites row since SNOW-904, the panel switch before it) |
| `static/js/map.js` — `window.pwaCommunityReportsOverlay` (SNOW-419, SNOW-658) | `map.community_reports.overlay_toggled` with `properties.visible` — emitted by the bridge's own `show()`/`hide()`, whatever drives them (the layers menu's "Field observations" row since SNOW-904) |
| `static/js/map.js` (main IIFE, SNOW-419) | `map.community_reports.marker_tapped` with `properties.observation_type` — the `community-reports-point` layer's click handler, fired before the popup opens |
| `static/js/routes.js` (SNOW-686, SNOW-764) | `map.route.created` (a successful upload to `routes:create`) / `map.route.deleted` (a panel row's Remove form returning 2xx) / `map.route.shared` (a share link minted, not sent) / `map.route.claimed` (a recipient's Save returning 2xx; `static/js/map.js` emits the same event for the map's own claim path). No file name, size, geometry, uuid or token on any of them |
| `static/js/map_tour_offer.js` (SNOW-1230) | `map.welcome.chosen` with `properties.choice` — every answer to the "Explore Snowdesk" card |
| `static/js/map_help_menu.js` (SNOW-1230) | `map.help_menu.chosen` with `properties.choice` — an item in the "?" menu |
| `static/js/map_example_guide.js` (SNOW-1230) | `map.example.opened` (the example's route opened with `?example=1`) / `.step_completed` with `properties.step` / `.exited` with `properties.step` / `.finished` |
| `static/js/map_resort_guide.js` (SNOW-1231) | `map.resort_guide.opened` with `properties.offseason` / `.step_completed` with `properties.step` / `.exited` with `properties.step` / `.finished` / `.pinned` |
| `static/js/map.js` — `window.pwaRoutesOverlay` (SNOW-687) | `map.route.overlay_toggled` with `properties.visible` — emitted by the bridge's own `show()`/`hide()`, the favourites sibling the routes set was missing at SNOW-686 |
| `static/js/mutation_queue.js` (SNOW-462) | `pwa.mutation.discarded` with `properties.reason` — `account_change` (the whole queue cleared on a principal change at load) or `principal_mismatch` (one row caught by the drain guard). A discarded row is never replayed |
| `static/js/map_region_download.js::_recordRegionDownload` (SNOW-612) | `map.basemap.record_write_failed` with `properties.region_id` — the `basemap.regions` write failed after a completed download, leaving a pinned bucket with no record behind it. Was swallowed silently before this ticket |
| `static/js/error_reporting.js` (SNOW-894) | `js.error` — every uncaught exception (`window` `error`) and unhandled rejection. **CRITICAL**, so it beacons immediately and fires regardless of opt-in; the payload is what differs. Opted in: `{kind, pathname, message, filename, lineno, colno, stack}`. Opted out: `{kind, pathname}` and nothing more. Never a query string on either branch — the map's URLs carry `?route_share=` / `?trip_share=` tokens, and a share token is a capability. Deduped per page on `message\|filename\|lineno` (3 reports per fault, 20 distinct faults), because a throw inside a MapLibre `moveend` handler fires on every frame of a pan |

**SNOW-585 — suppressed in dev.** `pwa.sw.update_available` (and
`.update_applied`) never fire while `settings.SW_DEV_SHELL_BYPASS` is
active — `sw_register.js` checks `DEV_SHELL_BYPASS_ACTIVE` before each
`emit()` call, because the bypass already serves fresh shell assets on the
very next reload, so the event would describe an update the developer
never has to act on. Always off in production; see
[`docs/decisions/dev-bypasses-the-shell-cache.md`](decisions/dev-bypasses-the-shell-cache.md).
No `ALLOWED_EVENTS` change — no new event name is introduced.

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

The consumer wire-ups — other modules calling INTO telemetry.js, rather
than telemetry.js's own logic — are covered a layer down, in Vitest:

| File | Covers |
|------|--------|
| `tests/js/test_pwa_install.js` | the install funnel (`pwa.install.prompted` / `.accepted` / `.dismissed` / `.completed`), the eligibility gate, the 30-day cool-off |
| `tests/js/test_pwa_client_version.js` | `pwa_client_version.js`'s `X-Client-Version` header stamping (and `…_no_meta.js` for the tag-less page) |
| `tests/js/test_pwa_version_check.js` | the blocked-build forced update, including that it emits `pwa.forced_update.triggered` exactly once |
| `tests/js/test_pwa_reset.js`, `test_offline_page_reset.js` | `pwa.reset.user_initiated` from both reset surfaces |
| `tests/public/test_pwa_version_api.py` | Mechanism A's `/api/sw-config` verdict, server-side |

SNOW-389 had added a Playwright class on top of that, driving a real,
undisabled service worker: `pwa.sw.installed` / `.activated` on first
install, `pwa.sw.update_available` on a byte-different `sw.js`, Mechanism
A's pre-register kill gate, and `pwa.push.received` / `.shown`. SNOW-649
retired those modules with the rest of the lifecycle suite, so **those four
event families now have no automated coverage** and are exercised through
`docs/testing-scenarios.md` (P1, P4, P10, and the push scenarios) instead.

**Also not covered, and not by Playwright before either**:

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
  known flake rather than catch one, so the update tests deliberately
  didn't.
- Mechanism B's `pwa.kill_switch.activated` (`mechanism: 'b'`,
  `sw-kill.js`) — a real-SW test for this was written and initially
  looked solid, but a wider anti-flake pass surfaced a genuine
  intermittent failure in the underlying
  install → skipWaiting → activate → wipe → unregister chain (not a
  timing-margin issue — a longer deadline didn't fix it). Dropped per
  the SNOW-389 scope's fallback ladder; stays manual — see
  `docs/testing-scenarios.md` Scenario P11.

Two Playwright/Chromium quirks discovered along the way are worth keeping:
a SW's own script fetch is invisible to `page.route()`, and a second
`wait_for_function()`/`evaluate()` call issued while an earlier SW-driven
promise is still settling can read back empty. The spike's full findings
lived in `tests/e2e/_spike_results.py`, removed with the suite in
SNOW-649.

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
  the PostHog dashboards + alerts this pipeline once fed (gone with
  SNOW-1182).
