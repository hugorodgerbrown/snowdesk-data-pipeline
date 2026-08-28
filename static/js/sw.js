/*
 * static/js/sw.js — PWA shell service worker for Snowdesk.
 *
 * Replaces the SNOW-9 precache controller (~190 lines, opt-in "Save
 * offline" button + chunked manifest fetch + version dance) with a
 * minimal runtime cache that makes the second load of any page
 * instant without the user having to opt in. Removes the source of
 * the "stuck on stale data" reports that motivated SNOW-79.
 *
 * Strategies:
 *
 *   - Same-origin static shell  (CSS, JS, fonts, images, manifest,
 *                                /sw.js itself, the region/resort
 *                                GeoJSON feeds which don't change
 *                                between deploys, and /api/ratings/
 *                                whose URL encodes the date window —
 *                                see STATIC_PATHS below for the full
 *                                list and the per-entry safety argument)
 *     → stale-while-revalidate. Cache-served responses get an added
 *       ``X-SW-Cache: hit`` header (SNOW-482) so the page can tell a
 *       replay apart from a real network round-trip.
 *
 *   - HTML navigations          → network-first with a per-page cache
 *                                fallback so an offline reload still
 *                                surfaces the last-seen version, and a
 *                                pre-cached /static/offline.html if the
 *                                requested URL has never been visited
 *                                (SNOW-118). Every cache-fallback branch
 *                                also gets ``X-SW-Cache: hit`` (SNOW-482).
 *
 *   - Everything else           (most /api/* endpoints, and third-party
 *                                origins not registered as the active
 *                                basemap)
 *     → network-only. Bulletin JSON, calendar partials, and map tiles
 *     must always reflect server-side freshness; cached avalanche
 *     ratings are dangerous. Exception: /api/ratings/ gets
 *     stale-while-revalidate because its URL encodes the (country,
 *     date) window via query parameters — a stale entry can never be
 *     served for the wrong day, since date rollover changes the URL.
 *
 *   - Active basemap origins    (SNOW-484: vector tiles, sprites, glyphs
 *                                from whichever basemap(s) map.js has
 *                                registered via postMessage — see
 *                                _basemapOrigins below)
 *     → stale-while-revalidate against a dedicated BASEMAP_CACHE, so a
 *     previously-browsed map area still renders offline. Only readable
 *     (``cors``) successful responses are cached — never ``opaque``
 *     no-cors responses, which are unreadable by design.
 *
 * Update contract (the important part)
 * -------------------------------------
 * The goal is a contract a non-technical user can rely on: *if there is
 * an update, you see one "Reload" message; if there is no message, you
 * are already on the latest version.* No silent swaps, no stale tab that
 * never catches up.
 *
 * To make that true the worker does NOT call ``skipWaiting()`` on
 * install. A freshly-installed worker sits in the "waiting" state — that
 * waiting worker IS the pending update, and ``sw_register.js`` shows the
 * banner for exactly that condition. The worker only activates when the
 * page tells it to, by posting ``{ type: 'SKIP_WAITING' }`` (the user
 * clicked "Reload"). On ``activate`` it then calls ``clients.claim()`` so
 * it takes control of every open tab immediately; that fires
 * ``controllerchange`` in the page, which does ONE guarded reload onto
 * the new shell. Because activation is user-driven, claiming here cannot
 * reproduce the dev reload-loop the previous design hit — that loop
 * required auto-skipWaiting on install, which we no longer do.
 *
 * Dev shell-cache bypass (SNOW-585)
 * ----------------------------------
 * The contract above still leaves one gap in local development: right
 * after a ``git pull`` the OLD worker is still in control (it never
 * skipped waiting), and it is the old worker's ``fetch`` handler that
 * decides what ``static``-classified requests get served — out of ITS
 * ``CACHE_VERSION`` cache, carrying the previous ``map.js`` and friends.
 * The page looks current; the code running it is not. Rejected fix:
 * ``skipWaiting()`` in dev — the page that triggered the install already
 * ran with the old assets, so that trades one stale reload for a second
 * unnecessary one.
 *
 * ``DEV_SHELL_BYPASS`` (declared just below ``CACHE_VERSION``) is a
 * literal ``false`` on disk, rewritten to ``true`` at request time by
 * ``apps.public.views.serve_sw`` when ``settings.SW_DEV_SHELL_BYPASS`` is
 * on (development default; always off in production, enforced by
 * ``apps.core.checks``). When true, ``_staleWhileRevalidate`` skips the
 * cache read AND the cache write and calls ``fetch(request)`` directly —
 * so even a still-in-control old worker serves current bytes on the very
 * next reload, no second reload required. An opt-in escape hatch (a
 * checkbox on ``/_sw-version/``, `static/js/pwa_dev_shell_toggle.js`)
 * restores ordinary stale-while-revalidate behaviour for anyone who
 * deliberately wants to exercise the production cache path locally — see
 * the ``dev-shell-cache`` ``message`` handler below. Production never sets
 * ``SW_DEV_SHELL_BYPASS``, so none of this changes production behaviour.
 * Full rationale: ``docs/decisions/dev-bypasses-the-shell-cache.md``.
 *
 * Cache version
 * -------------
 * ``CACHE_VERSION`` is **derived, not committed** (SNOW-590). The literal
 * below is a placeholder: ``apps.public.views.serve_sw`` rewrites it on
 * every response with ``snowdesk-shell-<shell content hash>``, so any
 * change to a shell source automatically produces a new cache name. There
 * is nothing to bump by hand — the old ``bin/sw-version`` ritual and its
 * committed hash file are gone, along with the merge conflict they caused
 * on every concurrent branch touching ``static/js/``.
 *
 * A new version string changes the bytes of this script, which is what
 * makes the browser detect the update and surface the banner. On
 * ``activate``, every cache key not matching the current version is
 * deleted so old SW deploys leave nothing behind. The version is also
 * surfaced via a ``message`` handler so devtools can confirm which SW
 * version is in control.
 *
 * Keep the assignment in its exact single-quoted form. The substitution
 * is a regex, and ``apps.core.checks.check_sw_cache_version_substitutable``
 * fails ``manage.py check`` if it stops matching — without that guard an
 * unmatched line would freeze every client on one cache name and stop
 * shell updates reaching anyone (SNOW-457).
 *
 * X-SW-Cache header (SNOW-482, SNOW-490)
 * ----------------------------------------
 * Every same-origin response served from Cache Storage — the
 * stale-while-revalidate cache hit and all three ``_networkFirst``
 * cache-fallback branches — is rebuilt via ``_stampCacheHit()`` with an
 * added ``X-SW-Cache: hit`` header before it reaches the page.
 * (``_basemapStaleWhileRevalidate`` serves its cache hits un-rebuilt:
 * those responses are cross-origin, so ``pwa_offline.js``'s same-origin
 * check already excludes them from the sync clock without a stamp.)
 * ``static/js/pwa_offline.js`` reads this header to distinguish a
 * Cache-Storage replay from a genuine server round-trip: only 2xx,
 * un-stamped same-origin responses advance the persisted "last synced"
 * clock and append a ``log:sync`` row.
 *
 * The synthesized 504 fallbacks in ``_staleWhileRevalidate`` and
 * ``_basemapStaleWhileRevalidate`` (an offline cache miss with no network
 * to fall back to) carry ``X-SW-Cache: miss`` instead. They also have
 * ``url === ''`` (a synthesized ``Response`` has no URL), which
 * ``pwa_offline.js`` treats as un-resolvable rather than resolving it
 * against the page's own URL — belt and braces against a synthesized
 * fallback ever being counted as a successful sync (SNOW-490).
 *
 * X-SW-Principal header (C1, docs/code-reviews/2026-08-03-js-review.md)
 * ---------------------------------------------------------------------
 * The shell cache is the one persistent store in the PWA that held page
 * HTML with no record of who it was rendered for, so an offline
 * navigation after a sign-out replayed the previous user's
 * ``/account/`` hub page. ``_networkFirst`` now refuses to cache a
 * ``Cache-Control: no-store`` response at all, and stamps every other
 * navigation with ``X-SW-Principal`` — the account its HTML was rendered
 * for, read from the response's own ``<meta name="pwa-user-id">``. The
 * offline read serves an entry only to that same principal. Full
 * rationale, and why the stamp comes from the body rather than IndexedDB,
 * sits above ``_networkFirst`` below.
 *
 * Scope
 * -----
 * Registered from /sw.js (root path) so the SW controls the whole
 * site. The Service-Worker-Allowed header on the response from
 * ``public.views.serve_sw`` makes that scope explicit.
 *
 * Background Sync (SNOW-376)
 * ---------------------------
 * The ``sync`` event listener below is keyed on the single shared tag
 * ``static/js/mutation_queue_core.js`` exports as ``SYNC_TAG``
 * (``'mutation-queue'``), registered by ``static/js/mutation_queue.js`` at
 * enqueue-time via ``registration.sync.register(...)`` (feature-detected —
 * a no-op on browsers without ``SyncManager``, e.g. iOS Safari). Two
 * cases:
 *
 *   - A tab is still open → post ``{type: 'drain-mutations'}`` to it;
 *     ``sw_register.js``'s message bridge calls the REAL
 *     ``window.pwaMutationQueue.drain()``, which already has ``window.pwaDb``
 *     / ``window.pwaTelemetry`` wired up, rather than duplicating that
 *     logic here. This path resolves ``waitUntil`` immediately, before the
 *     page's own drain has even run — it deliberately does NOT throw to
 *     get a Background Sync reschedule if that drain leaves retryable
 *     rows; the page's own 30s timer / ``visibilitychange`` / ``online``
 *     triggers carry it forward instead (see ``_handleMutationSync``'s
 *     own docstring for the full rationale).
 *   - No tab is open (the actual Background-Sync case this API exists
 *     for) → self-drain directly against IndexedDB using the shared
 *     ``mutation_queue_core.js`` helpers, since a worker has no
 *     ``window.pwaDb``. If any row still needs a further retry after this
 *     pass, the handler THROWS so ``event.waitUntil`` rejects — that's
 *     what tells the browser to reschedule the sync per its own backoff.
 *
 * Account-change guard (SNOW-462)
 * --------------------------------
 * ``_selfDrainMutations()`` only fires with no tab open, so it cannot read
 * ``<meta name="pwa-user-id">`` — there is no page. Instead it best-effort
 * reads the last-seen principal persisted by the page in ``meta:app``
 * (key ``mutations.principal``, written by
 * ``static/js/mutation_queue.js``'s ``_reconcilePrincipal()``) and
 * discards (deletes, does not replay) any row whose stamped ``principal``
 * doesn't match it. This is a best-effort backstop, not the airtight
 * guarantee — the page-side drain-guard in ``mutation_queue.js``'s
 * ``_processRow()`` is what makes the property hold whenever a tab is
 * open; SNOW-463 is the server-side backstop that makes it hold
 * regardless.
 *
 * i18n: this worker never renders UI, so there are no translatable
 * strings.
 */

'use strict';

// SNOW-376: pure backoff/classification helpers shared with the page
// (static/js/mutation_queue.js). A worker has no <script> tags, hence
// importScripts rather than a second <script src> tag. Wrapped in
// try/catch so a transient 404 / network hiccup on this one script can't
// take down SW startup — the ``sync`` handler below falls back to a
// literal tag string when the global didn't load.
try {
  importScripts('/static/js/mutation_queue_core.js');
} catch (_importErr) {
  // Non-fatal — see fallback in the 'sync' listener below.
}

// SNOW-496: pure fetch-classification/cache-eviction helpers, extracted so
// they can be unit-tested directly (tests/js/test_basemap_cache_core.js)
// rather than only via a real, slow-to-drive service worker. Same guarded
// importScripts idiom as above — a transient 404 can't take down SW
// startup; _classifySync/_classifyCrossOriginGet/_trimCache below fall back
// to an inline literal when the global didn't load.
try {
  importScripts('/static/js/basemap_cache_core.js');
} catch (_importErr) {
  // Non-fatal — see the inline fallbacks in _classifySync,
  // _classifyCrossOriginGet and _trimCache below.
}

// SNOW-615: a ~180-line change-log lived here, one entry per shell change
// naming the CACHE_VERSION it bumped ("v13", "v14", … "v67") and which
// files it touched. Every line of it described a ritual that no longer
// exists: SNOW-590 made CACHE_VERSION derived, so nothing is bumped by
// hand and no change "gets" a version. The block had drifted to naming
// bin/sw-version — a script deleted by that same ticket — as the thing
// that stamps the value.
//
// git log over this file records the same history, accurately and without
// implying a step a contributor has to perform.

// Placeholder — substituted per-response by apps.public.views.serve_sw with
// the derived `snowdesk-shell-<hash>` name (SNOW-590). The value here is
// never what a browser sees; it is deliberately not a plausible version
// string so that a substitution failure is obvious in devtools rather than
// looking like a legitimate cache name.
const CACHE_VERSION = 'snowdesk-shell-UNSUBSTITUTED';

// SNOW-585: literal placeholder substituted by apps.public.views.serve_sw
// (never serve_sw_kill) on its own response, when settings.SW_DEV_SHELL_BYPASS
// is on — the exact string 'const DEV_SHELL_BYPASS = false;' is replaced with
// '... = true;' before the response is returned. The on-disk default stays
// 'false', so a failed substitution (a typo in either copy of the literal)
// fails safe: production semantics, not an accidental bypass. See "Dev
// shell-cache bypass" below and docs/decisions/dev-bypasses-the-shell-cache.md.
const DEV_SHELL_BYPASS = false;

// SNOW-484: a dedicated cache for the active basemap's cross-origin
// responses (vector tiles, sprites, glyphs) — deliberately NOT the shell
// cache (CACHE_VERSION), so a shell-only change (new CSS, new JS) never
// evicts a previously-browsed area's offline basemap coverage. See
// activate's cleanup sweep below for how stale versions of this cache are
// reaped without touching the current one.
const BASEMAP_CACHE = 'snowdesk-basemap-v1';

// A single map browsing session at typical zoom levels issues a few
// hundred vector-tile + sprite + glyph requests across a handful of zoom
// levels (a manual trace panning/zooming one region logged ~150-300
// distinct URLs). 600 gives headroom for a longer session while still
// bounding on-disk growth from origins whose response sizes we don't
// control — this is a count-based cap, not a byte budget (out of scope
// for SNOW-484).
const BASEMAP_CACHE_MAX_ENTRIES = 600;

// SNOW-614: how many passive basemap puts may land between two trims.
//
// The trim was run after EVERY put, and each run is a full `cache.keys()`
// walk over up to 600 entries — so browsing (and, before SNOW-586 split
// downloads into their own untrimmed buckets, a download) paid an
// enumeration per tile. Amortising over a batch makes it one walk per 32
// tiles instead.
//
// The cap is a soft one already — an insertion-order approximation of LRU,
// not a byte budget — so the cost of batching is that the cache can sit at
// most BASEMAP_CACHE_TRIM_INTERVAL entries over its limit between trims.
// 32 of 600 is ~5% overshoot, small against a bound whose own value is a
// judgement call, and the next trim always brings it back to exactly the
// limit rather than to the limit minus a batch.
const BASEMAP_CACHE_TRIM_INTERVAL = 32;

// Puts into BASEMAP_CACHE since the last trim. Worker-global rather than
// per-cache: there is exactly one passively-trimmed basemap cache (the
// pinned buckets are never trimmed — see `_warmCache`).
let _basemapPutsSinceTrim = 0;

// ---------------------------------------------------------------------------
// Read-path budgets and the offline latch (SNOW-742)
// ---------------------------------------------------------------------------
//
// Every offline fallback below this line used to be a ``catch`` branch, which
// assumes a dead network REJECTS. On a radio that is attached but has no route
// — the Underground, a valley with no coverage, a captive portal that black-
// holes rather than refuses — ``fetch`` does not reject. It hangs on TCP
// retries for tens of seconds to minutes. The catch never ran, so the fallback
// never engaged, and the app sat blank on top of data already on disk.
//
// Two mechanisms, and the distinction between them is the whole design:
//
//   The BUDGETS below are the DETECTOR. They convert a hang into a rejection
//   so the existing fallback paths fire on time. They are not the fix on their
//   own: a budget alone turns one multi-minute hang into a 3-5 second hang per
//   request, indefinitely, which for a day in the backcountry means paying the
//   tax on every navigation and every uncached tile.
//
//   The LATCH is the fix. Once the app has established there is no route it
//   stops asking: read paths skip the network entirely, a cache hit serves and
//   a miss 504s immediately, with no waiting at all, until something says
//   otherwise. That steady state is what the ticket is actually about.
//
// Both apply to READ paths only. ``_warmCache`` keeps unbounded fetches and
// ignores the LATCH — a download is a long operation the user explicitly asked
// for, on a connection they believe they have, and its failures already reach
// them (SNOW-568).
//
// SNOW-748 added a THIRD way into the same steady state: the user asking for
// it from the "Offline mode" row in the account menu, with no failure
// involved at all. See
// ``_networkMode`` below — that mode is not probed, because there is nothing
// to discover.
//
// And it is the one offline mode ``_warmCache`` does NOT ignore. The bypass
// above reasons from a connection the user believes they have; under a forced
// mode the user has told the app not to use the connection they know they
// have, so downloading tiles down it is the app contradicting its own offline
// symbol.
// So a forced mode refuses a new run (``_warmCache``'s first guard) and
// cancels one in flight (``_forceOffline``), while an auto-latch does neither.

// How long a navigation's network attempt gets before it is aborted. Generous
// relative to the tile budget because a navigation is one request whose whole
// page depends on it, and because the fallback it unlocks (the cached shell)
// is a strictly worse answer than the live page when the live page is merely
// slow rather than absent.
const NAVIGATION_FETCH_BUDGET_MS = 5000;

// The same, for a same-origin shell asset or SWR-cached feed. Equal to the
// navigation budget rather than derived from it: they are independently
// tunable, and a stylesheet the page is blocked on is as load-bearing as the
// document that references it.
const SHELL_FETCH_BUDGET_MS = 5000;

// The same, for a basemap-origin request (tile, sprite, glyph, style JSON).
// Shorter: a map view issues these by the hundred, MapLibre retries and
// overzooms on a failed tile on its own (see the SNOW-492 note on map.js's
// error handler), and three of these timing out is what trips the latch — so
// this number also sets how fast a genuinely dead radio is recognised.
const BASEMAP_FETCH_BUDGET_MS = 3000;

