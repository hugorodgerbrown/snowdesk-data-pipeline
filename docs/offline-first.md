---
name: offline-first
description: Offline-first PWA compliance index — spec §12 non-negotiables → code; version + freshness + idempotency + reset + install + telemetry
status: current
last-reviewed: 2026-07-19
---

# Offline-first PWA compliance

Top-level index for the offline-first PWA spec (attached to the
originating session — sixteen sections covering lifecycle, service-
worker contract, server headers, kill switch, mutation queue, push,
observability, and non-negotiables). This document tracks where each
piece of the compliance surface lives so a future agent doesn't have
to reverse-engineer it.

The umbrella ticket is
[SNOW-368](https://linear.app/hugorodgerbrown/issue/SNOW-368); the
child tickets that shipped each piece are cross-referenced below.

Read [`offline-map.md`](offline-map.md) first for the SW / manifest /
kill-switch mechanics. This file focuses on the surrounding contract:
version headers, freshness headers, idempotency, client-side
version-check, offline UX, reset escape hatch, and install
orchestration.

## Non-negotiables checklist (spec §12)

Every row must have a code home. Any gap is a compliance regression.

| §    | Requirement                                        | Ticket        | Code                                                                                              |
|------|----------------------------------------------------|---------------|---------------------------------------------------------------------------------------------------|
| 12.2 | `X-App-Version` on every response                  | SNOW-369      | `core.middleware.AppVersionHeaderMiddleware` in `config/settings/base.py::MIDDLEWARE`             |
| 12.2 | `X-App-Min-Version` on every response              | SNOW-369      | Same middleware; reads `settings.APP_MIN_VERSION`                                                 |
| 12.2 | `/api/version` endpoint                            | SNOW-369      | `public.api.version_view` at `/api/version/`                                                      |
| 12.3 | `Idempotency-Key` deduplication                    | SNOW-371      | `core.idempotency.IdempotencyMiddleware`; `core.IdempotencyRecord` model                          |
| 12.6 | `X-Data-Generated-At` freshness header             | SNOW-370      | `core.freshness.apply_freshness_headers`; applied by data-bearing views in `public/api.py`        |
| 12.6 | `X-Data-Max-Age` freshness header                  | SNOW-370      | Same helper                                                                                       |
| 12.6 | `X-Data-Unsafe-After` on safety-critical resources | SNOW-370      | Same helper (default 48h on rating endpoints)                                                     |
| 12.7 | "Reset local data" escape hatch                    | SNOW-378      | `static/js/pwa_reset.js`; `[data-pwa-reset-trigger]` on the manage page                           |
| 12.9 | Two-mechanism kill switch — Mechanism A            | SNOW-372      | `/api/sw-config` returns `{sw_url, kill}` from `SW_URL` / `SW_KILL` settings                      |
| 12.9 | Two-mechanism kill switch — Mechanism B            | SNOW-373      | `static/js/sw-kill.js` served at `/sw-kill.js`; wipes storage on activate then unregisters        |
| 12.10| Client obeys server version verdict                | SNOW-374      | `static/js/pwa_version_check.js` wraps `fetch` + hooks `htmx:afterOnLoad`; `_pwa_update_modal.html` |
| 12.11| First-party client telemetry (server + buffer + emit wiring) | SNOW-381 / SNOW-385 / SNOW-384 | Server: `analytics/views.py::telemetry_receive`, `analytics/signals.py`. Client: `static/js/telemetry.js` on the SNOW-375 `queue:events` store. Emit call sites: see [`telemetry-pipeline.md`](telemetry-pipeline.md#consumer-wire-up). |

## Version + freshness contract

Two response-header contracts drive every client-side check.

### Version headers (SNOW-369 / SNOW-374)

The server stamps `X-App-Version` (the build currently running) and
`X-App-Min-Version` (the minimum build the server will accept) on
**every** response — not just `/api/version/` — via
`AppVersionHeaderMiddleware`. This is deliberate: the client is
inspecting responses continuously so a min-version bump is detected on
the next in-flight request rather than needing a poll.

The client's own build is baked into `<meta name="pwa-app-version">`
and `<meta name="pwa-app-min-version">` at page-render time (see
`public.context_processors.pwa_version`). `pwa_version_check.js`
compares the two on every fetch / HTMX response.

A header drift is treated as a **hint, not a verdict**: cacheable API
responses (`/api/ratings/`, the geo feeds) can be replayed by the
browser HTTP cache or the SW's stale-while-revalidate cache with
pre-deploy headers, which is indistinguishable from a real drift at the
header level (the staging stuck-banner bug — Reload clears Cache
Storage but not the HTTP cache, so the stale header returned
immediately). An observed drift therefore triggers one authoritative
`fetch('/api/version', {cache: 'no-store'})`, and the verdict comes
from the response **body**:

- body `min_supported` non-empty AND differs from the shell's build →
  open `#pwa-update-modal` (non-dismissable), wipe SW + Cache Storage,
  wait for the user to click "Reload now".
- body `current` differs from the shell's build → reveal the soft
  `#sw-update-banner` and stamp
  `localStorage['pwa.update.first_shown_at']`.
- body matches the shell's build → the observed header value is
  memoised as a stale-cache artefact and reveals nothing.
- On cold launch, if the soft banner has been showing >24h, escalate
  straight to the blocking modal — after the same `/api/version`
  confirmation; a stamp the server disowns is cleared instead.

Empty header content is legal — the client treats `""` as "no floor
declared" rather than "missing / older server". This distinction is
what makes the middleware always stamp both headers rather than
conditionally omitting them.

### Freshness headers (SNOW-370 / SNOW-377)

Data-bearing responses carry three headers via
`core.freshness.apply_freshness_headers`:

- `X-Data-Generated-At` — when the source data was produced (tz-aware ISO 8601).
- `X-Data-Max-Age` — seconds after which the data is "stale but usable".
- `X-Data-Unsafe-After` — seconds after which the data must not drive
  operational decisions. Omitted for non-safety data (pass
  `unsafe_after=None`).

The `core.freshness.freshness_state()` helper and the
`pwa_freshness.freshness_state` template tag classify the state
server-side so a page's first paint carries the correct verdict:
`fresh` (green), `stale` (yellow), or `unsafe` (red).
`templates/includes/_freshness_indicator.html` renders the dot +
timestamp. `pwa_offline.js` absorbs the same headers on every JS-
issued response so the persistent `#pwa-offline-banner` shows an
accurate "last updated" suffix even when the connection has since
dropped.

Defaults for safety-critical data: 24h `max_age`, 48h `unsafe_after`.

### §12.6 relaxation — cached-with-explicit-staleness (SNOW-418)

The spec's default posture for safety-critical data is network-only: no
cache, fail closed offline. `favourites/views.py` (`favourite_card`,
`favourite_list`) documents the one accepted relaxation of that rule —
cache the response with its freshness envelope, and let the client
classify staleness itself rather than never caching at all:

- Every favourite roster/card record is written through into the
  `data:favourites` IndexedDB store (schema v2 — see
  [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md)) by
  `static/js/favourites_offline.js`, alongside its own `generated_at` +
  `unsafe_after_seconds` (mirroring the response's freshness headers).
- Offline, a failed `favourites:list` / `favourites:card` request
  repaints from that cache with an explicit "as of HH:MM" stamp — never
  a silent, current-looking render.
- A cached rating past its `unsafe_after_seconds` horizon (48h) renders
  as explicitly **EXPIRED** ("Rating expired — reconnect to see today's
  danger level"), never a stale-looking `danger-tile` chip. Weather
  (non-safety, `unsafe_after=None`) never expires this way.

This is the first `data:*` consumer under the SNOW-375 reserved
namespace — see [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md)
for the store's shape.

## Idempotency (SNOW-371)

`core.idempotency.IdempotencyMiddleware` (mounted immediately after
`CsrfViewMiddleware`) inspects state-changing requests
(`POST`/`PATCH`/`PUT`/`DELETE`). Requests carrying an `Idempotency-Key`
header are deduplicated by that key for 24h so the PWA mutation queue
can retry after connectivity blips without duplicating side effects.

- Cache hits short-circuit before URL routing — the cached response
  body + status + content-type is returned verbatim.
- 5xx responses are treated as transient and left uncached; a retry
  re-executes the view.
- Missing / malformed headers fall through to the view and log
  `pwa.idempotency.missing` / `pwa.idempotency.invalid` keyed on the
  resolved view name (never the raw path, which can embed tokens or
  emails).
- Concurrent-race INSERT collisions are isolated in a savepoint so
  the raced `IntegrityError` doesn't poison the outer transaction.

The cache table (`core.IdempotencyRecord`) is a plain Django model
with an admin surface for operator inspection / manual purge.

## Offline UX (SNOW-377)

`static/js/pwa_offline.js` runs alongside `pwa_version_check.js` on
every public page. Its responsibilities:

- Watch `navigator.onLine` — reveal the persistent
  `#pwa-offline-banner` on `offline` / hide on `online`.
- Reveal the banner on any fetch network failure or HTMX `sendError`
  event, so slow / patchy connections show the banner even when the
  browser still reports `onLine === true`.
- Absorb `X-Data-Generated-At` off every response into a page-scoped
  freshness ledger; the banner's timestamp suffix reads from that
  ledger.
- Toggle the `disabled` state of any element carrying
  `data-network-required` (and cascade into child submit buttons of
  form containers) so a user can't fire a mutation offline.

## Reset local data (SNOW-378)

`static/js/pwa_reset.js` runs the spec §3.10 six-step wipe:
unregister every SW → delete every Cache Storage entry → delete every
IndexedDB database (`indexedDB.databases()` with an explicit
`KNOWN_DB_NAMES` fallback list for browsers without it) → clear
`localStorage` / `sessionStorage` → reload.

The manage page ships a visible "Reset local data on this device"
button (`[data-pwa-reset-trigger]`) with helper copy. Programmatic
callers use `window.pwaResetLocalData()`;
`data-pwa-reset-skip-confirm` skips the `window.confirm` dialogue for
callers that carry their own (e.g. the Update Required modal).

## Install prompt orchestration (SNOW-379)

`static/js/pwa_install.js` layers a deliberate install flow on top of
the browser's native affordance:

- **Chromium**: captures `beforeinstallprompt`, defers, reveals
  `#pwa-install-banner` once the meaningful-action threshold is met.
  Accept calls the deferred `prompt()`.
- **iOS Safari** (feature-detected via `'standalone' in navigator` +
  UA sniff): reveals `#pwa-install-ios`, a Share → Add to Home Screen
  visual guide (inline SVG).
- **Post-install**: detects standalone launch and calls
  `navigator.storage.persist()` so IndexedDB / Cache Storage survive
  storage-pressure eviction.

Threshold satisfies at the earlier of "2 distinct region_ids seen in
the URL path" or ">30s cumulative foreground time". Dismiss cool-off
is 30 days. State currently lives in `localStorage` under
`pwa.install.*` keys; when SNOW-375 lands, the same keys will migrate
into the IndexedDB `meta:app` store.

## Deferred / follow-up

Not yet shipped (tracked as separate SNOW-368 children):

- **SNOW-376** — Client mutation queue with exponential backoff and
  Background Sync. Now unblocked (SNOW-375 shipped); the
  `window.pwaMutationQueue` surface it will sit behind is stubbed
  (SNOW-384 — see below).
- The actual PostHog dashboard/alert *configuration* (charts, saved
  insights, alert thresholds) building on top of the SNOW-384 signal —
  deliberately out of that ticket's pivoted scope ("plumbing, not
  PostHog UI work").

Shipped from the observability + IndexedDB track:

- **SNOW-375** — IndexedDB scaffolding (`static/js/db.js`, one DB per
  app, schema-versioned migrations, Reset Required overlay). See
  [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md).
- **SNOW-381 (server-side)** — `/api/telemetry` receiver,
  `analytics/schema.py` envelope validation, and the five §16.2
  server-side signals (`pwa.version.endpoint.hit`, `pwa.sw_config.hit`,
  `pwa.push.sent`, `pwa.push.gone_410`, `pwa.idempotency.replay`)
  wired into their existing call sites. See
  [`telemetry-pipeline.md`](telemetry-pipeline.md).
- **SNOW-385** — First-party client telemetry buffer
  (`static/js/telemetry.js`) using the SNOW-375 `queue:events` store;
  `sendBeacon` fast path for critical events; opt-in/out toggle with
  EU-default. See [`telemetry-pipeline.md`](telemetry-pipeline.md).
- **SNOW-380** — Declarative Web Push (`PushSubscription.mechanism`),
  410 Gone soft-delete (`inactive_at`) with launch-time client-side
  re-verification backed by the SNOW-375 `meta:app` store, and a VAPID
  subject Django system check. See
  [`push-notifications.md`](push-notifications.md).
- **SNOW-418** — First `data:*` consumer: caches favourites, region
  rating, and (once SNOW-416 lands) point weather in `data:favourites`
  for offline reads, per the §12.6 relaxation above. See
  `favourites/views.py` and `static/js/favourites_offline.js`.
- **SNOW-384** — Wires every remaining `pwa.*` emit call site so the
  eight §16.4 PostHog dashboards are buildable off real signal:
  `client_version` + a self-contained `POSTHOG_API_KEY` gate on every
  server-side signal; the SW→page telemetry message bridge
  (`sw.js` / `sw-kill.js` → `sw_register.js`); the install funnel, kill
  switch, forced-update, freshness, and reset-forced client emits; and a
  `window.pwaMutationQueue` no-op stub ahead of SNOW-376. See
  [`telemetry-pipeline.md`](telemetry-pipeline.md) for the full
  call-site table. PostHog dashboard/alert configuration itself is a
  separate, still-open follow-up.

Non-negotiables the deferred tickets cover:

| §    | Requirement                        | Ticket                  |
|------|------------------------------------|-------------------------|
| 12.4 | Mutation queue with Idempotency-Key | SNOW-376                |

## See also

- [`offline-map.md`](offline-map.md) — PWA shell mechanics (SW,
  manifest, kill switch, cache strategy, install checklist).
- [`decisions/`](decisions/) — architecture-choice notes.
- [`push-notifications.md`](push-notifications.md) — Web Push (VAPID,
  Render wiring, smoke test).
