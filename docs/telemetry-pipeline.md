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

## Client-side (SNOW-381 follow-up, blocked on SNOW-375)

Not yet shipped. When the IndexedDB scaffolding in SNOW-375 lands, the
follow-up will add:

- `static/js/telemetry.js` (~2 KB, no third-party library): batch
  buffer in `queue:events`, flush on `online` / foreground / every
  30s / queue depth > 50.
- `navigator.sendBeacon('/api/telemetry', …)` immediate-fire path for
  the critical events called out in SNOW-381
  (`pwa.kill_switch.activated`, `pwa.forced_update.triggered`,
  `pwa.reset.*`, `pwa.sw.fetch_undefined`,
  `pwa.mutation.failed_permanent`, `pwa.push.subscription_lost`,
  `pwa.sw.activation_failed`).
- Sample rates per spec §16.3 table.
- Settings toggle "Send anonymous usage data" (opt-in EU, opt-out
  elsewhere). Critical events fire with `user_id` / `session_id`
  stripped when the toggle is off (spec §16.6).

## See also

- [`offline-first.md`](offline-first.md) — full offline-first PWA
  compliance index and non-negotiables checklist.
- [`push-notifications.md`](push-notifications.md) — Web Push mechanics
  (the source of the two server-side `pwa.push.*` signals).
- [SNOW-384](https://linear.app/hugorodgerbrown/issue/SNOW-384) —
  PostHog dashboards + alerts backed by this pipeline.