// Consecutive read-path timeouts before the worker latches offline. Any
// successful read-path response resets the count, so a single slow request can
// never latch on its own — but a map page fires many tile requests at once, so
// a genuinely dead radio trips this in one burst (~9s).
const OFFLINE_LATCH_THRESHOLD = 3;

// How long the ``/livez`` probe gets. Shorter than any read budget: its only
// job is to answer "is there a route at all", and a route that cannot manage a
// no-op 200 in two seconds is not one the read paths want back yet.
const OFFLINE_PROBE_BUDGET_MS = 2000;

// Backoff schedule for the unlatch probe, in ms — 30s, then 60s, then 5 min
// for every attempt after that. The last entry repeats rather than growing, so
// a device left latched overnight still notices signal within five minutes.
// One 2s probe per five minutes is a rounding error against the per-request
// tax the latch removes.
const OFFLINE_PROBE_BACKOFF_MS = [30000, 60000, 300000];

// The same-origin endpoint the unlatch probe hits. ``/livez``
// (apps/core/views.py) does no work at all: it never reads request.user, never
// touches the database, and is exempt from PosthogContextMiddleware — so it
// measures the route and nothing else. A worker's own fetches do not re-enter
// its ``fetch`` handler, so this can never be intercepted, cached, or counted
// as one of the timeouts that trips the latch.
const OFFLINE_PROBE_URL = '/livez';

// Three modes, not two (SNOW-748):
//
//   ``'auto'``           — normal operation: read paths use the network,
//                          bounded.
//   ``'offline'``        — LATCHED by the worker itself, after
//                          ``OFFLINE_LATCH_THRESHOLD`` consecutive read-path
//                          timeouts. Read paths do not touch the network at
//                          all, and a probe on a backoff schedule looks for a
//                          route so the app can come back on its own.
//   ``'offline-forced'`` — the USER asked for offline mode, from the "Offline
//                          mode" row in the account menu
//                          (templates/includes/nav.html). Read paths behave
//                          exactly as they do while latched, but nothing
//                          probes: a mode the user chose is left alone until
//                          the user changes it back.
//
// The distinction is the SNOW-748 fix. SNOW-742 had two values, so a user's
// request was routed into ``_latchOffline()``, which schedules the unlatch
// probe. Pressed while there was a live connection, the probe then succeeded
// — being online is the premise — and put the user back in ``'auto'`` within
// thirty seconds. That was invisible while the control lived in the offline
// banner (which only reveals once the network is already failing, so the probe
// failed too); it cannot survive a toggle reachable from every page.
//
// So the comparisons below are NOT interchangeable. Some mean "any offline
// mode" (``!== 'auto'``) and some mean "auto-latched only" (``=== 'offline'``);
// each site says which it means and why.
let _networkMode = 'auto';

// The ``meta:app`` key ``static/js/pwa_offline.js`` persists the mode under.
// Same string on both sides; see ``_hydrateNetworkMode`` for why the worker
// reads it for itself rather than only being told it.
const NETWORK_MODE_KEY = 'network.mode';

// Memo for that read (SNOW-748), and the flag that makes a live page's
// ``network-mode`` message authoritative over it.
//
// ``_networkMode`` is module scope, so it dies with the worker — and Chrome
// terminates an idle worker after about thirty seconds. A user-forced offline
// mode was therefore silently lost: the restarted worker came back in
// ``'auto'`` and quietly resumed using the network while the header symbol
// still said "offline", with no event fired and nothing to correct it until
// the next page load. ``_hydrateNetworkMode`` is the worker recovering the
// user's standing choice by itself.
let _networkModeHydration = null;
let _networkModePushed = false;

// Consecutive read-path timeouts seen while in ``'auto'``. Reset to 0 by any
// successful read-path response, and by an unlatch.
let _consecutiveTimeouts = 0;

// Index into OFFLINE_PROBE_BACKOFF_MS for the next probe, and the handle of
// the pending probe timer (null when none is scheduled). The timer is the only
// thing that runs while latched, and ``_unlatch`` clears it.
let _probeBackoffIndex = 0;
let _probeTimer = null;

// True while a probe's fetch is in flight, so a burst of ``online`` events
// (browsers fire several as an interface comes up) cannot stack probes.
let _probeInFlight = false;

/**
 * Trim BASEMAP_CACHE, but only once per ``BASEMAP_CACHE_TRIM_INTERVAL``
 * puts (SNOW-614).
 *
 * @param {Cache} cache
 * @returns {Promise<void>}
 */
async function _trimBasemapCacheEvery(cache) {
  _basemapPutsSinceTrim += 1;
  if (_basemapPutsSinceTrim < BASEMAP_CACHE_TRIM_INTERVAL) return;
  _basemapPutsSinceTrim = 0;
  await _trimCache(cache, BASEMAP_CACHE_MAX_ENTRIES).catch(() => {});
}

// SNOW-586: ONE Cache Storage bucket PER DOWNLOADED AREA
// (``BASEMAP_PINNED_CACHE_PREFIX + areaId``), replacing the single shared
// ``BASEMAP_PINNED_CACHE`` every pinned download used to write into. The
// old design's own entry-count FIFO trim (``BASEMAP_PINNED_CACHE_MAX_ENTRIES``
// below, now gone) deleted the oldest-INSERTED cache entries with no
// record of which download they belonged to — so a new download's trim
// could delete tiles from the MIDDLE of an earlier, unrelated area,
// perforating it rather than removing it. A bucket per area makes
// ``caches.delete(name)`` remove exactly one area, atomically, and
// nothing else — see
// docs/decisions/per-area-pinned-basemap-caches.md for the full
// rationale, including why overlapping areas duplicating tiles on disk is
// an accepted consequence, not a defect.
//
// Still prefixed identically to ``BASEMAP_CACHE`` (``snowdesk-basemap-*``)
// so activate's stale-cache sweep and map_layer_sync_status.js's
// cache-presence probe — both of which discover basemap caches by prefix,
// not a hardcoded name — cover it without further change. Only
// ``_warmCache``'s pinned path ever writes to a pinned bucket; the
// passive ``_basemapStaleWhileRevalidate`` path (ordinary browsing) only
// ever reads/writes/trims ``BASEMAP_CACHE``.
const BASEMAP_PINNED_CACHE_PREFIX = 'snowdesk-basemap-pinned-';

// SNOW-586: the SNOW-521/522 single shared pinned cache's exact name.
// Named explicitly (rather than left to fall out of the prefix filter) so
// activate's cleanup sweep can single it out for deletion even though it
// also matches ``BASEMAP_PINNED_CACHE_PREFIX`` — the scoping comment
// settled "no migration": nobody has a completed per-area download yet,
// so the old undifferentiated blob is simply dropped on upgrade rather
// than split up.
const LEGACY_BASEMAP_PINNED_CACHE = 'snowdesk-basemap-pinned-v1';

// SNOW-613: the memoised pinned-bucket name list, and the promise for an
// enumeration currently in flight.
//
// This used to be re-derived from ``caches.keys()`` on every call, and the
// comment explaining why said a memo "would go stale with no invalidation
// path". There is one now, and the cost of not having it was real: every
// offline tile read paid a full ``caches.keys()``, so a single map pan on a
// device with several downloaded areas issued one enumeration per tile.
//
// Two events invalidate it explicitly:
//
//   1. This worker warms a pinned bucket it has not seen before
//      (``_warmCache`` below).
//   2. The page deletes one — ``evictBasemapAreas`` in static/js/map.js,
//      which posts ``pinned-buckets-changed`` after every eviction (see
//      the message handler).
//
// Those two are not a closed set, and relying on them being one would be
// the same class of trap this review is clearing out: anything that calls
// ``caches.open(BASEMAP_PINNED_CACHE_PREFIX + id)`` outside ``_warmCache``
// creates a bucket the memo cannot see, and the symptom is a downloaded
// tile silently failing to serve offline.
//
// So a MISS re-enumerates unconditionally, and that costs nothing worth
// saving: reaching a pinned miss means the passive basemap cache missed
// too, so this request is already on its way to the network. One
// ``caches.keys()`` is noise beside a network round trip. The memo earns
// its keep on the HIT path — a device panning offline over ground it has
// downloaded, which is thousands of reads that now share one enumeration.
//
// Staleness in either direction is a real fault, not just a slow path: a
// name left in the list would be handed to ``caches.open``, which CREATES
// an empty cache under that name — resurrecting a bucket the user just
// deleted, and one that ``pinnedBucketAreaIds()`` (SNOW-612) would then
// report back to them as an orphaned download.
/** @type {string[]|null} */
let _pinnedNames = null;
/** @type {Promise<string[]>|null} */
let _pinnedNamesInFlight = null;

/**
 * Drop the memoised pinned-bucket list (SNOW-613).
 *
 * @returns {void}
 */
function _invalidatePinnedCacheNames() {
  _pinnedNames = null;
  _pinnedNamesInFlight = null;
}

/**
 * SNOW-586: every live per-area pinned bucket currently in Cache Storage
 * — i.e. every ``caches.keys()`` entry under ``BASEMAP_PINNED_CACHE_PREFIX``
 * EXCLUDING the legacy shared name, which activate's sweep deletes rather
 * than treats as a bucket.
 *
 * SNOW-613: memoised, with the invalidation contract documented above.
 * Concurrent callers share one enumeration rather than each starting their
 * own — a burst of tile requests on a cold memo is the exact case this
 * exists for.
 *
 * @returns {Promise<string[]>}
 */
async function _pinnedCacheNames() {
  if (_pinnedNames) return _pinnedNames;
  if (_pinnedNamesInFlight) return _pinnedNamesInFlight;
  _pinnedNamesInFlight = (async () => {
    const names = await caches.keys();
    return names.filter(
      (name) =>
        name.startsWith(BASEMAP_PINNED_CACHE_PREFIX) && name !== LEGACY_BASEMAP_PINNED_CACHE,
    );
  })();
  try {
    const resolved = await _pinnedNamesInFlight;
    // Only memoise a settled result — an invalidation that landed WHILE
    // this enumeration was in flight has already cleared the in-flight
    // handle, and writing the now-stale answer into the memo would undo it.
    if (_pinnedNamesInFlight) _pinnedNames = resolved;
    return resolved;
  } finally {
    _pinnedNamesInFlight = null;
  }
}

/**
 * The pinned-bucket list, re-enumerated after a lookup the memoised one
 * failed to answer (SNOW-613).
 *
 * The memo can only ever be wrong in one direction that costs anything: a
 * bucket that exists but is not in the list, whose tiles then fail to
 * serve offline. That shows up as a miss, so a miss is where it is worth
 * paying to re-check — see the section comment above for why the walk is
 * effectively free on that path.
 *
 * @param {string[]} tried The list the miss was computed against.
 * @returns {Promise<string[]|null>} A fresh list when it differs from
 *   ``tried``; ``null`` when there is nothing new to search.
 */
async function _pinnedCacheNamesAfterMiss(tried) {
  _invalidatePinnedCacheNames();
  const fresh = await _pinnedCacheNames();
  if (fresh.length === tried.length && fresh.every((n, i) => n === tried[i])) {
    return null;
  }
  return fresh;
}

/**
 * Search every live pinned basemap bucket for ``request``, READ-ONLY.
 *
 * SNOW-586: a tile from ANY deliberate download should serve offline
 * regardless of which area's bucket holds it. Stops at the first hit; a
 * tile shared by two overlapping areas is identical bytes in either, so
 * which one answers first makes no difference to what is served.
 *
 * SNOW-613: the buckets are searched in parallel rather than walked in
 * order. A device with several downloaded areas paid one round trip per
 * bucket per tile, serially, on every offline pan. ``Promise.all`` turns N
 * sequential waits into one. A miss then re-enumerates the bucket list and
 * searches again, in case the memoised list was the stale half of the
 * story.
 *
 * SNOW-722: extracted from ``_basemapStaleWhileRevalidate`` unchanged, so
 * the fetch listener's read-only fallback for an UNCLASSIFIED cross-origin
 * GET can reach the same search. Never writes to or trims a pinned bucket
 * (that stays ``_warmCache``'s pinned path's job alone), so ordinary
 * browsing can neither grow nor evict a deliberate download. Never throws:
 * a lookup failure must not break a caller's miss -> network chain.
 *
 * @param {Request} request
 * @returns {Promise<Response|undefined>} The first bucket hit, or
 *   ``undefined`` when no bucket holds it.
 */
async function _searchPinnedBuckets(request) {
  const searchPinned = async (names) => {
    const hits = await Promise.all(
      names.map(async (name) => {
        try {
          const pinnedCache = await caches.open(name);
          return await pinnedCache.match(request);
        } catch (_e) {
          // One bucket failing must not lose the others.
          return undefined;
        }
      }),
    );
    return hits.find(Boolean);
  };

  try {
    const pinnedNames = await _pinnedCacheNames();
    const pinnedHit = await searchPinned(pinnedNames);
    if (pinnedHit) return pinnedHit;

    // Missed. The list may be the stale half of the story — re-enumerate
    // and search again if anything has appeared since.
    const freshNames = await _pinnedCacheNamesAfterMiss(pinnedNames);
    if (freshNames) {
      const freshHit = await searchPinned(freshNames);
      if (freshHit) return freshHit;
    }
  } catch (_err) {
    // Defensive: the caller falls through to the network.
  }
  return undefined;
}

/**
 * SNOW-722: the read-only cache probe for a cross-origin GET that
 * classification did NOT recognise as a basemap request.
 *
 * ``_classifyCrossOriginGet`` answers ``'basemap'`` only for an origin in
 * the in-memory ``_basemapOrigins`` allowlist, and that Set does not
 * survive the browser terminating an idle worker (Android Chrome does this
 * aggressively). Its recovery path — ``_hydrateBasemapOrigins()`` — can
 * itself fail, and the reported symptom was exactly that: a blank basemap
 * inside an area the app had marked as downloaded, with the pinned buckets
 * sitting full underneath. The allowlist decides what may be WRITTEN; it
 * has no business deciding what may be READ back from a cache this device
 * already holds.
 *
 * So: never writes. An unregistered origin may serve from cache but must
 * never populate one. ``BASEMAP_CACHE`` is searched as well as the pinned
 * buckets, because it can hold tiles written in an earlier session when
 * the allowlist WAS populated.
 *
 * Cost, stated exactly, because every unclassified cross-origin GET on the
 * page reaches this function:
 *
 *   - ONE ``caches.open(BASEMAP_CACHE)`` + ``cache.match(request)``,
 *     unconditionally. No enumeration; a keyed lookup in one bucket.
 *   - The pinned-bucket walk ONLY when at least one pinned bucket exists.
 *     ``_pinnedCacheNames()`` is memoised, so establishing that costs one
 *     ``caches.keys()`` per worker lifetime; with nothing pinned the walk —
 *     and the per-miss ``_pinnedCacheNamesAfterMiss`` re-enumeration behind
 *     it — is skipped entirely.
 *
 * The ``BASEMAP_CACHE`` match is deliberately NOT behind the
 * pinned-emptiness check, and moving it there would reintroduce the bug
 * this function exists to kill. ``BASEMAP_CACHE`` is the PASSIVE cache
 * ordinary online browsing fills, so a user who has panned around a
 * basemap but never run an explicit "Download basemap" has tiles in it and
 * ZERO pinned buckets. Gating the match on a pinned bucket existing would
 * hand exactly that user a blank map over a full cache.
 *
 * @param {Request} request
 * @returns {Promise<Response|undefined>} A cached response, or
 *   ``undefined`` when the caller should go to the network.
 */
async function _readOnlyBasemapCacheProbe(request) {
  try {
    // Unconditional, and it must stay that way — see the docstring's note
    // on the browsed-but-never-downloaded case.
    const cache = await caches.open(BASEMAP_CACHE);
    const cached = await cache.match(request);
    if (cached) return cached;
    // Nothing pinned: skip the walk, and the re-enumeration behind it.
    const pinnedNames = await _pinnedCacheNames();
    if (pinnedNames.length === 0) return undefined;
  } catch (_err) {
    return undefined;
  }
  return _searchPinnedBuckets(request);
}

// SNOW-484: the allowlist of cross-origin basemap origins it is safe to
// cache. A service worker has no DOM, so it cannot itself read the
// basemap picker's data-basemap-url attributes — map.js posts them here
// via a 'register-basemap-origins' message (see the message handler
// below). Populated fresh (replaced, not merged) on every registration
// so a stale allowlist from an earlier page load can't linger. Entries
// are exact ``scheme://host[:port]`` strings.
//
// SNOW-487: this in-memory Set does not survive the browser terminating
// an idle worker — a later ``fetch`` event runs in a fresh global with
// an empty Set even though the durable BASEMAP_CACHE still holds
// previously-cached tiles/sprites/glyphs. ``_hydrateBasemapOrigins()``
// below lazily repopulates it from the durable ``meta:app`` mirror
// ``static/js/map.js`` writes alongside the postMessage.
let _basemapOrigins = new Set();

// SNOW-487: memoises the in-flight (or completed) hydration read so a
// burst of cross-origin requests on a freshly (re)started worker
// triggers one IndexedDB read, not one per request. Reset to null by
// the ``register-basemap-origins`` message handler whenever it replaces
// ``_basemapOrigins``, so a later explicit registration is never
// shadowed by a stale memoised promise.
let _basemapHydration = null;

// SNOW-722: how many times a FAILED hydration read may be retried before
// the empty result is memoised for the rest of the worker's lifetime.
//
// The memo above used to hold a failure just as firmly as a success, so one
// transient IndexedDB error (a concurrent version upgrade, lock contention)
// froze an empty allowlist until the next live page re-registered — every
// tile classified ``network`` and died offline. But an unbounded retry is
// not the answer either: the same ``catch`` covers the PERMANENT case of a
// worker-created DB with no ``meta:app`` store (see ``_openMutationsDb()``'s
// docstring), which would then cost a DB open on every cross-origin request
// forever. A small cap is enough for the transient case, and the read-only
// cache probe in the fetch listener is the actual guarantee behind it.
const BASEMAP_HYDRATION_MAX_ATTEMPTS = 3;

// Failed hydration reads so far, this worker lifetime. Reset alongside
// ``_basemapHydration`` by the ``register-basemap-origins`` handler.
let _basemapHydrationFailures = 0;

