---
name: offline-first
description: Offline-first PWA compliance — §12 non-negotiables → code; version, freshness, idempotency, X-SW-Principal, reset, install, sync log
status: current
last-reviewed: 2026-08-04
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
| 12.2 | `X-App-Version` on every response                  | SNOW-369      | `apps.core.middleware.AppVersionHeaderMiddleware` in `config/settings/base.py::MIDDLEWARE`             |
| 12.2 | Server-decided forced-update verdict              | SNOW-369 / SNOW-609 | `apps.public.api.version` returns `update_required` from `settings.APP_BLOCKED_VERSIONS` × the request's `X-Client-Version`. **Supersedes the `X-App-Min-Version` response header**, which SNOW-609 removed — see [`decisions/blocked-builds-not-a-version-floor.md`](decisions/blocked-builds-not-a-version-floor.md) |
| 12.2 | `/api/version` endpoint                            | SNOW-369      | `apps.public.api.version_view` at `/api/version/`                                                      |
| 12.3 | `Idempotency-Key` deduplication                    | SNOW-371      | `apps.core.idempotency.IdempotencyMiddleware`; `core.IdempotencyRecord` model                          |
| 12.4 | Mutation queue with exponential backoff + Background Sync | SNOW-376 / SNOW-420 / SNOW-479 | `static/js/mutation_queue.js` (`window.pwaMutationQueue`); backoff/classification shared with `static/js/sw.js` via `static/js/mutation_queue_core.js`. Consumers: offline field-report submission (`static/js/report.js` → `apps.observations.views.report_submit`, SNOW-420) and offline favourite creation (`static/js/favourites.js` → `apps.favourites.views.favourite_create`, SNOW-479 — optimistic pending pin, 409 at the cap). See [`mutation-queue.md`](mutation-queue.md). |
| 12.6 | `X-Data-Generated-At` freshness header             | SNOW-370      | `apps.core.freshness.apply_freshness_headers`; applied by data-bearing views in `apps/public/api.py`        |
| 12.6 | `X-Data-Max-Age` freshness header                  | SNOW-370      | Same helper                                                                                       |
| 12.6 | `X-Data-Unsafe-After` on safety-critical resources | SNOW-370      | Same helper (default 48h on rating endpoints)                                                     |
| 12.7 | "Reset local data" escape hatch                    | SNOW-378      | `static/js/pwa_reset.js`; `[data-pwa-reset-trigger]` on **two** surfaces — the manage page and the pre-cached `static/offline.html`. The offline copy is the one that reaches a stuck-and-offline user: the manage page is cached but partitioned per account (SNOW-607), so its copy is there only once that account has loaded it online in this browser. The offline copy reveals itself once `pwa_reset.js` has loaded, which `PRECACHE_URLS` (`static/js/sw.js`) guarantees — see [Reset local data](#reset-local-data-snow-378) below. |
| 12.9 | Two-mechanism kill switch — Mechanism A            | SNOW-372      | `/api/sw-config` returns `{sw_url, kill}` from `SW_URL` / `SW_KILL` settings                      |
| 12.9 | Two-mechanism kill switch — Mechanism B            | SNOW-373      | `static/js/sw-kill.js` served at `/sw-kill.js`; wipes storage on activate then unregisters        |
| 12.10| Client obeys server version verdict                | SNOW-374 / SNOW-609 | `static/js/pwa_version_check.js` wraps `fetch` + hooks `htmx:afterOnLoad`; `_pwa_update_modal.html`. The client performs no version comparison — it branches on `update_required` and reveals the modal, which waits for the user's click |
| 12.11| First-party client telemetry (server + buffer + emit wiring) | SNOW-381 / SNOW-385 / SNOW-384 | Server: `apps/analytics/views.py::telemetry_receive`, `apps/analytics/signals.py`. Client: `static/js/telemetry.js` on the SNOW-375 `queue:events` store. Emit call sites: see [`telemetry-pipeline.md`](telemetry-pipeline.md#consumer-wire-up). **Offline:** both network paths (`flush()` fetch and the critical-event `sendBeacon`) short-circuit while the app is not using the network — `navigator.onLine === false`, or SNOW-748's user-forced offline mode with the radio still up. Events stay enqueued and drain on the next flush once the app is back on the network, so offline never fires a doomed request and nothing is dropped. |

## Version + freshness contract

Two response-header contracts drive every client-side check.

### Version headers (SNOW-369 / SNOW-374 / SNOW-609)

The server stamps `X-App-Version` (the build currently running) on
**every** response — not just `/api/version` — via
`AppVersionHeaderMiddleware`. This is deliberate: the client is
inspecting responses continuously, so a deploy is noticed on the next
in-flight request rather than needing a poll.

The client's own build is baked into `<meta name="pwa-app-version">` at
page-render time (see `apps.public.context_processors.pwa_version`).
`pwa_version_check.js` compares the two on every fetch / HTMX response.

A header drift is treated as a **hint, not a verdict**: cacheable API
responses (`/api/ratings/`, the geo feeds) can be replayed by the
browser HTTP cache or the SW's stale-while-revalidate cache with
pre-deploy headers, which is indistinguishable from a real drift at the
header level (the staging stuck-banner bug — Reload clears Cache
Storage but not the HTTP cache, so the stale header returned
immediately). An observed drift therefore triggers one authoritative
`fetch('/api/version', {cache: 'no-store'})`, and the verdict comes
from the response **body**:

- body `update_required: true` → open `#pwa-update-modal`
  (non-dismissable) and **stop**. Nothing is cleared and nothing
  reloads until the user clicks "Reload now"; that click clears the
  shell caches (`window.pwaClearShellCachesAndReload`, `sw_register.js`)
  and reloads. Pinned basemaps, IndexedDB and web storage are untouched
  — a code update does not destroy user data.
- body `current` differs from the shell's build → reveal the soft
  `#sw-update-banner`.
- body matches the shell's build → the observed header value is
  memoised as a stale-cache artefact and reveals nothing.
- an unreachable `/api/version` reveals nothing — "cannot confirm" must
  never read as "confirmed".

The client performs **no version comparison** of its own. SNOW-609
removed the `X-App-Min-Version` header, the `min_supported` body field
and the `pwa-app-min-version` meta tag: `APP_VERSION` resolves to a git
SHA, so a floor could not be ordered against it on either side of the
wire. `update_required` is computed server-side by membership in
`settings.APP_BLOCKED_VERSIONS` against the request's `X-Client-Version`
(`static/js/pwa_client_version.js`, SNOW-388), and fails open when the
client sends no version. Removing the header also disarms shells that
were deployed before this change: they read a missing floor as "no floor
enforced". Rationale:
[`decisions/blocked-builds-not-a-version-floor.md`](decisions/blocked-builds-not-a-version-floor.md).

SNOW-609 also removed the §3.9 24h escalation (soft banner showing for
>24h → blocking modal on the next cold launch). It blocked the app on
elapsed time rather than on any server statement that the build was
unacceptable, and fired on a `current` drift alone — a build the server
is happy to serve. Its `localStorage['pwa.update.first_shown_at']` stamp
had no other reader and is no longer written.

### Freshness headers (SNOW-370 / SNOW-377)

Data-bearing responses carry three headers via
`apps.core.freshness.apply_freshness_headers`:

- `X-Data-Generated-At` — when the source data was produced (tz-aware ISO 8601).
- `X-Data-Max-Age` — seconds after which the data is "stale but usable".
- `X-Data-Unsafe-After` — seconds after which the data must not drive
  operational decisions. Omitted for non-safety data (pass
  `unsafe_after=None`).

The `apps.core.freshness.freshness_state()` helper and the
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
cache, fail closed offline. `apps/favourites/views.py` (`favourite_card`,
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

`apps.core.idempotency.IdempotencyMiddleware` (mounted immediately after
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

## Offline UX (SNOW-377 / SNOW-482)

`static/js/pwa_offline.js` runs alongside `pwa_version_check.js` on
every public page. Its responsibilities:

- Watch `navigator.onLine` — reveal the persistent
  `#pwa-offline-banner` on `offline` / hide on `online`.
- Reveal the banner on any fetch network failure or HTMX `sendError`
  event, so slow / patchy connections show the banner even when the
  browser still reports `onLine === true`.
- Track **two distinct clocks** and persist both to IndexedDB
  `meta:app` (SNOW-482), read back on init so a cold offline launch
  shows real values instead of resetting to blank:
  - `sync.last_at` — wall-clock time of the most recent successful
    (2xx) same-origin response that did NOT carry `X-SW-Cache: hit` and
    is not a synthesized service-worker fallback (SNOW-490: a non-empty
    resolved URL — a synthesized `Response` has `url === ''`, which
    must not be resolved against the page's own URL and mistaken for a
    same-origin round-trip). The banner summary
    surfaces this as a relative phrase — "Offline — last synced
    6 minutes ago" — rendered with `Intl.RelativeTimeFormat` and
    re-rendered on a 30s timer while the banner is shown, so it counts
    up rather than freezing.
  - SNOW-482 added a second clock alongside it,
    `freshness.last_generated_at` — the newest `X-Data-Generated-At`
    header seen — persisted "for the ledger" but never surfaced. Nothing
    read it in the three tickets of banner work that followed, so
    SNOW-615 removed it. Rows already written under that key on devices
    in the field are simply never read again.
- Toggle the `disabled` state of any element carrying
  `data-network-required` (and cascade into child submit buttons of
  form containers) so a user can't fire a mutation offline.
- Broadcast a `snowdesk:connectivity-changed` `{ online }` CustomEvent on
  every transition (and once at init), so consumers that need
  **cache-aware** gating — not the blunt `data-network-required` all-or-
  nothing disable — can react in lockstep with the banner without polling
  `navigator.onLine` themselves.
- (offline-integrity) **Layers-menu cache-aware gating.** The map's layers
  popover (`static/js/map_layer_sync_status.js`) already probed per-row
  cache state (green/grey dots, SNOW-505); offline it now *gates* it. While
  `navigator.onLine === false`, a layer or basemap whose resource isn't
  cached can't be loaded, so its row gets the red `unavailable-offline` dot
  AND is disabled (`aria-disabled`, honoured by the picker's click handler
  in `map.js`); every uncached overlay/basemap follows the same rule. (The
  bulletin boundary has no row and no dot: SNOW-521 removed it and SNOW-532
  dropped the matching `dated-geojson` probe, so its offline state is
  deliberately not surfaced — SNOW-526's caching of settled `?d=` dates is
  unaffected. See
  [`offline-map.md`](offline-map.md#offline-gating-of-the-layers-menu).)
  Each basemap carries its own dot; the **active** basemap is
  never disabled (the user can't be stranded on a map they can't leave).
  Disabling a row never hides a layer already on the map — it only locks the
  control ("keep shown, lock the toggle"). The `snowdesk:region-download`
  icon is likewise disabled offline (no downloading of layers offline). See
  [`offline-map.md`](offline-map.md#offline-gating-of-the-layers-menu).
- (SNOW-483, refined SNOW-492) On the map page, when the third-party
  basemap style JSON can't be fetched offline (the SW treats it as
  network-only), `static/js/map.js` swaps in an inline fallback
  background style so MapLibre's `load` event still fires and the
  SW-cached region overlays paint on a plain background instead of a
  blank canvas. Retried automatically on the next `window` `online`
  event. SNOW-492 tightened the trigger: the `error` handler now
  returns early for any tile/source-scoped error (carrying
  `sourceId`/`tile`) before its `!isStyleLoaded()` guard, since that
  guard is transiently true mid-zoom while tiles are in flight — a
  benign, uncached-tile 504 from the SW no longer permanently blanks
  the map. Only a genuine style-document load failure (no
  `sourceId`/`tile`) still engages the fallback.
- (SNOW-492) Favourites / community-reports map overlays, both
  `network`-classified in `sw.js`, get their own client-side
  write-through/read-back cache (`data:map_overlays`,
  `static/js/map_overlay_offline_cache.js`) so they still render
  offline once fetched at least once — see
  [`offline-map.md`](offline-map.md#offline-overlay-caches--cache-this-area-snow-492)
  for the full mechanism, including the "Cache this area for offline"
  on-demand precache control and the per-overlay "unavailable offline"
  toast.

### `X-SW-Cache` header (SNOW-482, SNOW-490)

`static/js/sw.js` stamps every response it serves from Cache Storage —
the stale-while-revalidate cache hit and all three `_networkFirst`
cache-fallback branches — with `X-SW-Cache: hit`, via a
`_stampCacheHit()` helper that rebuilds the (otherwise header-immutable)
cached `Response`. Its synthesized 504 fallbacks for an offline cache
miss (`_staleWhileRevalidate` and `_basemapStaleWhileRevalidate`) carry
`X-SW-Cache: miss` instead. `pwa_offline.js` reads this header, plus the
response status and resolved URL, to decide whether a response advances
`sync.last_at` and appends a `log:sync` row: it requires a successful
(2xx), un-stamped, non-synthesized same-origin response — excluding
both cache replays and synthesized fallbacks.

### `X-SW-Principal` header (SNOW-607)

The shell cache is the one persistent store in the PWA that held page
HTML with no record of who it was rendered for. `_networkFirst`
(`static/js/sw.js:1652`) now partitions it by account, with two guards.

**No-store responses are never written.** `_isNoStore` reads
`Cache-Control` and skips the `cache.put` — Cache Storage is not the HTTP
cache, so nothing else in the stack enforces the directive. Django's
`@never_cache` is the server-side half of the gate, and its scope is
narrow by design: `change_email_view` (`apps/accounts/views.py:699`)
carries it and `AdminSite.admin_view` applies it to every admin view.
`manage_view` does not — see "cache-partitioned, not cache-avoided"
below.

**Everything else is stamped.** Each cached navigation carries an
`X-SW-Principal` header naming the account its HTML was rendered for,
read out of the response's own `<meta name="pwa-user-id">`
(`apps/public/templates/public/base.html:120`). The offline read serves
an entry only when that stamp equals the principal signed in now, read
from the `meta:app` row `mutations.principal` (`_currentPrincipal`,
`static/js/sw.js:1544`). The stamp comes from the response body rather
than that row because the row lags by one page load — the page's own
`_reconcilePrincipal()` (`static/js/mutation_queue.js:721`) writes it
after the navigation carrying the HTML has already been cached. An entry
with no stamp, or one from a page carrying no meta tag, never matches
and is never served.

This is the same partitioning SNOW-493 applied to the `data:map_overlays`
cache and SNOW-462 applied to mutation rows (see
[`mutation-queue.md`](mutation-queue.md#account-change-partitioning-snow-462)).
Mechanics, the `Vary: Cookie` dead end, and the fail-closed argument:
[`offline-map.md`](offline-map.md#principal-partitioned-navigations-snow-607).

**What this costs offline.** A shell entry cached under one principal is
not served to another, so a `/` cached while anonymous falls back to
`/static/offline.html` for a signed-in user until that page is next
loaded online. `/account/change-email/` is never available offline at
all — it is `@never_cache`, it is a mutation form, and nothing needs it
offline.

**The account pages are cache-partitioned, not cache-avoided.** They ARE
written to the shell cache, stamped with the account they were rendered
for, and served offline to that account alone. `@never_cache` was the
first shape of this fix and was backed out: the offline favourites roster
is built on the page that hosts the roster being in the shell cache, so
`no-store` took a shipped feature offline with it. The stamp closes the leak on its
own, which is why it exists: it is what makes an authenticated page safe
to hold in a cache shared with every other account. Do not add
`@never_cache` back;
`tests/accounts/test_favourites_page.py::TestFavouritesPageCaching`,
`tests/accounts/test_views.py::test_response_is_not_no_store` and the
views' own docstrings pin the choice, and
[`offline-map.md`](offline-map.md#the-account-pages-are-partitioned-not-excluded)
carries the full argument.

**Which page holds the roster has moved.** It was `/account/manage/`
(SNOW-667 split that into `/account/` + `/account/settings/`), then the
favourites section of `/account/`, and since SNOW-668 it is
`/account/favourites/` — `apps.accounts.views.favourites_view`. The two
neighbouring account pages whose views live elsewhere, `my_routes` and
`my_observations`, DO send `Cache-Control: private, no-store`
deliberately; copying that header onto `favourites_view` is the silent
way to break this, which is what `TestFavouritesPageCaching` guards.

Partitioned is not the same as dependable, though. The page's entry is
in the cache only after that account has loaded it online in this
browser, and it is refused to every other principal. So the two
offline-facing surfaces the account area carries land differently:

- **§12.7's escape hatch** — the "Reset local data on this device"
  button (`[data-pwa-reset-trigger]`,
  `apps/accounts/templates/accounts/manage.html:304`) also ships on
  `static/offline.html`, which is precached, carries no account identity
  and sits outside the principal check. That copy is the one that
  reaches a user whose PWA is stuck *and* whose network is gone — the
  state §12.7 exists for, and the state in which the manage page's own
  copy may simply not be there. See
  [Reset local data](#reset-local-data-snow-378).
- **The `log:sync` read-out panel** (`static/js/sync_log.js`, `sync_log`
  waffle flag) has no second copy. The store keeps filling offline; the
  page that renders it loads offline for the account it was cached
  under, and not for anyone else.

### Sync log (SNOW-482)

Qualifying responses (same-origin, un-cached, not a static asset —
`/api/*` calls and HTML partials/navigations) append a row to the
`log:sync` IndexedDB store via `window.pwaDb.appendSyncLog()`, trimmed
to the newest 100. The manage page's "Sync log" panel — and a matching
`/help/` section — read it back via `window.pwaDb.getSyncLog()`
(`static/js/sync_log.js`), both gated on the `sync_log` waffle flag
(see [`feature-flags.md`](feature-flags.md)). The store keeps filling
offline; the manage page that reads it back loads offline only for the
account it was cached under (see
[`X-SW-Principal`](#x-sw-principal-header-snow-607)). The SNOW-378 reset
wipes the whole IndexedDB database, so the log clears along with
everything else. Store shape: [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md#logsync-row-shape-snow-482).

## Reset local data (SNOW-378)

`static/js/pwa_reset.js` runs the spec §3.10 six-step wipe:
unregister every SW → delete every Cache Storage entry → delete every
IndexedDB database (`indexedDB.databases()` with an explicit
`KNOWN_DB_NAMES` fallback list for browsers without it) → clear
`localStorage` / `sessionStorage` → reload.

Two surfaces ship a visible "Reset local data on this device" button
(`[data-pwa-reset-trigger]`) with helper copy. Both are the same
mechanism — the attribute is `pwa_reset.js`'s binding contract, and the
confirmation dialogue, the six-step wipe and the telemetry all live in
that one module:

- **The manage page** (`apps/accounts/templates/accounts/manage.html`).
  Cached and stamped per account since SNOW-607, so it does load offline
  — but only for the account it was cached under, and only once that
  account has loaded it online in this browser (see
  [`X-SW-Principal`](#x-sw-principal-header-snow-607)). Dependable
  enough to be the everyday surface; not dependable enough to be the
  only one.
- **The offline fallback page** (`static/offline.html`, `#pwa-reset-panel`).
  Pre-cached on SW install and outside the principal check, so it is the
  surface that can reach a user whose PWA is stuck *and* whose network is
  gone — the state §12.7 exists for, and the state in which the manage
  page's own copy may not be in the cache at all.

Programmatic callers use `window.pwaResetLocalData()` —
`static/js/db.js`'s Reset Required overlay CTA is the only one;
`data-pwa-reset-skip-confirm` skips the `window.confirm` dialogue for a
surface that carries its own.

The Update Required modal is **not** a caller (SNOW-609). It clears only
the shell caches, via `window.pwaClearShellCachesAndReload`
(`sw_register.js`). A blocked build is a code problem, and the mutation
queue, the offline favourites roster and a user's pinned basemaps are
not code.

### Why the reset script is precached

The offline page's panel ships `hidden` and reveals itself only once
`window.pwaResetLocalData` exists. The wipe itself needs no network
(service workers, Cache Storage, IndexedDB, web storage — all local),
but *delivering* `pwa_reset.js` does: it is a subresource, so it has to
survive the same conditions the hatch exists for. `PRECACHE_URLS`
(`static/js/sw.js`) therefore holds `/static/js/pwa_reset.js` alongside
`/static/offline.html`.

Both are unhashed paths. `collectstatic` under
`CompressedManifestStaticFilesStorage` writes the original filename
next to the hashed one, so the stable URL resolves in production. What
a stable URL cannot do is serve a page that references assets through
`{% static %}` — those request the hashed name and would miss a
precached unhashed entry. Neither of these is reached that way: the
worker asks for the fallback by constant, and `offline.html` is a plain
static file that hardcodes the script path in its own markup, so
precache and request agree.

The offline journey is covered by `test_offline_page_reset_control_offline`
in `tests/e2e/test_pwa_lifecycle_kill_and_reset.py`.

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

- The actual PostHog dashboard/alert *configuration* (charts, saved
  insights, alert thresholds) building on top of the SNOW-384 signal —
  deliberately out of that ticket's pivoted scope ("plumbing, not
  PostHog UI work").

Shipped from the observability + IndexedDB track:

- **SNOW-375** — IndexedDB scaffolding (`static/js/db.js`, one DB per
  app, schema-versioned migrations, Reset Required overlay). See
  [`indexeddb-scaffolding.md`](indexeddb-scaffolding.md).
- **SNOW-376** — Client mutation queue with Idempotency-Key, exponential
  backoff, a nav sync badge, a permanent-failure toast, and feature-detected
  Background Sync (Android) behind the `window.pwaMutationQueue` surface
  SNOW-384 stubbed. See [`mutation-queue.md`](mutation-queue.md).
- **SNOW-420** — First real `window.pwaMutationQueue` consumer: offline
  field-report submission. `static/js/report.js` routes the report form's
  POST through the queue instead of `hx-post`, stamping a tap-time
  `observed_at` before enqueuing so an offline report records when the
  user actually observed the problem rather than whenever the queued
  mutation replays. See [`mutation-queue.md`](mutation-queue.md#consumers).
- **SNOW-381 (server-side)** — `/api/telemetry` receiver,
  `apps/analytics/schema.py` envelope validation, and the five §16.2
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
  `apps/favourites/views.py` and `static/js/favourites_offline.js`.
- **SNOW-384** — Wires every remaining `pwa.*` emit call site so the
  eight §16.4 PostHog dashboards are buildable off real signal:
  `client_version` + a self-contained `POSTHOG_API_KEY` gate on every
  server-side signal; the SW→page telemetry message bridge
  (`sw.js` / `sw-kill.js` → `sw_register.js`); the install funnel, kill
  switch, forced-update, freshness, and reset-forced client emits; and the
  `window.pwaMutationQueue` no-op stub SNOW-376 later filled in. See
  [`telemetry-pipeline.md`](telemetry-pipeline.md) for the full
  call-site table. PostHog dashboard/alert configuration itself is a
  separate, still-open follow-up.
- **SNOW-482** — Splits the single in-memory freshness ledger into two
  persisted `meta:app` clocks (`sync.last_at` /
  `freshness.last_generated_at` — the latter removed again by SNOW-615,
  having never been read), stamps cache-served responses with
  `X-SW-Cache: hit` (`static/js/sw.js`) so the two can be told apart,
  and adds a `log:sync` IndexedDB store (schema v3) with a manage-page
  read-out panel and matching `/help/` section behind the `sync_log`
  waffle flag. Clears the SNOW-377 "IndexedDB-backed persistence"
  deferred bullet.
- **SNOW-492** — "Further adventures in offline sync": fixes the
  blank-map bug (a benign, tile-scoped `error` mid-zoom no longer
  permanently swaps in the SNOW-483 fallback), adds the
  `data:map_overlays` write-through/read-back cache (schema v4) so
  favourites and community-reports keep working offline, a per-overlay
  "unavailable offline" toast, and the "Cache this area for offline"
  on-demand precache control (`sw.js`'s `warm-cache` message handler +
  `window.pwaWarmCache`). See
  [`offline-map.md`](offline-map.md#offline-overlay-caches--cache-this-area-snow-492).
  Known gap carried forward rather than shipped now: neither offline
  overlay cache itself expires/evicts — a snapshot can go arbitrarily
  stale if the device never reconnects.

## See also

- [`offline-map.md`](offline-map.md) — PWA shell mechanics (SW,
  manifest, kill switch, cache strategy, install checklist).
- [`decisions/`](decisions/) — architecture-choice notes.
- [`push-notifications.md`](push-notifications.md) — Web Push (VAPID,
  Render wiring, smoke test).
- [`mutation-queue.md`](mutation-queue.md) — client mutation queue (row
  shape, backoff, Background Sync, permanent-failure toast + nav badge).