// SNOW-585: whether the page has opted BACK IN to ordinary
// stale-while-revalidate behaviour while DEV_SHELL_BYPASS is on (the
// checkbox on /_sw-version/, static/js/pwa_dev_shell_toggle.js). Ignored
// entirely when DEV_SHELL_BYPASS is false (production). Defaults to
// opted-out (false) — the whole point of the bypass is "off means off"
// until a developer deliberately asks for the cache back.
let _devShellCacheOptIn = false;

// Memoises the in-flight (or completed) opt-in hydration read, mirroring
// ``_basemapHydration`` exactly — see ``_hydrateDevShellCacheOptIn()``.
// Reset to null by the ``dev-shell-cache`` message handler whenever it
// replaces ``_devShellCacheOptIn``, so a later toggle is never shadowed by
// a stale memoised promise.
let _devShellCacheHydration = null;

// Pre-cached on install so the offline fallback is reliably available
// the moment the network drops, even on the very first navigation that
// loses connectivity. Keep this list short — stale-while-revalidate
// already handles the shell on the second visit.
//
// Both entries are unhashed paths, and that is deliberate rather than an
// oversight. ``collectstatic`` under ``CompressedManifestStaticFilesStorage``
// writes the original filename alongside the hashed one, so the stable URL
// resolves in production. What a stable URL cannot do is serve a page that
// references its assets through ``{% static %}`` — those request the hashed
// name, so a precached unhashed entry would never be matched. Neither entry
// here is reached that way: the worker asks for ``OFFLINE_FALLBACK`` by this
// exact constant, and ``offline.html`` is a plain static file that hardcodes
// ``RESET_SCRIPT`` in its own markup. Precache and request agree, so both
// are hits.
//
// RESET_SCRIPT carries spec §12.7's "Reset local data" escape hatch onto the
// offline page (SNOW-607). The hatch exists for the state where the app is
// broken and the network is gone, so the script that binds it has to survive
// exactly that — without it the control renders bound to nothing, which is
// worse than absent.
const OFFLINE_FALLBACK = '/static/offline.html';
const RESET_SCRIPT = '/static/js/pwa_reset.js';
const PRECACHE_URLS = [OFFLINE_FALLBACK, RESET_SCRIPT];

// File extensions that count as same-origin static shell. Anything
// not in this set, and not a same-origin GeoJSON feed, falls through
// to network-only. The list deliberately excludes ``.json`` —
// generic JSON paths under /api/ may be region summaries / bulletins,
// which must stay fresh.
const STATIC_SHELL_EXTENSIONS = new Set([
  '.css',
  '.js',
  '.svg',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.ico',
  '.woff',
  '.woff2',
  '.webmanifest',
]);

// Same-origin URL paths that are safe to serve stale-while-revalidate.
// Three classes of entry:
//
//   Geo feeds — versioned-by-deploy. Polygon and resort geometry
//   changes only on deploy, so a stale polygon never misleads the user
//   about danger (unlike a stale rating, which would).
//
//   /api/ratings/ — safe for a different reason: its URL encodes the
//   data window via ?d=YYYY-MM-DD and ?country= parameters, so each
//   (country, date) variant cache-keys separately. Date rollover
//   changes the URL, meaning a stale entry can never be returned for
//   the wrong day. The cache rule is the same as for geo feeds; the
//   safety argument is URL-encoded date window rather than
//   deploy-versioned geometry.
//
//   /api/bulletin-groupings.geojson (SNOW-526) — safe only for the subset
//   of ``?d=`` values the server has declared settled (no future ingest
//   run can still change that day's geometry). Unlike the two classes
//   above, the URL alone doesn't establish that — today's date and the
//   settled past share the same path/param shape. IMMUTABLE_ONLY_PATHS
//   below gates this entry so only a response the server marked
//   ``immutable`` is actually written to the cache; see
//   ``_staleWhileRevalidate`` and ``shouldPersist`` in
//   basemap_cache_core.js.
//
// Note on Vary: the ratings view emits ``Vary: Accept-Encoding`` and
// the Cache API honours Vary on match. In practice every request from
// a given browser session carries the same Accept-Encoding header (the
// UA sets it, not page JS), so cached entries hit reliably. A UA that
// somehow rotated Accept-Encoding mid-session would just cache-miss
// and re-fetch — not a correctness risk.
//
// Any new entry must be similarly safe to cache across a session.
const STATIC_PATHS = new Set([
  '/api/ratings/',
  '/api/regions.geojson',
  '/api/major-regions.geojson',
  '/api/sub-regions.geojson',
  '/api/resorts.geojson',
  '/api/bulletin-groupings.geojson',
]);

// SNOW-526: STATIC_PATHS entries that must only be persisted to the shell
// cache when the response declares itself ``immutable`` via Cache-Control
// — see the STATIC_PATHS comment above and shouldPersist() in
// basemap_cache_core.js.
const IMMUTABLE_ONLY_PATHS = new Set(['/api/bulletin-groupings.geojson']);

// ---------------------------------------------------------------------------
// Telemetry bridge (SNOW-384)
// ---------------------------------------------------------------------------
//
// A service worker has no ``window`` — ``window.pwaTelemetry`` (SNOW-385,
// static/js/telemetry.js) is unreachable from here. Instead we post a
// ``{type: 'pwa-telemetry', event, properties}`` message to the client(s)
// this worker controls; ``sw_register.js`` carries a matching
// ``navigator.serviceWorker.addEventListener('message', ...)`` listener
// that forwards it to ``window.pwaTelemetry.emit(event, properties)``. The
// envelope-level context fields (platform, install_state, sw_state,
// client_version, …) are attached by ``telemetry.js`` on the page side from
// ``window.pwaDb.context()`` — this helper only needs to supply the event
// name and any event-specific properties.
//
// Best-effort throughout: telemetry must never affect SW lifecycle
// behaviour, so every step here is wrapped and swallows its own errors.
//
// @param {string} eventName
// @param {object} [properties]
// @param {string} [clientId] Target a single client (e.g. the ``fetch``
//   event's originating client) rather than broadcasting to every open tab.
function _postTelemetry(eventName, properties, clientId) {
  const message = { type: 'pwa-telemetry', event: eventName, properties: properties || {} };
  const send = (client) => {
    try {
      client.postMessage(message);
    } catch (_err) {
      // Ignore — a torn-down client is not worth retrying for telemetry.
    }
  };
  try {
    if (clientId) {
      self.clients
        .get(clientId)
        .then((client) => {
          if (client) send(client);
        })
        .catch(() => {});
      return;
    }
    self.clients
      .matchAll({ includeUncontrolled: true, type: 'window' })
      .then((all) => all.forEach(send))
      .catch(() => {});
  } catch (_err) {
    // Non-fatal — telemetry must never break the SW lifecycle.
  }
}

// ---------------------------------------------------------------------------
// Lifecycle — install
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      // Pre-cache the offline fallback so the network-first strategy
      // can return it from cache when both network and per-page cache
      // miss (e.g. user opens a never-visited page while offline).
      const cache = await caches.open(CACHE_VERSION);
      await cache.addAll(PRECACHE_URLS);
      // SNOW-384: the browser fires 'install' once per successful
      // install cycle (a failed install can retry). Emitting after
      // cache.addAll() resolves means we only record successful
      // installs, so no extra idempotency guard is needed.
      _postTelemetry('pwa.sw.installed', { cache_version: CACHE_VERSION });
    })(),
  );
  // Deliberately NOT calling self.skipWaiting() here. The new worker
  // stays "waiting" until the page posts SKIP_WAITING (the user clicked
  // "Reload" on the update banner). A waiting worker is exactly what the
  // banner means by "an update is available" — activating silently would
  // break that contract. See the message handler below.
});

// ---------------------------------------------------------------------------
// Lifecycle — activate
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      try {
        // Reap caches from earlier SW versions so disk doesn't grow.
        // Use ``startsWith('snowdesk-shell-')`` rather than a strict
        // equality check so legacy ``map-shell-*`` caches from the
        // SNOW-9 precache controller also get cleared on first install
        // of the SNOW-79 SW (see the catch-all sweep below). SNOW-484:
        // also reaps stale ``snowdesk-basemap-*`` versions the same way,
        // but the second filter below excludes current caches
        // (CACHE_VERSION, BASEMAP_CACHE, and — SNOW-586 — every LIVE
        // per-area pinned bucket) from deletion — the shell and basemap
        // caches are versioned independently, so a shell-only
        // CACHE_VERSION bump must never wipe either, and a live pinned
        // bucket must never be swept just because it isn't a name this
        // worker recognises up front (there is no fixed list of area ids
        // to check against).
        //
        // SNOW-586: this is also what implements the "no migration"
        // decision — LEGACY_BASEMAP_PINNED_CACHE (the old single shared
        // pinned cache, SNOW-521/522) matches the ``snowdesk-basemap-``
        // prefix but is deliberately NOT treated as a live pinned bucket,
        // so it is swept and dropped outright on upgrade rather than
        // migrated into per-area buckets.
        const cacheNames = await caches.keys();
        const deletions = cacheNames
          .filter(
            (name) =>
              name.startsWith('snowdesk-shell-') ||
              name.startsWith('map-shell-') ||
              name.startsWith('snowdesk-basemap-'),
          )
          .filter((name) => {
            if (name === CACHE_VERSION || name === BASEMAP_CACHE) return false;
            if (name.startsWith(BASEMAP_PINNED_CACHE_PREFIX) && name !== LEGACY_BASEMAP_PINNED_CACHE) {
              return false; // a live per-area pinned bucket — keep it.
            }
            return true;
          })
          .map((name) => caches.delete(name));
        await Promise.all(deletions);
        // Take control of every open client the moment we activate. This is
        // safe now precisely because we no longer auto-skipWaiting on
        // install: activation only happens after the user opts into the
        // update (SKIP_WAITING) or after every tab has closed, so claiming
        // can't drive the dev reload-loop the old design avoided. Claiming
        // fires ``controllerchange`` in the page, which sw_register.js turns
        // into exactly one reload onto the new shell — guaranteeing the tab
        // actually moves to the new version rather than lingering on the old
        // worker.
        await self.clients.claim();
        // SNOW-384: the browser fires 'activate' exactly once per SW
        // instance — no extra gating needed for idempotency here.
        _postTelemetry('pwa.sw.activated', { cache_version: CACHE_VERSION });
      } catch (err) {
        // SNOW-384: pwa.sw.activation_failed is a critical event
        // (telemetry.js CRITICAL_EVENTS) — fires sendBeacon immediately
        // once forwarded by sw_register.js.
        _postTelemetry('pwa.sw.activation_failed', {
          cache_version: CACHE_VERSION,
          message: String((err && err.message) || err || 'unknown'),
        });
        throw err;
      }
    })(),
  );
});

// ---------------------------------------------------------------------------
// Fetch — strategy router
// ---------------------------------------------------------------------------

/**
 * SNOW-487: lazily rehydrate ``_basemapOrigins`` from the durable
 * ``meta:app`` IndexedDB row (key ``basemap.origins``) that
 * ``static/js/map.js``'s ``registerBasemapOrigins()`` writes alongside
 * its ``register-basemap-origins`` postMessage. The in-memory Set is
 * lost whenever the browser terminates an idle worker — a later
 * ``fetch`` event runs in a fresh global with an empty Set even though
 * the durable ``BASEMAP_CACHE`` still holds previously-cached
 * tiles/sprites/glyphs; without this, ``_classify()`` would route those
 * cross-origin requests to network-only and offline basemap rendering
 * would silently break after any idle period.
 *
 * Only replaces ``_basemapOrigins`` while it is still empty — an
 * explicit ``register-basemap-origins`` message (a live page, freshly
 * parsed ``BASEMAP_OPTIONS``) is always authoritative and must never be
 * shadowed by a stale IndexedDB read. Memoises the read in
 * ``_basemapHydration`` so a burst of cross-origin requests on a
 * freshly (re)started worker triggers one DB read, not one per
 * request. Tolerates a missing ``meta:app`` store — a worker-created
 * DB only has ``queue:mutations`` (see ``_openMutationsDb()``'s
 * docstring) — by leaving the Set empty rather than throwing.
 *
 * SNOW-722: a FAILED read is no longer memoised for the worker's whole
 * lifetime. It drops the memo so the next request retries, up to
 * ``BASEMAP_HYDRATION_MAX_ATTEMPTS`` — see that constant for why the retry
 * is bounded rather than unconditional. A SUCCESSFUL read is still
 * memoised exactly as before, failures or not.
 *
 * @returns {Promise<void>}
 */
function _hydrateBasemapOrigins() {
  if (_basemapHydration) return _basemapHydration;
  let failed = false;
  const attempt = (async () => {
    if (_basemapOrigins.size > 0) return;
    let db;
    try {
      db = await _openMutationsDb();
      const meta = await _idbGetAll(db, 'meta:app');
      const row = meta.find((r) => r.key === 'basemap.origins');
      if (row && Array.isArray(row.value) && _basemapOrigins.size === 0) {
        _basemapOrigins = new Set(row.value);
      }
    } catch (_err) {
      // meta:app missing (fresh worker-created DB), or a transient DB
      // open/read failure (blocked by a concurrent version upgrade, lock
      // contention) — leave _basemapOrigins empty; the cross-origin
      // request falls through to the read-only cache probe and then to the
      // network, as before SNOW-487.
      //
      // SNOW-722: flagged for the memo-drop below, so the next request
      // re-reads rather than reusing this empty result.
      failed = true;
    } finally {
      if (db) {
        try {
          db.close();
        } catch (_e) {
          // Non-fatal.
        }
      }
    }
  })();
  _basemapHydration = attempt;
  // SNOW-722: drop the memo on failure so the next request retries, but
  // only while under the attempt cap — the permanent missing-store case
  // lands in that catch too and must not buy a DB open per request forever.
  // Registered before the caller's own await, so a retrying caller sees the
  // cleared memo.
  //
  // The identity guard comes FIRST, before the counter bump: a
  // register-basemap-origins message that landed while this read was in
  // flight has already replaced the memo AND reset the retry budget, so a
  // superseded attempt must neither clear the fresh memo nor spend the
  // fresh budget. Its failure is news about a worker state that no longer
  // applies.
  attempt.then(() => {
    if (!failed) return;
    if (_basemapHydration !== attempt) return;
    _basemapHydrationFailures += 1;
    if (_basemapHydrationFailures >= BASEMAP_HYDRATION_MAX_ATTEMPTS) return;
    _basemapHydration = null;
  });
  return attempt;
}

/**
 * SNOW-585: lazily rehydrate ``_devShellCacheOptIn`` from the durable
 * ``meta:app`` IndexedDB row (key ``sw.devShellCache``) that
 * ``static/js/pwa_dev_shell_toggle.js`` writes alongside its
 * ``dev-shell-cache`` postMessage. Mirrors ``_hydrateBasemapOrigins()``
 * exactly, including tolerating a missing ``meta:app`` store (a
 * worker-created DB only has ``queue:mutations`` — see
 * ``_openMutationsDb()``'s docstring): a read failure just leaves the
 * default (opted-out), which is the safe direction for a bypass whose
 * whole point is "off means off" unless a developer deliberately asks for
 * the cache back. Memoised in ``_devShellCacheHydration`` so a burst of
 * ``static``-classified requests on a freshly (re)started worker triggers
 * one DB read, not one per request. Only called when ``DEV_SHELL_BYPASS``
 * is true — this never runs in production.
 *
 * @returns {Promise<void>}
 */
function _hydrateDevShellCacheOptIn() {
  if (_devShellCacheHydration) return _devShellCacheHydration;
  _devShellCacheHydration = (async () => {
    let db;
    try {
      db = await _openMutationsDb();
      const meta = await _idbGetAll(db, 'meta:app');
      const row = meta.find((r) => r.key === 'sw.devShellCache');
      if (row) _devShellCacheOptIn = !!row.value;
    } catch (_err) {
      // meta:app missing (fresh worker-created DB), or a transient DB
      // open/read failure — leave _devShellCacheOptIn at its default
      // (opted-out). Recovery comes from the next live page's
      // dev-shell-cache message, which resets _devShellCacheHydration.
    } finally {
      if (db) {
        try {
          db.close();
        } catch (_e) {
          // Non-fatal.
        }
      }
    }
  })();
  return _devShellCacheHydration;
}

/**
 * Synchronous portion of strategy classification: the ``method !== GET``
 * short-circuit, and every same-origin case. Returns ``null`` for a
 * cross-origin GET request because deciding that case may require the
 * async ``_basemapOrigins`` rehydration (SNOW-487) — see ``_classify()``.
 *
 * @param {Request} request
 * @param {URL} url
 * @returns {'static' | 'navigate' | 'network' | null}
 */
function _classifySync(request, url) {
  // SNOW-496: thin delegator — see basemap_cache_core.js's module header.
  // Inline fallback mirrors the ``self.pwaMutationQueueCore ||`` idiom
  // already used for the sync-event handler below, so a transient
  // importScripts 404 can't break the fetch path.
  if (self.pwaBasemapCacheCore) {
    return self.pwaBasemapCacheCore.classifySync(
      request,
      url,
      self.location.origin,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
  }
  if (request.method !== 'GET') return 'network';
  if (url.origin !== self.location.origin) return null;

  if (request.mode === 'navigate' || request.destination === 'document') {
    return 'navigate';
  }

  if (STATIC_PATHS.has(url.pathname)) return 'static';

  const dot = url.pathname.lastIndexOf('.');
  if (dot !== -1) {
    const ext = url.pathname.slice(dot).toLowerCase();
    if (STATIC_SHELL_EXTENSIONS.has(ext)) return 'static';
  }

  return 'network';
}

/**
 * Async portion of classification: the cross-origin GET case, split out
 * so the ``fetch`` listener can call it with the ``URL`` it has already
 * parsed rather than re-parsing and re-running ``_classifySync()``.
 * SNOW-484 opportunistic basemap caching — any other cross-origin request
 * (an unregistered CDN, etc.) stays network-only, unchanged from before
 * SNOW-484. ``_hydrateBasemapOrigins()`` (SNOW-487) may need to rehydrate
 * ``_basemapOrigins`` from IndexedDB before the allowlist check can run.
 *
 * @param {URL} url
 * @returns {Promise<'basemap' | 'network'>}
 */
async function _classifyCrossOriginGet(url) {
  await _hydrateBasemapOrigins();
  // SNOW-496: thin delegator — see basemap_cache_core.js's module header.
  const isBasemap = self.pwaBasemapCacheCore
    ? self.pwaBasemapCacheCore.isBasemapOrigin(url, _basemapOrigins)
    : _basemapOrigins.has(url.origin);
  if (isBasemap) return 'basemap';
  return 'network';
}

/**
 * Decide which strategy applies to a given request.
 *
 * Returns one of: ``'static'`` | ``'navigate'`` | ``'basemap'`` | ``'network'``.
 *
 * Delegates the cheap, synchronous cases to ``_classifySync()``; only
 * awaits anything for a cross-origin GET, via ``_classifyCrossOriginGet()``.
 * Retained as the single full-classification entry point (referenced by the
 * e2e tests' narrative); the ``fetch`` listener below skips it and calls the
 * two halves directly to avoid re-parsing the ``URL``.
 *
 * @param {Request} request
 * @returns {Promise<'static' | 'navigate' | 'basemap' | 'network'>}
 */
async function _classify(request) {
  const url = new URL(request.url);
  const sync = _classifySync(request, url);
  if (sync !== null) return sync;
  return _classifyCrossOriginGet(url);
}

/**
 * SNOW-482: reconstruct a cached ``Response`` with ``X-SW-Cache: hit``
 * added, so the page can tell a Cache-Storage replay apart from a real
 * server round-trip (``static/js/pwa_offline.js`` only advances the
 * persisted sync clock / appends a sync-log row when this header is
 * absent). Cached ``Response`` objects have immutable headers, so the
 * only way to add one is to build a new ``Response`` around the same
 * body/status/headers.
 *
 * @param {Response} response
 * @returns {Response}
 */
function _stampCacheHit(response) {
  const headers = new Headers(response.headers);
  headers.set('X-SW-Cache', 'hit');
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/**
 * Stale-while-revalidate: serve the cached response immediately if
 * present, kick off a background re-fetch to refresh the cache for
 * the next call. Falls through to network-only on cache miss.
 *
 * SNOW-585: when ``DEV_SHELL_BYPASS`` is on and the page hasn't opted
 * back in (see ``_hydrateDevShellCacheOptIn()``), this bypasses the
 * cache entirely — no read, no write — and goes straight to
 * ``fetch(request)``. That decision has to run before any cache
 * interaction, which is why it sits first, ahead of ``caches.open()``.
 */
async function _staleWhileRevalidate(request) {
  if (DEV_SHELL_BYPASS) {
    await _hydrateDevShellCacheOptIn();
    if (!_devShellCacheOptIn) return fetch(request);
  }
  const cache = await caches.open(CACHE_VERSION);
  const cached = await cache.match(request);
  const url = new URL(request.url);
  // SNOW-742: with no route to try, answer from what is on disk and stop.
  // Without this an offline reload still paid a stalled fetch per CSS file, JS
  // module and font — a request each, all of them doomed, all of them holding
  // a connection slot for the OS TCP timeout.
  if (!(await _shouldUseNetwork())) {
    return cached ? _stampCacheHit(cached) : _synthesizedGatewayTimeout();
  }
  const fetchPromise = _boundedFetch(request, SHELL_FETCH_BUDGET_MS)
    .then((response) => {
      // Only cache successful, basic (same-origin) responses. ``opaque``
      // responses from cross-origin no-cors requests are unreadable, and
      // 4xx/5xx would poison the cache.
      if (response && response.ok && response.type === 'basic') {
        // SNOW-526/SNOW-496: thin delegator — see basemap_cache_core.js's
        // module header. Gates IMMUTABLE_ONLY_PATHS entries (currently just
        // /api/bulletin-groupings.geojson) on the response's own
        // Cache-Control: immutable declaration so an unsettled date is
        // never written to the shell cache. The inline fallback mirrors
        // shouldPersist()'s exact token-split/trim/includes match (not a
        // bare substring test) so the two implementations can't disagree.
        const persist = self.pwaBasemapCacheCore
          ? self.pwaBasemapCacheCore.shouldPersist(url, response, IMMUTABLE_ONLY_PATHS)
          : !IMMUTABLE_ONLY_PATHS.has(url.pathname) ||
            (response.headers.get('Cache-Control') || '')
              .toLowerCase()
              .split(',')
              .map((token) => token.trim())
              .includes('immutable');
        if (persist) {
          cache.put(request, response.clone()).catch(() => {});
        }
      }
      return response;
    })
    .catch(() => null);
  if (cached) return _stampCacheHit(cached);
  const network = await fetchPromise;
  if (network) return network;
  return _synthesizedGatewayTimeout();
}

/**
 * SNOW-484: trim ``cache`` down to at most ``max`` entries, oldest first.
 * ``Cache.keys()`` returns entries in insertion order, so deleting the
 * earliest ``length - max`` keys is an LRU-by-insertion-order approximation
 * — no per-entry timestamp bookkeeping needed, which keeps the basemap
 * cache path cheap on every put.
 *
 * @param {Cache} cache
 * @param {number} max
 */
async function _trimCache(cache, max) {
  // SNOW-496: thin delegator — see basemap_cache_core.js's module header.
  if (self.pwaBasemapCacheCore) {
    return self.pwaBasemapCacheCore.trimCache(cache, max);
  }
  const keys = await cache.keys();
  const excess = keys.length - max;
  if (excess <= 0) return;
  await Promise.all(keys.slice(0, excess).map((key) => cache.delete(key)));
}

// SNOW-568: how many warm-cache fetches may be in flight at once. Six
// matches the per-host connection limit every major browser applies to
// HTTP/1.1, so the pool keeps the pipe full without queueing work the
// network stack would only stall on. The previous unbounded fan-out
// issued the whole list in one tick — up to 4096 fetches for a
// full-ceiling area download — and Chrome answered with
// ERR_INSUFFICIENT_RESOURCES on every one of them.
const WARM_CACHE_CONCURRENCY = 6;

// Tile-grid rework: shortest gap between two 'warm-cache-progress' messages. The
// on-map grid wants to know about each tile as it lands, but a
// full-ceiling run settles thousands of them and one postMessage each
// would flood the page with structured clones for no visible gain. ~8
// reports a second is under a frame's worth of work and still faster than
// the eye resolves individual squares appearing.
const PROGRESS_FLUSH_MS = 120;

// SNOW-632: requestIds the page has asked to cancel. Module-level rather
// than closed over by one 'message' handler invocation, because the
// 'warm-cache' and its later 'warm-cache-cancel' arrive as two independent
// `message` events — the handler runs fresh each time, with no shared
// closure between them — so this Set is the only channel connecting a
// cancel request to the run it names. Cleared of an id the moment that
// run's own promise settles (see the 'warm-cache' handler below), so the
// common case never grows it past one entry.
//
// A cap on top of that prompt cleanup: a cancel can arrive for a
// requestId whose run has already settled (a duplicate click, a message
// racing the done reply) or that never existed (a typo, a stale page).
// Neither is ever cleaned up by the settle path above, so an append-only
// Set would grow one entry per stray message for the life of the worker.
// Oldest-first eviction bounds it instead — the same trade-off
// `_trimCache` makes for the basemap cache itself.
//
// That eviction order has an ordering dependency worth naming: it assumes
// a live run's id is always among the newest ``WARM_CACHE_CANCEL_SET_MAX``
// entries. In normal operation that holds — one slot, one cancel per slot
// — but if ``WARM_CACHE_CANCEL_SET_MAX`` stray entries (duplicate clicks,
// cancels for ids that never existed) arrived after a live run's id was
// inserted, the live id would be the oldest and get evicted, silently
// un-cancelling that run. Not reachable today; flagged so a future change
// to how/when cancels are recorded doesn't reintroduce it unnoticed.
const WARM_CACHE_CANCEL_SET_MAX = 32;
const _warmCacheCancelledIds = new Set();

/**
 * Record a cancellation request for ``requestId``, evicting the oldest
 * entry first if that would push the set past ``WARM_CACHE_CANCEL_SET_MAX``
 * — see the constants' own comment for why the set needs a cap at all.
 *
 * @param {*} requestId
 */
function _markWarmCacheCancelled(requestId) {
  if (requestId === undefined || requestId === null) return;
  _warmCacheCancelledIds.add(requestId);
  if (_warmCacheCancelledIds.size > WARM_CACHE_CANCEL_SET_MAX) {
    const oldest = _warmCacheCancelledIds.values().next().value;
    _warmCacheCancelledIds.delete(oldest);
  }
}

/**
 * Drop ``requestId`` from the cancelled set — called once a run's promise
 * settles (or, for the ``pinned`` guard path, once it is refused outright)
 * so a finished run's id doesn't sit in the set for the rest of the
 * worker's life.
 *
 * @param {*} requestId
 */
function _clearWarmCacheCancelled(requestId) {
  _warmCacheCancelledIds.delete(requestId);
}

// SNOW-748: requestIds of warm-cache runs currently in flight. The cancelled
// set above records what the PAGE asked to stop; this one records what there
// is to stop, which is the half the worker never had. ``_forceOffline`` needs
// it: the user switching on "Offline mode" mid-download is a cancel request
// for whatever is running, and the worker is the only side that knows what
// that is
// (the page posts a cancel for one control's own run, not for anyone else's).
//
// Entries are added as a run is dispatched and removed as its promise settles,
// so in normal operation the set holds at most one — but it is capped the same
// way as the cancelled set, for the same reason: an entry that somehow escapes
// the settle path must not accumulate for the life of the worker.
const _warmCacheActiveIds = new Set();

/**
 * Record that a warm-cache run is in flight under ``requestId``, evicting the
 * oldest entry first past ``WARM_CACHE_CANCEL_SET_MAX`` — the same discipline
 * (and the same cap) as ``_markWarmCacheCancelled``.
 *
 * @param {*} requestId
 */
function _markWarmCacheActive(requestId) {
  if (requestId === undefined || requestId === null) return;
  _warmCacheActiveIds.add(requestId);
  if (_warmCacheActiveIds.size > WARM_CACHE_CANCEL_SET_MAX) {
    const oldest = _warmCacheActiveIds.values().next().value;
    _warmCacheActiveIds.delete(oldest);
  }
}

/**
 * Drop ``requestId`` from the in-flight set — called once its run settles,
 * cancelled or not.
 *
 * @param {*} requestId
 */
function _clearWarmCacheActive(requestId) {
  _warmCacheActiveIds.delete(requestId);
}

/**
 * SNOW-748: cancel every warm-cache run currently in flight, through the
 * ordinary cancellation protocol rather than a second abort mechanism — each
 * run's pool polls ``shouldCancel`` against the cancelled set and stops
 * dispatching, and the summary it returns carries ``cancelled: true`` with
 * ``failed: 0``, which is the shape ``basemap_download_runner.js``'s callers
 * already know how to finish on.
 *
 * Called only from ``_forceOffline``. An auto-latch deliberately does NOT call
 * it — see that function's own note.
 */
function _cancelActiveWarmCacheRuns() {
  _warmCacheActiveIds.forEach((requestId) => _markWarmCacheCancelled(requestId));
}

/**
 * Handle a ``'warm-cache-cancel'`` message's ``data`` payload — factored
 * out of the ``message`` listener below so it is callable on its own, not
 * just wired to ``addEventListener``. Vitest's sandbox stubs
 * ``addEventListener`` as a no-op (see tests/js/test_sw.js), so a body left
 * inline in the listener would never run under the unit suite; extracting
 * it here lets a test exercise the exact field name (``requestId``) the
 * listener reads off the message, not just _markWarmCacheCancelled in
 * isolation.
 *
 * @param {*} data - the message event's ``data`` payload.
 */
function _handleWarmCacheCancelMessage(data) {
  _markWarmCacheCancelled(data.requestId);
}

/**
 * SNOW-568: thin delegator — see basemap_cache_core.js's module header.
 * The inline fallback (used only if that importScripts 404s) runs the
 * list sequentially: slower than the pool, but never the unbounded
 * fan-out this ticket exists to remove.
 *
 * @param {Array<*>} items
 * @param {number} limit
 * @param {(item: *, index: number) => Promise<*>} worker
 * @returns {Promise<void>}
 */
async function _warmCacheRunPool(items, limit, worker) {
  if (self.pwaBasemapCacheCore && self.pwaBasemapCacheCore.runPool) {
    return self.pwaBasemapCacheCore.runPool(items, limit, worker);
  }
  for (let i = 0; i < items.length; i++) {
    await worker(items[i], i);
  }
}

/**
 * SNOW-568: thin delegator — see basemap_cache_core.js's module header.
 *
 * @param {*} err
 * @returns {'quota'|'network'|'other'}
 */
function _warmCacheClassifyFailure(err) {
  if (self.pwaBasemapCacheCore && self.pwaBasemapCacheCore.classifyFailure) {
    return self.pwaBasemapCacheCore.classifyFailure(err);
  }
  if (err && err.name === 'QuotaExceededError') return 'quota';
  if (err && err.name === 'TypeError') return 'network';
  return 'other';
}

// D3 (docs/code-reviews/2026-08-03-js-review.md): the precedence order the
// inline fallback below ranks by. A hand-copy of
// ``basemap_cache_core.js``'s own ``REASON_PRECEDENCE`` — most actionable
// first — kept in step with it by tests/js/test_sw.js, which runs both
// implementations over one shared input table. The previous fallback was
// ``a || b || null``, which returns whichever reason arrived first; a run
// that hit one storage-quota failure among a thousand network failures
// reported "network error" on the degraded-startup path, hiding the one
// reason with a distinct remedy and the only one a retry cannot clear.
const WARM_CACHE_REASON_PRECEDENCE = ['quota', 'network', 'other'];

/**
 * SNOW-568: thin delegator — see basemap_cache_core.js's module header.
 * The inline fallback mirrors ``worseReason``'s ranking, not just its
 * signature — see ``WARM_CACHE_REASON_PRECEDENCE`` above.
 *
 * @param {string|null} a
 * @param {string|null} b
 * @returns {string|null}
 */
function _warmCacheWorseReason(a, b) {
  if (self.pwaBasemapCacheCore && self.pwaBasemapCacheCore.worseReason) {
    return self.pwaBasemapCacheCore.worseReason(a, b);
  }
  const rankA = WARM_CACHE_REASON_PRECEDENCE.indexOf(a);
  const rankB = WARM_CACHE_REASON_PRECEDENCE.indexOf(b);
  if (rankA === -1 && rankB === -1) return null;
  if (rankA === -1) return b;
  if (rankB === -1) return a;
  return rankA <= rankB ? a : b;
}

/**
 * SNOW-586: thin delegator — see basemap_cache_core.js's module header.
 * The inline fallback mirrors ``responseBytes``'s own Content-Length-then-
 * blob-then-zero fallback chain, so a transient importScripts 404 still
 * gets a usable (if less exact on very old header-stripping proxies)
 * answer rather than losing byte accounting for the whole run.
 *
 * @param {Response} response
 * @returns {Promise<number>}
 */
async function _warmCacheResponseBytes(response) {
  if (self.pwaBasemapCacheCore && self.pwaBasemapCacheCore.responseBytes) {
    return self.pwaBasemapCacheCore.responseBytes(response);
  }
  if (!response) return 0;
  try {
    const header = response.headers && response.headers.get('Content-Length');
    if (header !== null && header !== undefined) {
      const n = Number(header);
      if (Number.isFinite(n) && n >= 0) return n;
    }
  } catch (_e) {
    // Fall through to the blob fallback below.
  }
  try {
    const blob = await response.clone().blob();
    return blob.size;
  } catch (_e) {
    return 0;
  }
}

/**
 * SNOW-492: eagerly fetch + cache a caller-supplied list of URLs — the
 * "Download basemap" control map.js wires into the map's Options menu.
 * Splits by origin into the same two cache *families* the fetch handler
 * above already reads from, so a subsequent offline reload of the current
 * view is served from a warm cache rather than depending on
 * stale-while-revalidate having already run for every one of these URLs:
 * same-origin data feeds → the shell cache (``CACHE_VERSION``);
 * basemap-origin tiles/sprites/glyphs/style JSON → ``BASEMAP_CACHE`` (SNOW-521:
 * or, when ``options.pinned`` is true, SNOW-586's per-area pinned bucket
 * — ``BASEMAP_PINNED_CACHE_PREFIX + options.areaId`` — a deliberate
 * download uses instead, exempt from the passive-browsing LRU trim and
 * from every OTHER area's own eviction). Only caches an ``ok`` response of
 * the expected ``type`` — ``'basic'`` same-origin, ``'cors'`` cross-origin
 * — mirroring the exact checks ``_staleWhileRevalidate`` /
 * ``_basemapStaleWhileRevalidate`` already apply, so a warmed entry is
 * guaranteed servable by those same read paths. Every URL is fetched
 * independently — one failure doesn't abort the rest — and (SNOW-586: only
 * for the non-pinned ``BASEMAP_CACHE`` path) the basemap cache used is
 * trimmed afterwards since a single run can add hundreds of tile entries
 * in one burst. A pinned run is never trimmed here — SNOW-586 replaced
 * that entry-count cap with the page's own byte-budget/whole-area-eviction
 * plan (map.js, basemap_download_core.js's ``planEviction``), which runs
 * BEFORE this call rather than after it.
 *
 * SNOW-521: ``options.onProgress(done, total, settled)``, if supplied, is
 * called once per settled URL, throttled to roughly every 5% of ``total``
 * (plus always on the final URL) so a caller can drive a live n/total
 * readout without a flood of calls for a large run.
 *
 * Tile-grid rework: ``settled`` carries the INDICES into ``urls`` that succeeded
 * since the previous report, so a caller that built the list knows which
 * tiles are now cached rather than just how many — that is what lets the
 * map fill its progress grid square by square. Failed URLs are omitted
 * (they count toward ``done``, but nothing was cached). Because the
 * indices are what makes it useful, reports also go out on a
 * ``PROGRESS_FLUSH_MS`` timer, not on the 5% buckets alone.
 *
 * SNOW-568: the fan-out is bounded to ``WARM_CACHE_CONCURRENCY`` in-flight
 * fetches (see that constant), and the returned summary carries a
 * ``reason`` — the most actionable failure classification seen across the
 * run, or ``null`` when nothing failed. Previously every error was
 * collapsed into the ``failed`` count with the error object discarded, so
 * a caller could not tell a full disk from a flaky network and had
 * nothing to tell the user.
 *
 * SNOW-586: the summary also carries ``bytes`` — the sum of every
 * successfully-cached response's size (``_warmCacheResponseBytes``),
 * accumulated regardless of ``pinned`` (a caller only reading it for a
 * pinned run costs a non-pinned caller nothing). The page records this
 * against the area's standing budget entry once the run settles.
 *
 * SNOW-632: ``onProgress`` is also handed the running ``bytes`` total as a
 * fourth argument, so a caller can show live on-disk MB rather than only a
 * fraction of ``total``. And ``options.shouldCancel``, if supplied, is
 * polled once at the START of every pool worker — before that URL's fetch
 * is dispatched — and a truthy answer skips the fetch entirely rather than
 * aborting one already under way. That is a deliberate half-measure, not
 * an oversight: up to ``WARM_CACHE_CONCURRENCY`` fetches can already be
 * in flight when cancellation lands, and letting them finish and write is
 * simpler and cheaper than tearing a partial cache write back out, at the
 * cost of a handful of tiles the user technically asked to stop. The
 * returned summary's ``cancelled`` flag is true the moment ``shouldCancel``
 * is first seen to return true, so a caller can tell a cancelled run apart
 * from one that simply finished — a cancelled run is never treated as a
 * failure (``failed`` only counts URLs that were actually attempted and
 * did not succeed).
 *
 * @param {string[]} urls
 * @param {{pinned?: boolean, areaId?: string, onProgress?: (done: number,
 *   total: number, settled: number[], bytes: number) => void,
 *   shouldCancel?: () => boolean}} [options] ``areaId`` is REQUIRED when
 *   ``pinned`` is true — the caller (the ``message`` handler below)
 *   refuses the run rather than calling this with ``pinned`` and no
 *   ``areaId``, so this function can assume the pair is already valid.
 * SNOW-748: refused outright while the user has forced offline mode, with the
 * shape of a cancelled run — ``cancelled: true``, ``failed: 0``, nothing
 * fetched — because that is what it is: the user has said not to use this
 * connection, and a run that never starts has no failures to report. The
 * distinct ``reason`` (``'offline-forced'``) is for a caller that wants to say
 * why; the callers that only ask "did it succeed" already read ``cancelled``
 * first (see ``basemap_download_runner.js``). An auto-LATCH is deliberately
 * not refused here — see the "Both apply to READ paths only" note at the head
 * of this file for why the two offline modes part company at exactly this
 * point.
 *
 * @returns {Promise<{ok: number, failed: number, reason: string|null,
 *   bytes: number, cancelled: boolean}>}
 */
async function _warmCache(urls, options) {
  // SNOW-748: the user's own offline mode, refused before a cache is even
  // opened. Not ``!== 'auto'``: an auto-latch must still let a download
  // through.
  //
  // Hydrated first, for the same reason ``_shouldUseNetwork`` awaits it: on a
  // restarted worker the mode is not in memory yet, and a run started against
  // the startup default would pull tiles down a connection the user has told
  // the app not to spend.
  await _hydrateNetworkMode();
  if (_networkMode === 'offline-forced') {
    return { ok: 0, failed: 0, reason: 'offline-forced', bytes: 0, cancelled: true };
  }
  const opts = options || {};
  const pinned = !!opts.pinned;
  const areaId = opts.areaId;
  const onProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;
  // SNOW-632: see the docstring's cancellation note — polled once per pool
  // worker, before that URL's fetch.
  const shouldCancel = typeof opts.shouldCancel === 'function' ? opts.shouldCancel : null;
  const shellCache = await caches.open(CACHE_VERSION);
  // SNOW-613: `caches.open` CREATES a pinned bucket that did not exist, so
  // a pinned run can add a name the memoised list below has not seen. Drop
  // it rather than test for membership — a run happens once, an offline
  // tile read happens thousands of times, and the next read repopulates.
  if (pinned) _invalidatePinnedCacheNames();
  const basemapCache = await caches.open(
    pinned ? BASEMAP_PINNED_CACHE_PREFIX + areaId : BASEMAP_CACHE,
  );
  const list = Array.isArray(urls) ? urls : [];
  const total = list.length;
  let ok = 0;
  let failed = 0;
  let done = 0;
  // SNOW-586: summed across every successful write — see the docstring's
  // ``bytes`` note.
  let bytes = 0;
  let lastReportedBucket = -1;
  let lastReportedAt = 0;
  // Tile-grid rework: which list indices have settled since the last report. The
  // caller pairs these against the URL list it built, so it knows which
  // TILES landed rather than only how many — see the ``settled`` note on
  // ``onProgress`` in this function's docstring.
  let settledSince = [];
  // SNOW-632: true from the moment ``shouldCancel`` is first seen to
  // return true — see the docstring's cancellation note.
  let cancelled = false;
  const reportProgress = () => {
    if (!onProgress || total === 0) return;
    const final = done === total;
    const bucket = Math.floor((done / total) * 20); // 20 buckets ≈ every 5%
    // Tile-grid rework: the 5% buckets alone are too coarse to drive a per-tile
    // grid — a big run would light up a whole row of squares at once — so
    // a report also goes out whenever PROGRESS_FLUSH_MS has passed. That
    // bounds the message rate rather than the message count: a slow
    // download reports roughly per tile, a fast one batches whatever
    // settled inside the window.
    const elapsed = Date.now() - lastReportedAt;
    if (bucket === lastReportedBucket && elapsed < PROGRESS_FLUSH_MS && !final) return;
    lastReportedBucket = bucket;
    lastReportedAt = Date.now();
    const settled = settledSince;
    settledSince = [];
    // SNOW-632: the running byte total rides along as a fourth argument so
    // a caller can show live on-disk MB, not just a done/total fraction.
    onProgress(done, total, settled, bytes);
  };
  // SNOW-568: the most actionable failure seen so far, or null.
  let reason = null;
  const noteFailure = (why) => {
    reason = _warmCacheWorseReason(reason, why);
  };

  /**
   * Fetch one URL and write it to the cache its origin belongs to.
   * Returns the failure reason, or null on success. Never throws — a
   * rejection here would abandon every URL still queued behind it in the
   * pool.
   *
   * @param {string} rawUrl
   * @returns {Promise<string|null>}
   */
  const warmOne = async (rawUrl) => {
    let url;
    try {
      url = new URL(rawUrl, self.location.origin);
    } catch (_err) {
      // A malformed entry in the caller's list, not a runtime condition
      // the user can act on — classified directly rather than through
      // _warmCacheClassifyFailure, whose TypeError branch means "the
      // network was unreachable", which this is not.
      return 'other';
    }
    const sameOrigin = url.origin === self.location.origin;
    try {
      const response = await fetch(url.toString());
      const validType = sameOrigin ? response.type === 'basic' : response.type === 'cors';
      if (!response || !response.ok || !validType) {
        // A reachable server that answered with the wrong thing (404,
        // 5xx, an opaque redirect). Retrying is pointless, but it is
        // also not a quota problem — 'other' keeps it out of the
        // "free up space" message.
        return 'other';
      }
      const cache = sameOrigin ? shellCache : basemapCache;
      // SNOW-624: a same-origin response that is page HTML goes into the
      // shell cache alongside the ones `_networkFirst` writes, and the
      // offline read refuses any shell entry without a matching
      // `X-SW-Principal` stamp (C1). Writing one unstamped would put it in
      // the cache and make it permanently unservable — the user would see
      // `offline.html` for a page the device demonstrably holds, with no
      // error anywhere.
      //
      // Latent until now: both callers pass feeds and basemap tiles, so no
      // HTML has ever reached here. But warming a page URL ahead of time is
      // an obvious thing to want, and the trap would have sprung silently
      // months later. Stamping rather than refusing, because that makes the
      // obvious thing work instead of merely failing loudly.
      //
      // Scoped by content type so the feed path is untouched: reading the
      // principal means buffering the body, and `/api/*` responses carry no
      // `pwa-user-id` meta tag and are read back by `_staleWhileRevalidate`,
      // which does not check a principal at all.
      if (sameOrigin && _isHtmlResponse(response)) {
        const html = await response.clone().text();
        await cache.put(
          url.toString(),
          _stampPrincipal(response.clone(), _principalFromHtml(html)),
        );
      } else {
        await cache.put(url.toString(), response.clone());
      }
      // SNOW-586: measured off the same (unconsumed) response the clone
      // above was taken from — responseBytes clones again internally for
      // its blob fallback, so this never races the cache.put write.
      bytes += await _warmCacheResponseBytes(response);
      return null;
    } catch (err) {
      return _warmCacheClassifyFailure(err);
    }
  };

  await _warmCacheRunPool(list, WARM_CACHE_CONCURRENCY, async (rawUrl, index) => {
    // SNOW-632: the cancellation point — checked before this URL's fetch
    // is dispatched, never after. `runPool`'s shared cursor (see
    // basemap_cache_core.js) keeps advancing through the rest of the list
    // regardless — each remaining worker call lands here, sees the same
    // answer, and returns immediately, so the pool still drains normally
    // and quickly rather than needing its own abort path.
    if (shouldCancel && shouldCancel()) {
      cancelled = true;
      return;
    }
    let why = await warmOne(rawUrl);
    // One retry for a transient failure. A quota failure is never
    // retried — the disk will not have grown between the two attempts,
    // and a second full run of retries against a full disk just doubles
    // the time the user waits for the same answer.
    if (why && why !== 'quota') {
      why = await warmOne(rawUrl);
    }
    if (why) {
      failed += 1;
      noteFailure(why);
    } else {
      ok += 1;
      // Successes only. A failed tile must not light up its square — the
      // grid's job is to show what is actually cached. Skipped entirely
      // when there is no onProgress: nothing would ever drain the array.
      if (onProgress) settledSince.push(index);
    }
    done += 1;
    reportProgress();
  });
  // SNOW-586: a pinned run is never trimmed — its own bucket IS the unit
  // of eviction now, decided by the page before this call ever runs (see
  // the docstring). Only the passive BASEMAP_CACHE still gets the
  // entry-count trim.
  if (!pinned) {
    // Once per run, not per tile, so this stays an unbatched trim — and it
    // resets the batch counter, since the cache is at its limit right after.
    _basemapPutsSinceTrim = 0;
    await _trimCache(basemapCache, BASEMAP_CACHE_MAX_ENTRIES).catch(() => {});
  }
  // SNOW-742: make this area's labels as durable as its tiles.
  if (pinned && opts.glyphPrefix) {
    bytes += await _promoteGlyphs(opts.glyphPrefix, basemapCache);
  }
  return { ok, failed, reason, bytes, cancelled };
}

/**
 * SNOW-742: copy the glyph entries already in ``BASEMAP_CACHE`` into a pinned
 * bucket, so a downloaded area stops losing its labels.
 *
 * The bug this fixes is not "glyphs are never downloaded". They ARE fetched,
 * by ordinary browsing, via ``_basemapStaleWhileRevalidate`` — but they land
 * in ``BASEMAP_CACHE``, which is FIFO-trimmed to ``BASEMAP_CACHE_MAX_ENTRIES``
 * (600), while pinned buckets are never trimmed at all. A single browsing
 * session logs 150-300 distinct URLs (see that constant's note), so within a
 * couple of sessions the glyphs an area needs are evicted while its tiles sit
 * safe in the pinned bucket. The area quietly decays into geometry with no
 * labels — which is what "the map only partially loaded" looked like.
 *
 * Deliberately NOT an enumeration of every glyph range the style could ask
 * for. That would mean re-deriving MapLibre's own range logic, which
 * ``computeBasemapSpriteURLs`` (map_basemap_downloads.js) rejected for good
 * reasons that still hold. Ranges the user has never browsed stay uncovered,
 * exactly as before; what changes is that an area stops losing the ones it
 * already had.
 *
 * Idempotent: ``cache.put`` overwrites, so re-downloading an area re-promotes
 * the same entries rather than duplicating them. The byte total is returned so
 * the caller can add it to the run's own — a promoted glyph occupies real disk
 * in the pinned bucket, and the page's budget has to see it. A re-download
 * REPLACES the area's recorded size rather than adding to it (SNOW-632), so
 * counting these cannot inflate the budget across runs.
 *
 * @param {string} prefix The active style's glyph URL prefix — everything
 *   before the first ``{`` in its ``glyphs`` template.
 * @param {Cache} pinnedCache The open pinned bucket for this area.
 * @returns {Promise<number>} Bytes promoted; 0 if nothing matched or the
 *   passive cache could not be read.
 */
async function _promoteGlyphs(prefix, pinnedCache) {
  let promoted = 0;
  try {
    const passive = await caches.open(BASEMAP_CACHE);
    const requests = await passive.keys();
    for (const request of requests) {
      if (!request.url.startsWith(prefix)) continue;
      const response = await passive.match(request);
      if (!response || !response.ok) continue;
      await pinnedCache.put(request, response.clone());
      promoted += await _warmCacheResponseBytes(response);
    }
  } catch (_err) {
    // Best-effort, and deliberately silent. A download whose tiles all landed
    // has succeeded; failing it over the labels would be a worse answer than
    // the labels that browsing will re-cache anyway.
  }
  return promoted;
}

/**
 * SNOW-742: ``fetch`` with a deadline, for read paths only.
 *
 * Resolves exactly as ``fetch`` does, and rejects on a timeout the same way it
 * rejects on a refusal — that equivalence is the point. Every fallback in this
 * worker was written against a rejection, so converting a hang into one makes
 * all of them fire on time without restating any of their logic.
 *
 * A timeout also feeds the latch: it increments ``_consecutiveTimeouts`` and
 * trips ``_latchOffline()`` on the third in a row, while any success resets the
 * count. A REFUSAL deliberately does not count. A refusal is a fast, honest
 * answer the existing fallbacks already handle well, and it is what a browser
 * gives for a blocked request, a CORS failure or a DNS miss on an otherwise
 * live connection — latching on those would take the app offline while the
 * network is fine.
 *
 * @param {Request|string} request
 * @param {number} ms Budget in milliseconds.
 * @returns {Promise<Response>}
 */
async function _boundedFetch(request, ms) {
  // An explicit AbortController rather than ``AbortSignal.timeout(ms)``, for
  // two reasons. It is controllable by a test's fake clock — ``timeout``'s
  // internal timer is not driven by ``setTimeout``, so a suite could only
  // exercise a real multi-second wait — and it works on every browser that
  // has a service worker at all, where ``timeout`` needs Safari 16.
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, ms);
  let response;
  try {
    response = await fetch(request, { signal: controller.signal });
  } catch (err) {
    // Only OUR abort is evidence about the route. A refusal is a fast, honest
    // answer the existing fallbacks handle well, and it is also what a browser
    // gives for a blocked request, a CORS failure or a DNS miss on an
    // otherwise live connection — latching on those would take the app offline
    // while the network is fine.
    if (timedOut) _recordReadPathTimeout();
    throw err;
  } finally {
    clearTimeout(timer);
  }
  _recordReadPathSuccess();
  return response;
}

/**
 * True when a read path should attempt the network at all.
 *
 * False under either offline mode — latched or user-forced (SNOW-748) — and
 * false when the platform already knows the interface
 * is down (``navigator.onLine === false``) — that second case never needed a
 * budget to discover, and checking it costs nothing. The Underground case is
 * precisely the one where ``onLine`` stays TRUE, which is why the latch has to
 * exist alongside it rather than instead of it.
 *
 * SNOW-748: ASYNC, because it awaits ``_hydrateNetworkMode()`` first. A
 * restarted worker holds no mode at all until that read lands, so answering
 * from ``_networkMode``'s startup default would use the network for every
 * request that raced the read — which is the bug, merely narrowed. The await
 * is a settled promise for every call after the first.
 *
 * @returns {Promise<boolean>}
 */
async function _shouldUseNetwork() {
  await _hydrateNetworkMode();
  // ANY offline mode blocks the network — latched or forced, the promise to
  // the user is the same one: this app is not calling the server.
  if (_networkMode !== 'auto') return false;
  return !(typeof navigator !== 'undefined' && navigator.onLine === false);
}

/**
 * SNOW-490: the synthesized offline-miss response, stamped so
 * ``pwa_offline.js`` cannot mistake it for a successful sync.
 *
 * SNOW-742 factored this out of the two stale-while-revalidate paths that had
 * a copy each, and gave it a third caller: a latched read path answers with it
 * the moment every cache partition has missed, without touching the network.
 *
 * @returns {Response}
 */
function _synthesizedGatewayTimeout() {
  return new Response('', {
    status: 504,
    statusText: 'Gateway Timeout',
    headers: { 'X-SW-Cache': 'miss' },
  });
}

/**
 * Refresh one basemap entry in the background, writing only ``BASEMAP_CACHE``.
 *
 * Fire-and-forget by design: the caller has already answered from cache, so
 * nothing waits on this and a failure is not an error — it is the ordinary
 * outcome on a weak connection. Never writes a pinned bucket (see the SNOW-521
 * note on ``_basemapStaleWhileRevalidate``).
 *
 * @param {Request} request
 * @param {Cache} cache The open ``BASEMAP_CACHE``.
 */
function _revalidateBasemap(request, cache) {
  _boundedFetch(request, BASEMAP_FETCH_BUDGET_MS)
    .then(async (response) => {
      if (response && response.ok && response.type === 'cors') {
        await cache.put(request, response.clone()).catch(() => {});
        await _trimBasemapCacheEvery(cache);
      }
    })
    .catch(() => {});
}

/**
 * Note that a read-path request answered within its budget, so the route is
 * alive. Clears the timeout run; does NOT unlatch, because while latched no
 * read path calls the network at all and so nothing can reach here — an
 * unlatch is the probe's job alone.
 */
function _recordReadPathSuccess() {
  _consecutiveTimeouts = 0;
}

/**
 * Note that a read-path request burned its whole budget, and latch once
 * ``OFFLINE_LATCH_THRESHOLD`` of them have happened in a row.
 */
function _recordReadPathTimeout() {
  _consecutiveTimeouts += 1;
  if (_consecutiveTimeouts >= OFFLINE_LATCH_THRESHOLD) _latchOffline();
}

/**
 * Enter offline mode: read paths stop touching the network entirely until a
 * probe (or the user) says otherwise.
 *
 * Idempotent — a second call while already latched neither restarts the
 * backoff nor re-notifies the page, so a burst of timeouts arriving after the
 * third does not reset the probe schedule it just set.
 *
 * SNOW-748: the guard is ``!== 'auto'``, not ``=== 'offline'``. Timeouts keep
 * arriving under ``'offline-forced'`` (an in-flight read can still time out
 * just after the user forces the mode), and latching there would DOWNGRADE the
 * user's choice to an auto-latch — which schedules the probe that then
 * unlatches it. Only a mode nothing has claimed yet can be latched.
 *
 * SNOW-748: it deliberately does NOT cancel a warm-cache run in flight, where
 * ``_forceOffline`` does. The latch is a guess drawn from three read timeouts,
 * and a download is a long operation the user explicitly asked for on a
 * connection they believe they have; killing it on a guess would be the
 * worker overruling the user. See ``_warmCache``'s own guard for the other
 * half of that distinction.
 */
function _latchOffline() {
  if (_networkMode !== 'auto') return;
  _networkMode = 'offline';
  _consecutiveTimeouts = 0;
  _probeBackoffIndex = 0;
  _scheduleProbe();
  _publishNetworkMode();
}

/**
 * SNOW-748: enter offline mode because the USER asked for it, from the
 * "Offline mode" row in the account menu (templates/includes/nav.html).
 *
 * Sibling to ``_latchOffline`` and identical to it in every way but one: it
 * deliberately does NOT call ``_scheduleProbe()``. That omission IS the fix.
 * A probe exists to notice that a dead route came back; a forced mode is not
 * waiting for a route, it is a user who has one and has chosen not to spend it
 * (a metered roam, a battery to nurse, a tunnel they are about to enter). The
 * probe would succeed on the first attempt and ``_unlatchOffline`` would undo
 * the user's choice within thirty seconds.
 *
 * Any pending probe from a previous auto-latch is cancelled for the same
 * reason: it would fire under the forced mode and unlatch it.
 *
 * So is any basemap download in flight, and that one is NOT shared with the
 * latch. ``_warmCache`` bypasses the latch on purpose — a latch is the
 * worker's guess that there is no route, and a download the user explicitly
 * asked for should still be attempted in case the guess is wrong. A forced
 * mode is the opposite kind of fact: the user has said not to spend this
 * connection, and continuing to pull tiles down it would be the app doing the
 * one thing its own header says it has stopped doing. Cancelled through the
 * ordinary protocol (``_cancelActiveWarmCacheRuns``), so a run stops with
 * ``cancelled: true`` and ``failed: 0`` rather than being written up as a
 * failed download.
 *
 * Idempotent, like its sibling.
 */
function _forceOffline() {
  if (_probeTimer !== null) {
    clearTimeout(_probeTimer);
    _probeTimer = null;
  }
  if (_networkMode === 'offline-forced') return;
  _networkMode = 'offline-forced';
  _consecutiveTimeouts = 0;
  _probeBackoffIndex = 0;
  _cancelActiveWarmCacheRuns();
  _publishNetworkMode();
}

/**
 * Leave offline mode and resume ordinary bounded network reads.
 *
 * Also idempotent, and always cancels a pending probe — a probe firing after
 * an unlatch would be a wasted request whose failure could re-enter the
 * backoff for a mode nothing is in any more.
 *
 * SNOW-748: the guard stays ``=== 'auto'``, which already clears EITHER
 * offline value. Returning to ``'auto'`` is the one transition both offline
 * modes share, and its only callers are the user and an ``online`` event —
 * both of which mean the same thing whichever mode they interrupt.
 */
function _unlatchOffline() {
  if (_probeTimer !== null) {
    clearTimeout(_probeTimer);
    _probeTimer = null;
  }
  if (_networkMode === 'auto') return;
  _networkMode = 'auto';
  _consecutiveTimeouts = 0;
  _probeBackoffIndex = 0;
  _publishNetworkMode();
}

/**
 * Schedule the next unlatch probe, advancing the backoff.
 *
 * The last entry in ``OFFLINE_PROBE_BACKOFF_MS`` repeats forever rather than
 * growing without bound: a device left latched overnight should still notice
 * signal within five minutes of getting it, not hours later.
 */
function _scheduleProbe() {
  if (_probeTimer !== null) clearTimeout(_probeTimer);
  const index = Math.min(_probeBackoffIndex, OFFLINE_PROBE_BACKOFF_MS.length - 1);
  _probeBackoffIndex += 1;
  _probeTimer = setTimeout(() => {
    _probeTimer = null;
    _probeNetwork();
  }, OFFLINE_PROBE_BACKOFF_MS[index]);
}

/**
 * One bounded request to ``OFFLINE_PROBE_URL``. Unlatches on any response at
 * all — even a 5xx, which still proves a route exists, which is the only
 * question being asked. Reschedules on failure.
 *
 * ``cache: 'no-store'`` so an HTTP-cached 200 can never answer for a route
 * that is no longer there.
 *
 * SNOW-748: both mode comparisons in here stay ``'offline'`` exactly, and that
 * is load-bearing rather than incidental. ``'offline-forced'`` is therefore
 * never probed and never rescheduled — a mode the user chose is theirs to
 * leave. Widening either to ``!== 'auto'`` would reinstate the bug this ticket
 * fixed.
 */
async function _probeNetwork() {
  if (_networkMode !== 'offline' || _probeInFlight) return;
  _probeInFlight = true;
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), OFFLINE_PROBE_BUDGET_MS);
    try {
      await fetch(OFFLINE_PROBE_URL, { cache: 'no-store', signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
    _unlatchOffline();
  } catch (_err) {
    // Still no route. Back off and try again later; the mode is unchanged.
    if (_networkMode === 'offline') _scheduleProbe();
  } finally {
    _probeInFlight = false;
  }
}

/**
 * Tell every client which mode the worker is in, so ``pwa_offline.js`` can
 * render the banner and persist the mode to ``meta:app``.
 *
 * Called on every transition, and once more by ``_hydrateNetworkMode()`` when
 * a restarted worker recovers a forced mode from that row — a page holding a
 * stale toggle has no other way to learn the worker came back with the user's
 * choice intact.
 */
function _publishNetworkMode() {
  const mode = _networkMode;
  // Guarded rather than assumed: a worker with no clients attached is normal
  // (every tab closed while it finishes a background sync), and the mode is
  // worker state that stays correct whether or not anyone is listening.
  if (!self.clients || typeof self.clients.matchAll !== 'function') return;
  self.clients
    .matchAll({ includeUncontrolled: true })
    .then((clients) => {
      for (const client of clients) {
        client.postMessage({ type: 'network-mode', mode });
      }
    })
    .catch(() => {});
}

/**
 * SNOW-748: recover the USER's offline mode from the durable ``meta:app`` row
 * (key ``network.mode``) that ``pwa_offline.js`` writes on every mode change.
 *
 * ``_networkMode`` is module scope, so it lives exactly as long as the worker
 * does — and Chrome terminates an idle worker after about thirty seconds. The
 * restarted worker came back in ``'auto'``, resumed using the network, and
 * fired no event at all: the page's toggle went on reading "offline" while the
 * app was back on the wire, and nothing corrected either until the next page
 * load re-asserted the mode. Recovery cannot depend on a page being there to
 * push it, so the worker reads the row itself.
 *
 * Only ``'offline-forced'`` is restored. A persisted ``'offline'`` is an
 * auto-LATCH — the worker's own inference, drawn from three read-path timeouts
 * against a radio that may well have come back since. Restoring it would strand
 * the user offline on evidence that has already expired, with nothing to clear
 * it but a probe on a backoff that reaches five minutes; declining to restore
 * it costs a re-latch within about nine seconds if the radio really is still
 * dead, which is the cheap direction to be wrong in. A forced mode has no
 * evidence to expire: it is the user's standing instruction, and only the user
 * ends it. (A page that is open still re-asserts a persisted latch on boot, as
 * it always has — that path is unchanged.)
 *
 * Memoised in ``_networkModeHydration`` so a burst of requests on a freshly
 * restarted worker costs one DB read rather than one per request, failures
 * included: a mode that could not be read is a mode this worker will not learn
 * by asking again, and the live page's boot re-assert is the recovery path.
 *
 * Fails safe to ``'auto'`` on any error — a missing ``meta:app`` store (a
 * worker-created DB has only ``queue:mutations``; see ``_openMutationsDb()``),
 * an absent row, a blocked open. Same defensive posture as
 * ``_currentPrincipal()``: the worker must never wedge itself offline over a
 * DB it could not read.
 *
 * @returns {Promise<void>}
 */
function _hydrateNetworkMode() {
  if (_networkModeHydration) return _networkModeHydration;
  _networkModeHydration = (async () => {
    let db;
    try {
      db = await _openMutationsDb();
      const meta = await _idbGetAll(db, 'meta:app');
      const row = meta.find((r) => r.key === NETWORK_MODE_KEY);
      // A live page's ``network-mode`` message is always authoritative over
      // this read, exactly as an explicit ``register-basemap-origins`` message
      // is over ``_hydrateBasemapOrigins``. The row is only as fresh as the
      // last write, so a user who pressed the toggle back to online while this
      // read was in flight must not be forced offline again by it.
      if (_networkModePushed) return;
      // ``_forceOffline`` rather than a bare assignment: recovering the mode
      // has to recover everything the mode means — no probe scheduled, no
      // download running down a connection the user has said not to spend —
      // and it publishes to every client, which is how an open page resyncs a
      // toggle still showing the pre-restart state.
      if (row && row.value === 'offline-forced') _forceOffline();
    } catch (_err) {
      // Unreadable row, missing store, or a transient open failure: stay in
      // ``'auto'``. Read paths behave as they did before this ticket, and the
      // next page load re-asserts the persisted mode.
    } finally {
      if (db) {
        try {
          db.close();
        } catch (_e) {
          // Non-fatal.
        }
      }
    }
  })();
  return _networkModeHydration;
}

/**
 * Apply a ``network-mode`` message from a page, and answer the sender with the
 * mode the worker is actually in.
 *
 * Three sources send one: the user's own control (the account menu's
 * "Offline mode" row since SNOW-748), the mode persisted in ``meta:app`` being
 * re-asserted on boot, and
 * the page's ``online`` listener asking for an immediate probe rather than
 * waiting out the backoff. A page can also send no mode at all, which is a
 * pure query.
 *
 * ``'auto'`` unlatches directly instead of probing first: the caller is either
 * a user who can see they have signal or an ``online`` event, and in both
 * cases the next real read is a better probe than a synthetic one — it is
 * bounded, and if the route is still dead three of them re-latch within about
 * nine seconds.
 *
 * SNOW-748: three modes rather than two. ``'offline-forced'`` is the user's own
 * choice and goes to ``_forceOffline()``, which schedules no probe;
 * ``'offline'`` remains the auto-latch, and is only ever sent back by a page
 * re-asserting a persisted auto-latch on boot.
 *
 * SNOW-748: the reply AWAITS hydration, and that is not a tidiness point. A
 * message is one of the four reasons a worker wakes, and it touches no read
 * path — so a recycled worker asked for its mode answered from the unhydrated
 * ``'auto'`` default while the user's forced mode sat unread on disk. That
 * answer is acted on: ``pwa_offline.js`` treats any ``network-mode``
 * announcement as authoritative and repainted the toggle OFF, which is the
 * original bug arriving down a second route. Answering ``'auto'`` while a
 * forced mode is persisted and unread is the thing this must never do.
 *
 * The transition above still runs SYNCHRONOUSLY, before that await: a mode a
 * page actively pushed outranks a row read still in flight (see
 * ``_hydrateNetworkMode``), and the flag has to be set before anything yields
 * for that precedence to hold.
 *
 * @param {string} mode The mode asked for; anything unrecognised is a query.
 * @param {object|null} source The sending client, when there is one.
 * @returns {Promise<void>}
 */
async function _handleNetworkModeMessage(mode, source) {
  // Set only for the three known modes: an unrecognised value changes nothing
  // here, and must not suppress a hydration that would.
  if (['auto', 'offline', 'offline-forced'].includes(mode)) _networkModePushed = true;
  if (mode === 'offline') _latchOffline();
  if (mode === 'offline-forced') _forceOffline();
  if (mode === 'auto') _unlatchOffline();
  await _hydrateNetworkMode();
  // Answer the sender directly as well as broadcasting, so a page that has just
  // booted learns the mode even when nothing has changed and
  // ``_publishNetworkMode`` therefore had nothing to announce.
  source?.postMessage({ type: 'network-mode', mode: _networkMode });
}

// Started at script evaluation, deliberately, and deliberately NOT awaited
// here — a top-level await would delay the worker's own listeners registering.
//
// SNOW-748: kicking this off lazily from the first read path was not enough,
// and the gap it left reproduced the original bug exactly. A worker is woken
// for four reasons — ``fetch``, ``message``, ``push``, ``sync`` — and only the
// first consults a read path. A recycled worker asked for its mode by a page
// (a bare ``postMessage({type: 'network-mode'})``) therefore answered from the
// unhydrated ``'auto'`` default, and that answer is not inert: ``pwa_offline.js``
// acts on any ``network-mode`` announcement, so the worker's wrong answer
// repainted the user's toggle OFF. Starting the read at evaluation means every
// wake reason finds the memo already in flight, and every consumer awaits the
// same promise.
//
// ``activate`` is NOT the hook for this: it fires on install and update, not on
// the ordinary idle restart — which is the only case that loses the mode.
_hydrateNetworkMode();

/**
 * SNOW-484: stale-while-revalidate for the active basemap's cross-origin
 * requests (vector tiles, sprites, glyphs), against the dedicated
 * ``BASEMAP_CACHE`` rather than the shell's ``CACHE_VERSION`` cache.
 * Serves a cache hit immediately while refreshing it in the background;
 * on a cache miss it awaits the network so the very first fetch of a
 * tile still resolves.
 *
 * Only ``ok``, ``cors`` responses are cached. ``opaque`` responses (from
 * a ``no-cors`` request) are unreadable by design and would poison the
 * cache with content that can't be verified; a non-2xx response would
 * cache an error body as if it were a tile. Every origin here comes from
 * ``_basemapOrigins``, itself sourced from URLs MapLibre already fetches
 * cross-origin in default (``cors``) mode, so ``opaque`` is not expected
 * in practice — the type check is defence-in-depth, not a workaround for
 * an observed MapLibre behaviour.
 *
 * SNOW-521: on a ``BASEMAP_CACHE`` miss, also checks the pinned buckets
 * before falling to the network — a deliberate "Download basemap" run may
 * have already written this exact tile there. This path is READ-ONLY
 * against every pinned bucket: it never writes to or trims any of them
 * (that stays ``_warmCache``'s pinned path's job alone), so ordinary
 * browsing can neither grow nor evict a deliberate download. The
 * background revalidation fetch (``fetchPromise`` below) still only ever
 * writes/trims ``BASEMAP_CACHE``, even when the response actually served
 * came from a pinned bucket — an online revisit of a pinned tile
 * opportunistically promotes it into the passive cache too, which is the
 * existing SNOW-484 behaviour, unchanged.
 *
 * SNOW-586/613/722: the pinned search itself lives in
 * ``_searchPinnedBuckets()`` — every LIVE pinned bucket, in parallel, with
 * a re-enumeration retry on a miss. Extracted so the fetch listener's
 * read-only fallback can reach the same search; behaviour here is
 * unchanged.
 */
async function _basemapStaleWhileRevalidate(request) {
  const cache = await caches.open(BASEMAP_CACHE);
  const cached = await cache.match(request);
  if (cached) {
    // SNOW-742: revalidate AFTER the hit is in hand, not before it is looked
    // up. This call used to start its fetch on the function's first line, so
    // every tile — hits included — launched a request. Offline that meant
    // several hundred hanging revalidations holding connection slots, and the
    // requests that genuinely needed the network queued behind them; the
    // "sporadic" half of the reported failure was that queue draining as
    // sockets timed out one by one. Unawaited, so a hit still returns at cache
    // speed, and skipped entirely when there is nothing to revalidate against.
    if (await _shouldUseNetwork()) _revalidateBasemap(request, cache);
    return cached;
  }
  // SNOW-586: BASEMAP_CACHE miss — check every live pinned bucket before
  // falling through to the network.
  const pinnedHit = await _searchPinnedBuckets(request);
  if (pinnedHit) {
    if (await _shouldUseNetwork()) _revalidateBasemap(request, cache);
    return pinnedHit;
  }
  // SNOW-742: latched offline — every cache partition has missed and there is
  // no route to try, so answer now rather than burning a budget proving it.
  if (!(await _shouldUseNetwork())) return _synthesizedGatewayTimeout();
  const fetchPromise = _boundedFetch(request, BASEMAP_FETCH_BUDGET_MS)
    .then(async (response) => {
      if (response && response.ok && response.type === 'cors') {
        await cache.put(request, response.clone()).catch(() => {});
        // SNOW-614: batched — this used to be a full keys() walk per tile.
        await _trimBasemapCacheEvery(cache);
      }
      return response;
    })
    .catch(() => null);
  // Cache miss (both partitions): fall through to the network. If that
  // also fails (e.g. offline and never previously cached), this
  // deliberately does NOT throw — the caller (_guardedRespond via the
  // fetch handler) still needs a Response, and a 504 here is more
  // informative than an unhandled rejection reaching the page as a raw
  // network error.
  const network = await fetchPromise;
  if (network) return network;
  return _synthesizedGatewayTimeout();
}

// ---------------------------------------------------------------------------
// Navigation principal partitioning (C1, docs/code-reviews/2026-08-03-js-review.md)
// ---------------------------------------------------------------------------
//
// ``_networkFirst`` used to write every 2xx same-origin navigation into the
// shell cache with nothing recording who it was rendered for. Sign-out
// purges neither the cache nor the session's trace in it, so the next
// offline navigation to the ``/account/`` hub replayed the previous user's
// rendered page — their email address included. ``Vary: Cookie`` cannot
// help: ``Cookie`` is a forbidden header name, invisible to the Cache
// API's Vary comparison, so the match succeeds whoever is signed in.
//
// Two independent guards, in the order they apply:
//
//   1. A response declaring ``Cache-Control: no-store`` is never written.
//      Cache Storage is not the HTTP cache and ``cache.put`` ignores the
//      header on its own, so this is an explicit check. It is what honours
//      a view that has opted out entirely — ``change_email_view``'s
//      ``@never_cache`` (apps/accounts/views.py), and
//      ``AdminSite.admin_view``, which already applies ``never_cache`` to
//      every Django admin view and which templates/admin/base_site.html
//      registers this same worker on. Note that ``manage_view`` is
//      deliberately NOT in that set: the offline favourites roster reads
//      it out of this cache, so it relies on guard 2 instead.
//
//   2. Every cached navigation carries an ``X-SW-Principal`` header naming
//      the account its HTML was rendered for, and the offline read serves
//      an entry only when that stamp equals the principal signed in now.
//      This is the primary mechanism, not the backstop — guard 1 only
//      covers pages nothing needs offline.
//      Mirrors SNOW-493's partitioning of the overlay cache
//      (static/js/map_overlay_offline_cache.js) and SNOW-462's mutation-row
//      principal guard (``_selfDrainMutations`` below).
//
// The stamp is read out of the response's own
// ``<meta name="pwa-user-id">`` (public/base.html, populated by
// apps/accounts/context_processors.py) rather than out of IndexedDB. A
// worker has no ``window.pwaDb``, and the durable ``mutations.principal``
// row lags by one page load — it is written by the page's own
// ``_reconcilePrincipal()`` AFTER the navigation that carried the HTML has
// already been cached, so stamping from it would stamp a signed-in page
// with the previous session's principal. The response body is the only
// source that is right at write time.
//
// The read side has no such choice: ``mutations.principal`` is the one
// signal a worker has for "who is signed in now", and by read time it has
// settled. An entry whose stamp is missing (written before this fix, or a
// page with no meta tag at all, e.g. Django admin) never matches, so it is
// never served — the same fail-closed direction SNOW-493 takes for a
// principal-less favourites row.

const PRINCIPAL_HEADER = 'X-SW-Principal';

// Stamped for a response rendered for an anonymous visitor — the meta tag
// is present with an empty ``content``. A distinct literal rather than the
// empty string so the stamp is legible in devtools and can't be confused
// with an absent header.
const PRINCIPAL_ANONYMOUS = 'anonymous';

// Stamped when the response carries no ``pwa-user-id`` meta tag at all, so
// the worker cannot tell whose page it is. Never equal to any principal
// the read side can produce, which is what keeps such an entry out of the
// offline path.
const PRINCIPAL_UNKNOWN = 'unknown';

// Matches base.html's ``<meta name="pwa-user-id" content="…">``. Both
// quote styles are accepted so a djangofmt reflow of the attribute cannot
// silently turn every navigation into PRINCIPAL_UNKNOWN.
const PWA_USER_ID_META = /<meta\s+name=["']pwa-user-id["']\s+content=["']([^"']*)["']/i;

/**
 * True when ``response`` declares ``Cache-Control: no-store``. Uses the
 * same token-split/trim/includes match as ``shouldPersist``'s
 * ``immutable`` check rather than a bare substring test, so a directive
 * that merely contains the word (``no-store-foo``) cannot match.
 *
 * @param {Response} response
 * @returns {boolean}
 */
function _isNoStore(response) {
  const header = (response.headers && response.headers.get('Cache-Control')) || '';
  return header
    .toLowerCase()
    .split(',')
    .map((token) => token.trim())
    .includes('no-store');
}

/**
 * The principal a navigation response was rendered for, read out of its
 * own ``pwa-user-id`` meta tag.
 *
 * @param {string} html
 * @returns {string} An account uuid, ``PRINCIPAL_ANONYMOUS``, or
 *   ``PRINCIPAL_UNKNOWN`` when the tag is absent.
 */
function _principalFromHtml(html) {
  const match = PWA_USER_ID_META.exec(html || '');
  if (!match) return PRINCIPAL_UNKNOWN;
  return match[1] === '' ? PRINCIPAL_ANONYMOUS : match[1];
}

/**
 * The principal signed in right now, read from the durable ``meta:app``
 * row (key ``mutations.principal``) the page maintains in
 * ``static/js/mutation_queue.js``'s ``_reconcilePrincipal()``. Same row
 * ``_selfDrainMutations()`` reads for its own SNOW-462 guard.
 *
 * Falls back to ``PRINCIPAL_ANONYMOUS`` whenever the row cannot be read —
 * a fresh worker-created DB has no ``meta:app`` store, and a browser with
 * IndexedDB unavailable has no row at all. That keeps the ordinary
 * anonymous offline experience intact (public pages stamp
 * ``PRINCIPAL_ANONYMOUS`` too, so they still match) while refusing to
 * serve any page rendered for a named account.
 *
 * @returns {Promise<string>}
 */
async function _currentPrincipal() {
  let db;
  try {
    db = await _openMutationsDb();
    const meta = await _idbGetAll(db, 'meta:app');
    const row = meta.find((r) => r.key === 'mutations.principal');
    if (!row || row.value === null || row.value === undefined || row.value === '') {
      return PRINCIPAL_ANONYMOUS;
    }
    return String(row.value);
  } catch (_err) {
    return PRINCIPAL_ANONYMOUS;
  } finally {
    if (db) {
      try {
        db.close();
      } catch (_e) {
        // Non-fatal.
      }
    }
  }
}

/**
 * Rebuild ``response`` with its ``X-SW-Principal`` stamp, ready for
 * ``cache.put``. Cached ``Response`` objects have immutable headers, so
 * the only way to add one is to build a new ``Response`` around the same
 * body/status/headers — the same construction ``_stampCacheHit()`` uses.
 *
 * @param {Response} response
 * @param {string} principal
 * @returns {Response}
 */
function _stampPrincipal(response, principal) {
  const headers = new Headers(response.headers);
  headers.set(PRINCIPAL_HEADER, principal);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

/**
 * True when a cached navigation may be served to ``current``. An entry
 * with no stamp, or one stamped ``PRINCIPAL_UNKNOWN``, never matches.
 *
 * @param {Response} cached
 * @param {string} current
 * @returns {boolean}
 */
function _principalMatches(cached, current) {
  const stamped = cached.headers.get(PRINCIPAL_HEADER);
  if (!stamped || stamped === PRINCIPAL_UNKNOWN) return false;
  return stamped === current;
}

/**
 * True when ``response`` is page HTML rather than data (SNOW-624).
 *
 * The discriminator for "would `_networkFirst` have cached this, and so
 * does the offline read expect a principal stamp on it?". Matches on the
 * media type alone, ignoring any ``; charset=…`` parameter.
 *
 * @param {Response} response
 * @returns {boolean}
 */
function _isHtmlResponse(response) {
  const type = (response.headers && response.headers.get('Content-Type')) || '';
  return type.split(';')[0].trim().toLowerCase() === 'text/html';
}

/**
 * Stamp and write one navigation into the shell cache, off the response
 * path. Reading the principal out of the body means buffering it, which
 * must not delay the response the page is waiting on — so this is
 * deliberately not awaited, matching the fire-and-forget
 * ``cache.put(...).catch(() => {})`` the caller already used.
 *
 * ``forCache`` and ``forSniff`` are two clones of the same response: one
 * body for the cache write, one to read the meta tag from. Both must be
 * cloned by the caller before either is consumed.
 *
 * @param {Cache} cache
 * @param {Request} request
 * @param {Response} forCache
 * @param {Response} forSniff
 */
function _cacheNavigation(cache, request, forCache, forSniff) {
  forSniff
    .text()
    .then((html) => cache.put(request, _stampPrincipal(forCache, _principalFromHtml(html))))
    .catch(() => {});
}

/**
 * Network-first: try the network, fall back to cache on failure, then
 * to the offline fallback page if the request is a navigation. Use for
 * HTML navigations so the user sees fresh data normally, the last-seen
 * page when offline-but-cached, and a branded offline page when neither
 * network nor cache has the URL (a page they've never visited before).
 *
 * The offline-fallback branch does a second cache lookup with
 * ``ignoreSearch: true`` before giving up. Rationale: the map page adds
 * ``?d=YYYY-MM-DD`` client-side via ``history.replaceState`` while the
 * user scrubs the timeline (see ``static/js/map.js``), so those URLs are
 * never fetched from the server and never cached. An offline reload of
 * ``/?d=2026-01-23`` would otherwise miss the exact-URL cache lookup and
 * fall straight to ``offline.html`` even though the ``/`` shell HTML
 * (which is byte-identical for every ``?d`` value — the date is read
 * back off ``location.search`` by page-level JS) has been cached since
 * the first visit. Matching with ``ignoreSearch: true`` returns that
 * cached shell, and the page reinitialises to the requested date.
 *
 * C1 (docs/code-reviews/2026-08-03-js-review.md): the write skips a
 * ``no-store`` response and stamps everything else with the principal its
 * HTML was rendered for; the two request-matched reads serve an entry only
 * to that same principal. See the section comment above for the full
 * argument. ``OFFLINE_FALLBACK`` is deliberately outside the check — it is
 * precached by the ``install`` handler, carries no account identity, and
 * is the branded page whose whole purpose is to be shown when nothing else
 * can be.
 */
/**
 * The cached answer for a navigation the network could not serve, or ``null``
 * when neither the cache nor the offline fallback page has one.
 *
 * SNOW-742 lifted this out of ``_networkFirst``'s ``catch`` block so the
 * latched path can reach it without a network attempt first. The logic is
 * unchanged from C1 (docs/code-reviews/2026-08-03-js-review.md): both
 * request-matched reads serve an entry only to the principal its HTML was
 * rendered for, while ``OFFLINE_FALLBACK`` is deliberately exempt — it is
 * precached, carries no account identity, and exists to be shown when nothing
 * else can be.
 *
 * Returning ``null`` rather than throwing keeps the decision with the caller:
 * the network branch rethrows the original error (so a genuine failure still
 * surfaces with its own cause), while the latched branch answers with a
 * synthesized 504.
 *
 * @param {Request} request
 * @param {Cache} cache The open shell cache.
 * @returns {Promise<Response|null>}
 */
async function _networkFirstFallback(request, cache) {
  const current = await _currentPrincipal();
  const cached = await cache.match(request);
  if (cached && _principalMatches(cached, current)) return _stampCacheHit(cached);
  if (request.mode === 'navigate' || request.destination === 'document') {
    const searchless = await cache.match(request, { ignoreSearch: true });
    if (searchless && _principalMatches(searchless, current)) {
      return _stampCacheHit(searchless);
    }
    const fallback = await cache.match(OFFLINE_FALLBACK);
    if (fallback) return _stampCacheHit(fallback);
  }
  return null;
}

async function _networkFirst(request) {
  const cache = await caches.open(CACHE_VERSION);
  // SNOW-742: latched offline — go straight to the cache branch rather than
  // spending a 5s budget re-proving there is no route. This is the difference
  // between a day in the backcountry costing five seconds per navigation and
  // costing nothing.
  if (!(await _shouldUseNetwork())) {
    const offline = await _networkFirstFallback(request, cache);
    if (offline) return offline;
    return _synthesizedGatewayTimeout();
  }
  try {
    const response = await _boundedFetch(request, NAVIGATION_FETCH_BUDGET_MS);
    if (response && response.ok && response.type === 'basic' && !_isNoStore(response)) {
      _cacheNavigation(cache, request, response.clone(), response.clone());
    }
    return response;
  } catch (err) {
    // SNOW-742: a budget expiry rejects exactly as a refusal does, so a hang
    // now reaches this branch — the whole point of bounding the fetch. Before,
    // the network held the request open for the OS TCP timeout and this code
    // simply never ran, which is why the app showed a blank page for minutes
    // with the cached shell sitting on disk the entire time.
    const fallback = await _networkFirstFallback(request, cache);
    if (fallback) return fallback;
    throw err;
  }
}

/**
 * Wrap a strategy promise so an unexpected ``undefined`` / non-Response
 * resolution — which would otherwise surface to the page as a raw
 * network error with no diagnostic trail — is caught, reported via the
 * SNOW-384 telemetry bridge, and replaced with a same-request network
 * fetch so the navigation/asset load still has a chance to succeed.
 *
 * Both ``_staleWhileRevalidate`` and ``_networkFirst`` always resolve to
 * a ``Response`` today; this is a defensive guard against a future
 * regression in either function, not a documented current failure mode.
 *
 * @param {Promise<Response>} responsePromise
 * @param {Request} request
 * @param {string} [clientId]
 * @returns {Promise<Response>}
 */
async function _guardedRespond(responsePromise, request, clientId) {
  const response = await responsePromise;
  if (!response || typeof response.status !== 'number') {
    // SNOW-384: pwa.sw.fetch_undefined is a critical event — sendBeacon
    // fires immediately once sw_register.js forwards it.
    _postTelemetry(
      'pwa.sw.fetch_undefined',
      { url: request.url, mode: request.mode },
      clientId,
    );
    return fetch(request);
  }
  return response;
}

// SNOW-487: same-origin (and non-GET) classification is fully
// synchronous — ``_classifySync()`` — so those branches keep calling
// (or deliberately not calling) ``event.respondWith()`` synchronously,
// exactly as before. Cross-origin GET requests are the one case that
// may need the async ``_basemapOrigins`` rehydration, and a decision
// about whether to call ``event.respondWith()`` at all must be made
// before any ``await`` — so that branch ALWAYS calls it, with a promise
// that resolves to a plain ``fetch(event.request)`` when the request
// turns out not to be a registered basemap origin. For the CORS GETs the
// app actually issues cross-origin (vector tiles, sprites, glyphs) that
// passthrough is behaviourally equivalent to never having intercepted the
// GET, which preserves the "unknown cross-origin stays network-only"
// contract. (Non-GET cross-origin requests never reach here — they exit
// synchronously at the ``'network'`` branch above.)
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  const sync = _classifySync(request, url);

  if (sync === 'static') {
    event.respondWith(_guardedRespond(_staleWhileRevalidate(request), request, event.clientId));
    return;
  }
  if (sync === 'navigate') {
    event.respondWith(_guardedRespond(_networkFirst(request), request, event.clientId));
    return;
  }
  if (sync === 'network') {
    // Network-only: a non-GET request (any origin) or a same-origin GET
    // that is neither a static-shell asset nor a navigation. No
    // event.respondWith() call means the request is never seen by the
    // SW's caching layer — the browser handles it natively.
    return;
  }

  // sync === null: cross-origin GET — defer to the async cross-origin
  // classifier, which lazily rehydrates _basemapOrigins (SNOW-487) before
  // deciding. Pass the already-parsed url so we don't re-run _classifySync.
  event.respondWith(
    (async () => {
      const strategy = await _classifyCrossOriginGet(url);
      if (strategy === 'basemap') {
        return _guardedRespond(_basemapStaleWhileRevalidate(request), request, event.clientId);
      }
      // SNOW-722: unrecognised cross-origin GET. Before going to the
      // network, probe the basemap caches READ-ONLY — the allowlist this
      // classification rests on is in-memory and does not survive an idle
      // worker being terminated, so "not a basemap origin" can simply mean
      // "this worker restarted and could not rehydrate". Nothing is written
      // on this path: the allowlist still governs caching, and a genuinely
      // unrelated origin misses and falls through unchanged.
      const cached = await _readOnlyBasemapCacheProbe(request);
      if (cached) return cached;
      return fetch(request);
    })(),
  );
});

// ---------------------------------------------------------------------------
// Message — version probe (dev convenience)
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
  if (event.data === 'version') {
    event.source?.postMessage({ type: 'version', version: CACHE_VERSION });
  }
  // SNOW-742: the page setting the network mode, or asking what it is. Every
  // rule about which mode goes where — and why the reply waits for the
  // persisted row (SNOW-748) — lives in ``_handleNetworkModeMessage``.
  if (event.data && event.data.type === 'network-mode') {
    const answered = _handleNetworkModeMessage(event.data.mode, event.source);
    // The reply now lands after an await, so it needs the same keep-alive the
    // warm-cache handler below takes. Guarded because not every dispatcher is
    // an ExtendableMessageEvent.
    if (typeof event.waitUntil === 'function') event.waitUntil(answered);
  }
  // The page sends this when the user clicks "Reload" on the update
  // banner. Activating the waiting worker triggers ``activate`` (and its
  // ``clients.claim()``), which hands control to this new shell.
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  // SNOW-613: the page has deleted one or more pinned basemap buckets —
  // `evictBasemapAreas` in static/js/map.js posts this after every
  // eviction: a confirmed budget eviction, or a "Remove" from the
  // manage-downloads sheet. (SNOW-635: a custom-area download used to also
  // trigger this by replacing its own single bucket at a new bbox — that
  // scenario no longer exists, since every confirmed custom-area download
  // now mints a fresh id and bucket rather than replacing one.)
  //
  // The worker cannot see those deletions any other way, and a stale name
  // in its memoised list is not merely a slow path: it would be handed to
  // `caches.open`, which CREATES an empty cache under that name —
  // resurrecting the bucket the user just deleted, and one that
  // `pinnedBucketAreaIds()` (SNOW-612) would then report back to them as
  // an orphaned download.
  if (event.data && event.data.type === 'pinned-buckets-changed') {
    _invalidatePinnedCacheNames();
  }
  // SNOW-484: map.js posts this once BASEMAP_OPTIONS is parsed from the
  // basemap picker's data-basemap-url attributes (and again on
  // controllerchange, so a freshly-activated worker learns it promptly).
  // Replaces rather than merges _basemapOrigins — see its declaration
  // above for why. Origins for every basemap in the picker are included,
  // not just the active one, so switching basemap mid-session is covered.
  if (
    event.data &&
    event.data.type === 'register-basemap-origins' &&
    Array.isArray(event.data.origins)
  ) {
    _basemapOrigins = new Set(event.data.origins);
    // SNOW-487: an explicit registration is always authoritative — clear
    // any earlier memoised hydration read so a later cross-origin fetch
    // doesn't await a stale (already-resolved, pre-registration) promise
    // instead of using the Set we just replaced.
    _basemapHydration = null;
    // SNOW-722: and the retry budget with it — a live page has just proved
    // the allowlist is knowable, so a later idle-termination starts fresh.
    _basemapHydrationFailures = 0;
  }
  // SNOW-585: static/js/pwa_dev_shell_toggle.js posts this when the
  // dev-only "restore shell cache" checkbox on /_sw-version/ changes.
  // Mirrors 'register-basemap-origins' exactly: replaces the in-memory
  // value and clears the memoised hydration read, so the toggle takes
  // effect on the very next fetch rather than waiting for a worker
  // restart. Ignored (but harmless) if DEV_SHELL_BYPASS is false.
  if (event.data && event.data.type === 'dev-shell-cache') {
    _devShellCacheOptIn = !!event.data.enabled;
    _devShellCacheHydration = null;
  }
  // SNOW-492: "Download basemap" — map.js posts this with the current
  // view's URL list (see its docstring for how that list is assembled).
  // Runs inside event.waitUntil (ExtendableMessageEvent supports it, same
  // as 'fetch'/'sync') so the fetch burst isn't cut short if the worker
  // would otherwise be judged idle mid-flight; posts the completion
  // summary back to the requesting client so map.js can show a toast.
  //
  // SNOW-493 finding 9: echoes back whatever ``requestId`` the page sent
  // (undefined if an older page script didn't send one) so
  // ``sw_register.js`` can tell a genuine reply for THIS call apart from a
  // stale reply for a call it already gave up on (timed out). This worker
  // has no other way to correlate — it's the only side that can echo an id
  // it never generated itself.
  //
  // SNOW-521: ``event.data.pinned`` (set when map.js's download controls
  // call ``pwaWarmCache(urls, {pinned: true, ...})``) is forwarded
  // straight into ``_warmCache`` so its basemap-origin writes land in a
  // pinned bucket rather than ``BASEMAP_CACHE``. ``onProgress`` posts a
  // ``warm-cache-progress`` message per throttled step so
  // ``sw_register.js`` can drive a live n/total readout on the page —
  // distinct from the final ``warm-cache-done`` reply, which is unchanged.
  //
  // SNOW-586: ``event.data.areaId`` (the id of the area being downloaded
  // — see ``basemap_download_core.js``'s ``areaIdForRegion`` for a region,
  // or (SNOW-635) ``generateCustomAreaId``/``CUSTOM_AREA_ID`` for a custom
  // area) selects WHICH pinned bucket a pinned run writes into. ``pinned``
  // with no ``areaId`` is a programming error, not a
  // runtime condition to route around silently: writing to some fallback
  // shared bucket would resurrect exactly the perforation bug this ticket
  // exists to fix, so the run is refused outright with the same shape
  // ``_warmCache`` itself would return for a total failure — ``_warmCache``
  // is never even called.
  if (
    event.data &&
    event.data.type === 'warm-cache' &&
    Array.isArray(event.data.urls)
  ) {
    const requestId = event.data.requestId;
    const pinned = !!event.data.pinned;
    const areaId = event.data.areaId;
    if (pinned && !areaId) {
      // SNOW-632: no run was ever dispatched under this requestId, but a
      // cancel for it can still have arrived (or arrive later) — clear it
      // from the same set the dispatched path settles below, so a stray
      // entry doesn't sit there for the rest of the worker's life.
      _clearWarmCacheCancelled(requestId);
      event.source?.postMessage({
        type: 'warm-cache-done',
        ok: 0,
        failed: event.data.urls.length,
        reason: 'other',
        bytes: 0,
        requestId,
      });
    } else {
      // SNOW-632: polled once per pool worker inside _warmCache — see its
      // docstring and the 'warm-cache-cancel' handler below, which is the
      // only thing that ever adds to this set.
      const shouldCancel = () => _warmCacheCancelledIds.has(requestId);
      // SNOW-748: and the other half of that protocol — this run is now
      // something there is to cancel, which is what ``_forceOffline`` needs to
      // know when the user switches on "Offline mode" mid-download.
      _markWarmCacheActive(requestId);
      const onProgress = (done, total, settled, bytes) => {
        event.source?.postMessage({
          type: 'warm-cache-progress',
          done,
          total,
          // Tile-grid rework: indices into the posted URL list that succeeded since
          // the last message — the page turns them back into tiles.
          settled,
          // SNOW-632: the run's on-disk bytes so far, so the page can show
          // a live MB readout without waiting for the final summary.
          bytes,
          requestId,
        });
      };
      const warm = _warmCache(event.data.urls, {
        pinned,
        areaId,
        onProgress,
        shouldCancel,
        // SNOW-742: the active style's glyph URL prefix, so a pinned run can
        // promote the labels this area needs out of the trimmable passive
        // cache and into its own bucket. Absent on a non-pinned run, and on
        // an older page still serving a cached shell — ``_warmCache`` skips
        // the promotion entirely when it is missing.
        glyphPrefix: event.data.glyphPrefix,
      }).then((result) => {
        // SNOW-632: this requestId's run has settled one way or another —
        // drop it from the cancelled set now rather than waiting for the
        // cap in _markWarmCacheCancelled to age it out.
        _clearWarmCacheCancelled(requestId);
        // SNOW-748: and out of the in-flight set, so a later _forceOffline
        // does not mark a finished run cancelled (which would leave its id
        // sitting in the cancelled set with nothing to clear it).
        _clearWarmCacheActive(requestId);
        event.source?.postMessage({
          type: 'warm-cache-done',
          ok: result.ok,
          failed: result.failed,
          // SNOW-568: the run's most actionable failure classification, or
          // null when nothing failed — map.js turns this into the message
          // the user actually reads.
          reason: result.reason,
          // SNOW-586: the run's total on-disk bytes, so the page can
          // record it against the area's standing budget entry.
          bytes: result.bytes,
          // SNOW-632: true when the run stopped early on a cancel request
          // rather than running every URL to completion — see
          // _warmCache's docstring for what this does and doesn't
          // guarantee.
          cancelled: result.cancelled,
          requestId,
        });
      });
      if (typeof event.waitUntil === 'function') event.waitUntil(warm);
    }
  }
  // SNOW-632: the page's Cancel control — static/js/sw_register.js's
  // cancelWarmCache() posts this for the in-flight warm-cache call's own
  // requestId. Only records the request; the run itself (if one is even
  // still in flight for this id — see the cleanup notes above) notices it
  // the next time its pool polls shouldCancel. Body lives in
  // _handleWarmCacheCancelMessage so tests/js/test_sw.js can call the real
  // glue directly — the message-event sandbox never invokes an
  // addEventListener callback, so anything left inline here would be
  // untested.
  if (event.data && event.data.type === 'warm-cache-cancel') {
    _handleWarmCacheCancelMessage(event.data);
  }
});

// ---------------------------------------------------------------------------
// Background Sync (SNOW-376) — mutation-queue replay
// ---------------------------------------------------------------------------

/**
 * Open (or lazily create) the shared PWA IndexedDB database directly —
 * a worker has no ``window.pwaDb``. Mirrors ``static/js/db.js``'s
 * ``DB_NAME`` exactly so it opens the SAME database a page already
 * created. It opens WITHOUT a version number so it attaches to whatever
 * schema version the page most recently migrated to (db.js owns
 * ``DB_VERSION`` — currently 4 — and bumps it as stores are added; a
 * hardcoded version here would throw ``VersionError`` the moment db.js
 * moved ahead). The ``onupgradeneeded`` branch below only fires in the
 * (rare) case a Background Sync fires before any page has ever opened the
 * DB, creating a fresh v1 DB with ONLY the one store this worker needs,
 * mirroring ``db.js::STORES['queue:mutations']``; the next page load
 * upgrades it to the full schema. The page-side ``db.js`` remains the
 * single source of truth for the full schema; see
 * docs/indexeddb-scaffolding.md.
 *
 * @returns {Promise<IDBDatabase>}
 */
function _openMutationsDb() {
  return new Promise((resolve, reject) => {
    let req;
    try {
      req = indexedDB.open('snowdesk-pwa-v1');
    } catch (err) {
      reject(err);
      return;
    }
    req.onupgradeneeded = (evt) => {
      const db = evt.target.result;
      if (!db.objectStoreNames.contains('queue:mutations')) {
        db.createObjectStore('queue:mutations', { keyPath: 'id', autoIncrement: true });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('idb_open_failed'));
    req.onblocked = () => reject(new Error('idb_blocked'));
  });
}

function _idbGetAll(db, storeName) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readonly');
    const req = tx.objectStore(storeName).getAll();
    req.onsuccess = () => resolve(req.result || []);
    req.onerror = () => reject(req.error || new Error('getAll failed'));
  });
}

function _idbPut(db, storeName, value) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const req = tx.objectStore(storeName).put(value);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new Error('put failed'));
  });
}

function _idbDelete(db, storeName, key) {
  return new Promise((resolve, reject) => {
    const tx = db.transaction(storeName, 'readwrite');
    const req = tx.objectStore(storeName).delete(key);
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error || new Error('delete failed'));
  });
}

/**
 * One-pass replay of every eligible ``queue:mutations`` row, run directly
 * against IndexedDB (no open tab to delegate to — see the tag-handler
 * below for the has-a-tab fast path). Uses the SAME classification /
 * backoff rules as the page (``self.pwaMutationQueueCore``), and sends
 * the identical ``Idempotency-Key`` header per row.
 *
 * Throws if any row still needs a further retry once this pass completes,
 * so the caller's ``event.waitUntil`` rejects and the browser reschedules
 * the Background Sync per its own backoff — per spec, a fulfilled
 * ``waitUntil`` tells the browser the sync succeeded and it stops
 * retrying, which would silently strand a row still in backoff.
 *
 * @returns {Promise<void>}
 */
// Hand-rolled stand-ins for ``mutation_queue_core.js``, used only when the
// ``importScripts`` at startup failed — see the try/catch there. Kept so
// this path stays self-contained rather than a hard dependency on that
// call succeeding.
//
// SNOW-617: ``nextRowState`` is the transition the drain used to spell out
// inline, here and again in ``mutation_queue.js``. Both now call core's;
// this copy exists for the no-core case alone, and
// ``tests/js/test_sw.js`` runs it and core's over one shared input table
// so a future change to either fails there — the same treatment D3 gave
// ``worseReason`` after it drifted.
const _INLINE_MUTATION_QUEUE_CORE = {
  MAX_ATTEMPTS: 20,
  backoffDelayMs: (attempts) => Math.min(Math.pow(2, attempts), 300) * 1000,
  classifyStatus: (status) => {
    if (status >= 200 && status < 300) return 'success';
    if (status === 408 || status === 429) return 'retry';
    if (status >= 500) return 'retry';
    if (status >= 400) return 'permanent';
    return 'retry';
  },
  isRowEligible: (row, now) =>
    !!row && row.status !== 'failed' && (row.next_attempt_at || 0) <= now,
  nextRowState: (row, outcome, now) => {
    if (outcome === 'success') return { action: 'delete' };
    const attempts = (row && row.attempts ? row.attempts : 0) + 1;
    if (outcome === 'permanent') {
      return {
        action: 'fail',
        row: Object.assign({}, row, { attempts, status: 'failed' }),
        reason: 'permanent_4xx',
      };
    }
    if (attempts >= _INLINE_MUTATION_QUEUE_CORE.MAX_ATTEMPTS) {
      return {
        action: 'fail',
        row: Object.assign({}, row, { attempts, status: 'failed' }),
        reason: 'max_attempts',
      };
    }
    return {
      action: 'reschedule',
      row: Object.assign({}, row, {
        attempts,
        status: 'retry-scheduled',
        next_attempt_at: now + _INLINE_MUTATION_QUEUE_CORE.backoffDelayMs(attempts),
      }),
    };
  },
};

async function _selfDrainMutations() {
  // Fall back to a hand-rolled version of the shared helpers if
  // importScripts failed at startup — keeps this path self-contained
  // rather than a hard dependency on the earlier try/catch succeeding.
  const core = self.pwaMutationQueueCore || _INLINE_MUTATION_QUEUE_CORE;

  const db = await _openMutationsDb();
  let rows;
  try {
    rows = await _idbGetAll(db, 'queue:mutations');
  } catch (_e) {
    // Store may not exist yet on a brand-new DB — nothing to drain.
    rows = [];
  }

  // SNOW-462: best-effort tab-closed principal guard — see the header
  // comment's "Account-change guard" section. ``meta:app`` may not exist
  // on a worker-created DB (the onupgradeneeded branch above only
  // creates ``queue:mutations``); the try/catch below handles that by
  // leaving ``storedPrincipal`` undefined, which skips the guard entirely
  // rather than discarding every row.
  let storedPrincipal;
  try {
    const meta = await _idbGetAll(db, 'meta:app');
    const row = meta.find((r) => r.key === 'mutations.principal');
    storedPrincipal = row ? row.value : undefined;
  } catch (_e) {
    storedPrincipal = undefined;
  }

  const now = Date.now();
  let successCount = 0;
  let retryableRemaining = false;

  for (const row of rows) {
    // SNOW-462: discard (do not replay) a row stamped for a different
    // principal than the last one the page saw. Skipped entirely when
    // storedPrincipal is undefined (meta:app unavailable) rather than
    // guessing. The delete is wrapped — a rejection here must not
    // propagate to event.waitUntil, which would reschedule the
    // Background Sync and risk an indefinite discard-retry loop; mirrors
    // the page-side _processRow's own guarded delete.
    if (storedPrincipal !== undefined && row.principal !== storedPrincipal) {
      try {
        await _idbDelete(db, 'queue:mutations', row.id);
      } catch (_e) {
        // Non-fatal.
      }
      continue;
    }

    if (!core.isRowEligible(row, now)) {
      if (row.status !== 'failed') retryableRemaining = true;
      continue;
    }

    let outcome;
    try {
      const headers = Object.assign({}, row.headers || {}, {
        'Idempotency-Key': row.idempotency_key,
      });
      const response = await fetch(row.url, {
        method: row.method,
        headers,
        body: row.body != null ? row.body : undefined,
      });
      outcome = core.classifyStatus(response.status);
    } catch (_networkErr) {
      outcome = 'retry';
    }

    // SNOW-617: the transition is core's. This loop used to spell it out
    // by hand, in parallel with `mutation_queue.js`'s own copy — including
    // a `max_attempts` telemetry payload that, unlike the page's, omitted
    // `attempts` entirely.
    const transition = core.nextRowState(row, outcome, now);

    if (transition.action === 'delete') {
      await _idbDelete(db, 'queue:mutations', row.id);
      successCount += 1;
      continue;
    }

    await _idbPut(db, 'queue:mutations', transition.row);

    if (transition.action === 'fail') {
      _postTelemetry('pwa.mutation.failed_permanent', {
        method: transition.row.method,
        url: transition.row.url,
        idempotency_key: transition.row.idempotency_key,
        attempts: transition.row.attempts,
        reason: transition.reason,
      });
      continue;
    }

    retryableRemaining = true;
  }

  try {
    db.close();
  } catch (_e) {
    // Non-fatal.
  }

  if (successCount > 0) {
    _postTelemetry('pwa.mutation.drained', { count: successCount, source: 'background_sync' });
  }

  if (retryableRemaining) {
    throw new Error('mutation_queue_retry_pending');
  }
}

/**
 * Dispatch one Background Sync firing for the mutation-queue tag: prefer
 * delegating to an open tab (cheaper, reuses the page's already-wired
 * ``window.pwaDb`` / ``window.pwaTelemetry``); self-drain directly against
 * IndexedDB only when no tab is open at all.
 *
 * Asymmetric retry-rescheduling by design: the no-tab path below (
 * ``_selfDrainMutations``) throws when retryable rows remain, so
 * ``event.waitUntil`` rejects and the browser reschedules the sync itself.
 * The has-open-tabs path here does NOT do that — it resolves as soon as
 * the message is posted, before the page's own ``drain()`` has even run,
 * so there's nothing yet to inspect for "does this need a Background Sync
 * reschedule?". If that drain leaves retryable rows (still-backing-off
 * 5xx/429s), the page's OWN lifecycle triggers — the 30s periodic timer,
 * ``visibilitychange``, and the next ``online`` event — are what carry it
 * forward, not another Background Sync firing. That's an acceptable gap
 * (a tab is open, so those triggers are live), not an oversight.
 *
 * @returns {Promise<void>}
 */
async function _handleMutationSync() {
  const clientsList = await self.clients.matchAll({
    includeUncontrolled: true,
    type: 'window',
  });
  if (clientsList.length > 0) {
    clientsList.forEach((client) => {
      try {
        client.postMessage({ type: 'drain-mutations' });
      } catch (_e) {
        // A torn-down client — not worth retrying for this one message.
      }
    });
    return;
  }
  await _selfDrainMutations();
}

self.addEventListener('sync', (event) => {
  const tag = self.pwaMutationQueueCore ? self.pwaMutationQueueCore.SYNC_TAG : 'mutation-queue';
  if (event.tag !== tag) return;
  event.waitUntil(_handleMutationSync());
});

// ---------------------------------------------------------------------------
// Web Push (spike) — receive + click
// ---------------------------------------------------------------------------
//
// The 'push' event fires when the OS push service delivers a payload to
// this SW. We parse the JSON body the server sent (title, body, url) and
// display a notification. On click we focus an existing tab on the
// payload URL if one is already open, otherwise open a new window.

self.addEventListener('push', (event) => {
  let payload = { title: 'Snowdesk', body: '', url: '/' };
  if (event.data) {
    try {
      payload = { ...payload, ...event.data.json() };
    } catch (_err) {
      payload.body = event.data.text();
    }
  }
  // SNOW-384: pwa.push.received is the client half of the push funnel
  // (server half: pwa.push.sent / pwa.push.gone_410 in push_service.py).
  // One 'push' event = one occurrence, so this fires exactly once per
  // delivered message — naturally idempotent.
  _postTelemetry('pwa.push.received', { url: payload.url });
  const promise = self.registration
    .showNotification(payload.title, {
      body: payload.body,
      icon: '/static/icons/pwa/icon-192.png',
      badge: '/static/icons/pwa/icon-192.png',
      data: { url: payload.url },
      tag: 'snowdesk-push',
    })
    .then(() => {
      // SNOW-384: emitted only after showNotification's promise
      // resolves, so a suppressed/failed notification (denied
      // permission, browser quirk) does not falsely report "shown".
      _postTelemetry('pwa.push.shown', { url: payload.url });
    });
  event.waitUntil(promise);
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = event.notification.data?.url || '/';
  // SNOW-384: one click = one occurrence. Emitted unconditionally on
  // click, ahead of the focus/openWindow race below, so the signal
  // isn't lost if the focus/navigate branch throws.
  _postTelemetry('pwa.push.opened', { url: target });
  const promise = (async () => {
    const all = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    for (const client of all) {
      if (new URL(client.url).pathname === target && 'focus' in client) {
        return client.focus();
      }
    }
    if (self.clients.openWindow) {
      return self.clients.openWindow(target);
    }
    return null;
  })();
  event.waitUntil(promise);
});
