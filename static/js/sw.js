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
 * Bump ``CACHE_VERSION`` whenever the shell changes — a new version
 * string changes the bytes of this script, which is what makes the
 * browser detect the update and surface the banner. On ``activate``,
 * every cache key not matching the current version is deleted so old SW
 * deploys leave nothing behind. The version is also surfaced via a
 * ``message`` handler so devtools can confirm which SW version is in
 * control.
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

// SNOW-475: v13 — new static/js/place_picker.js, favourites.js/report.js
// rewritten to use it, and _map_embed.html's markup gained #map-place-pin.
// (v12 was taken by SNOW-462/SNOW-472 on main.)
// SNOW-445: v14 — map.js click-dispatch rewrite (marker exclusion zone,
// always-on-top pins, cluster-tap zoom fix).
// SNOW-474: v15 — persistent close (×) controls added to the favourites
// and report map sheets: new templates/includes/_sheet_header.html partial,
// plus Esc/click-outside dismissal wired into favourites.js/report.js.
// SNOW-479: v16 — favourite creation routed through the mutation queue
// (favourites.js submit interceptor, optimistic pending pin in map.js,
// drain re-dispatch + failed-permanent event in mutation_queue.js).
// SNOW-445: v17 — design-fixes batch: zoom pill removed (map.js/_map_embed.html),
// nav Help→footer + full-width wordmark + sync-badge display fix (nav.html),
// mutation_queue.js inline-flex toggle, off-season full-width bar (_map_embed.html).
// SNOW-477: v18 — report.js fix for the observation form (verified-only
// eligibility, unverified-user prompt, form-load error handling).
// SNOW-478: v19 — static/js/map.js now derives overlay label fonts from the
// active basemap and draws the favourites pin as an SDF icon (fixes glyph
// 404s on swisstopo/IGN/basemap.at basemaps).
// SNOW-483: v20 — static/js/map.js now swaps in an inline fallback style
// when the (non-ESRI) basemap style JSON can't be fetched offline, so
// cached region overlays still paint instead of a blank canvas; retried
// on the next ``online`` event.
// SNOW-482: v21 — stamp X-SW-Cache on cache-served responses.
// SNOW-484: v22 — opportunistic cross-origin basemap caching: a new
// 'basemap' fetch classification, a dedicated BASEMAP_CACHE kept separate
// from the shell cache, and a 'register-basemap-origins' message handler
// fed by static/js/map.js.
// SNOW-487: v23 — the basemap-origin allowlist is now durably mirrored
// into meta:app (key basemap.origins, written by static/js/map.js) and
// lazily rehydrated into _basemapOrigins from _classify() on a fresh
// worker restart, so an idle-terminated worker doesn't lose the
// allowlist and fall back to network-only for a previously-cached area.
// SNOW-486: v24 — overlay primitives consolidation touched shell JS/CSS
// (overlays.js, the toast/banner/modal/sheet partials, z-index tokens).
// SNOW-490: v25 — synthesized offline-cache-miss 504 fallbacks now stamp
// X-SW-Cache: miss, so pwa_offline.js can't mistake them for a real
// same-origin sync.
// SNOW-492: v26 — new 'warm-cache' message handler (the map's "Cache this
// area for offline" control) and the map.js/sw_register.js changes that
// drive it.
// SNOW-493: v27 — 'warm-cache-done' now echoes the requestId the page sent
// (sw_register.js correlates it against the in-flight call, ignoring a
// stale reply after a timeout) plus the map.js state-consistency fixes
// (findings 1-8) that ship alongside it.
// SNOW-496: v28 — _classifySync/_classifyCrossOriginGet's origin check/
// _trimCache now delegate to a second importScripts'd module,
// basemap_cache_core.js (self.pwaBasemapCacheCore), so the classification/
// eviction logic can be unit-tested directly. Behaviour is unchanged —
// this is a shell-bytes bump only.
// SNOW-505: v33 — new static/js/map_layer_sync_status.js plus the layers-
// popover shell markup change (sync-status dots, _map_embed.html/home.html).
// SNOW-505 iteration: v34 — "not cached" recoloured grey, and the dots now
// update live (map.js markCached on a tier's toggle-on load; refresh after
// "Cache this area") — shell JS bytes changed in map.js/map_layer_sync_status.js.
// SNOW-505 iteration: v35 — l3 (bulletin groupings) now renders a distinct
// hollow "unavailable" dot (never-cacheable) rather than the grey "not cached
// yet" fill — shell JS/CSS bytes changed (map_layer_sync_status.js, map.css).
// SNOW-511: v36 — the layers popover now clamps its height to the visible map
// area on open/resize so its top rows no longer clip behind the header on
// short viewports — shell JS/CSS bytes changed (map.js, map.css).
// SNOW-509: v37 — the weather-bucket CSS hook moved from `.bulletin-header`
// to `.weather-bucket` (main.css), shared by the new resort-page weather
// panel — shell CSS bytes changed.
// SNOW-518: v38 — map.js now notifies the layers-menu sync dashboard from
// the boot overlay-restore path and ensureCountryLoaded, and adds a
// visibilitychange re-probe — shell JS bytes changed.
// Map bottom-bar rework — split the scrubbed date out of the top
// #region-readout chip into a new #map-date-ribbon beside the bottom-left (i)
// toggle; open the legend card upward so it clears that row; unify the
// bottom-right roundels into one bottom-anchored stack with the (?) help
// roundel at the foot (level with the (i)); add Favourites + Observations
// keys to the legend. Shell HTML/JS/CSS bytes changed (_map_embed,
// _season_ribbon, map.js, map.css); version stamped by bin/sw-version.
// SNOW-521: "Cache this area for offline" reframed as "Download basemap" —
// widened z14-floor zoom band, a docked live "up to N MB" size bar with a
// Download/Cancel confirm step, n/total progress (_warmCache's onProgress +
// a new 'warm-cache-progress' message), and a dedicated
// BASEMAP_PINNED_CACHE deliberate downloads write to instead of
// BASEMAP_CACHE, exempt from the passive stale-while-revalidate LRU trim.
// Shell JS/HTML bytes changed (map.js, sw_register.js, _map_embed.html)
// plus the new static/js/basemap_download_core.js module.
// Distraction-free pin positioning: the new static/js/map_placement_focus.js
// module (window.PlacementFocus) clears every app layer off the basemap while
// a favourite / observation / resort pin is being positioned, wired in from
// place_picker.js and map_edit_resorts.js; map.js closes any open popup on
// entry. Shell JS/HTML bytes changed (place_picker.js, map_edit_resorts.js,
// map.js, home.html) plus the new module.
// SNOW-524: per-country sync dots in the layers menu — country rows carry
// their own dot probed exactly (no ignoreSearch) across all four
// ?country=-scoped feeds, uncached countries are disabled offline, and a
// country click paints a new pulsing "syncing" dot state until its data
// lands. Shell JS/HTML/CSS bytes changed (map.js, map_layer_sync_status.js,
// map.css, _map_embed.html).
// v53 — the region-readout download roundel re-probes the pinned basemap
// cache once the MapLibre style settles, so a reload of an
// already-downloaded region no longer paints the idle download icon until
// the region is reselected. Shell JS bytes changed (map.js).
// v57 — two steps added to the map help tour: the region-download roundel
// (#map-download-control) and the map display date (#map-date-ribbon).
// Shell HTML bytes changed (_map_embed.html).
// v58 — SNOW-532: the dormant l3 sync-dot plumbing is gone from the layers
// menu (OVERLAY_RESOURCES.l3, refresh()'s dated-geojson branch,
// _applyUnavailable and the hollow "unavailable" dot state). Shell JS/CSS
// bytes changed (map_layer_sync_status.js, map.css).
// v59 — SNOW-533: the bulletin-boundary line drops its fixed dash for a
// solid stroke whose width tracks regions-line's zoom curve. Shell JS bytes
// changed (map.js).
// v60 — SNOW-529: a stale TODO(SNOW-XXX) placeholder ticket reference
// reworded to a plain non-ticket note. Comment-only, no behaviour change;
// shell JS bytes changed (db.js).
// v62 — SNOW-536: map control chrome reworked. Every round / pill-ended map
// control moves onto a two-value size scale (--map-control-lg/-sm); the
// basemap download control moves from the region-readout row into the
// bottom-right stack and is renamed .map-download-control; that stack
// becomes collapsible behind a new toggle (the new
// static/js/map_controls_collapse.js module); the favourite / observation
// sheets are inset from the viewport edge. Shell JS/HTML/CSS bytes changed
// (map.js, map_help.js, map.css, main.css, _map_embed.html,
// _season_ribbon.html, _overlay_sheet.html, home.html) plus the new module
// and the new _map_download_control.html partial.
// v63 — SNOW-538: the placement pin is lifted clear of the sheet driving the
// placement (it sat behind the report form on a phone), with the lift
// mirrored into MapLibre's padding so the coordinate always matches the pin;
// the favourite create sheet no longer focuses its name input, which raised
// the keyboard before the pin was placed. Shell JS/HTML/CSS bytes changed
// (place_picker.js, favourites.js, report.js, map.css, _overlay_sheet.html).
// v67 — SNOW-540: the staff edit-resorts panel gains the hand-curated
// Resort detail fields, and its Save button reports the in-flight and
// saved states instead of going silent. Placement stays on the draggable
// marker — it briefly moved to the shared centre pin during this branch
// and was reverted: a geo-anchored marker holds its coordinate while you
// zoom, which is what a mouse-driven desktop tool wants. Shell JS bytes
// changed (map_edit_resorts.js); the panel template itself is not part of
// the cached shell.
// SNOW-568: basemap downloads no longer fail silently. _warmCache's fan-out
// is bounded to WARM_CACHE_CONCURRENCY (it used to issue every URL in one
// tick — up to 2048 for a full-ceiling area — and Chrome answered
// ERR_INSUFFICIENT_RESOURCES to all of them), each failure is classified
// and reported to the page as a `reason`, the page pre-flights the storage
// quota before starting, and both download roundels gained an 'error'
// state plus a toast saying what to do. Shell JS/HTML/CSS bytes changed
// (sw.js, sw_register.js, map.js, basemap_cache_core.js,
// basemap_download_core.js, map.css, _map_embed.html, both
// _map_*download_control.html partials).
// SNOW-569: a download now fills its own geometry on the map as it runs
// (region boundary or framed area), pulses it once, and only then flips the
// roundel — which keeps the download glyph on the green disc rather than
// swapping to a tick. Shell JS/CSS bytes changed (map.js,
// basemap_download_core.js, map.css, both _map_*download_control.html
// partials).
// SNOW-570: a "Downloaded areas" layers-menu overlay (off by default) rings
// every area held in the pinned basemap cache, probed from real Cache
// Storage rather than a stored flag. Shell JS/HTML/CSS bytes changed
// (map.js, basemap_download_core.js, map.css, _map_embed.html).
// SNOW-587: the overlay's dashed downloaded-areas rings
// (regions-line-downloaded, downloaded-area-line) are removed; the
// cached-tiles squares are the only thing it draws now. Shell JS/HTML
// bytes changed (map.js, _map_embed.html).
// SNOW-585: dev-only shell-cache bypass. When DEV_SHELL_BYPASS is on
// (development default, always off in production),
// _staleWhileRevalidate skips the shell cache entirely so a still-in-
// control old worker can't keep serving pre-pull assets after a git
// pull. Shell JS/HTML bytes changed (sw.js, sw_register.js,
// pwa_version_check.js, sw_version.html) plus a new
// pwa_dev_shell_toggle.js.
// SNOW-586: the single shared BASEMAP_PINNED_CACHE is gone — every pinned
// download now writes into its OWN Cache Storage bucket
// (BASEMAP_PINNED_CACHE_PREFIX + areaId), so evicting one area can never
// perforate another's tiles. _warmCache(urls, {pinned, areaId}) requires
// areaId when pinned (a message with none is refused, not silently routed
// to a shared bucket) and now sums each write's byte size
// (basemap_cache_core.js's responseBytes) into the 'warm-cache-done'
// reply's new `bytes` field, replacing the old entry-count FIFO trim with
// the page's own byte-budget/whole-area-eviction plan (map.js,
// basemap_download_core.js's planEviction). The activate sweep drops the
// old shared bucket outright rather than migrating it (no migration —
// nobody has a completed per-area download yet). Shell JS bytes changed
// (sw.js, sw_register.js, map.js, basemap_download_core.js,
// basemap_cache_core.js) plus the new confirm-banner/over-budget-toast
// markup (_overlay_banner.html, _map_embed.html).
const CACHE_VERSION = 'snowdesk-shell-v108';

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

/**
 * SNOW-586: every live per-area pinned bucket currently in Cache Storage
 * — i.e. every ``caches.keys()`` entry under ``BASEMAP_PINNED_CACHE_PREFIX``
 * EXCLUDING the legacy shared name, which activate's sweep deletes rather
 * than treats as a bucket.
 *
 * Deliberately re-derived from ``caches.keys()`` on every call rather than
 * memoised: the page deletes buckets directly (a confirmed eviction), so a
 * cached name list kept here would go stale with no invalidation path.
 *
 * @returns {Promise<string[]>}
 */
async function _pinnedCacheNames() {
  const names = await caches.keys();
  return names.filter(
    (name) => name.startsWith(BASEMAP_PINNED_CACHE_PREFIX) && name !== LEGACY_BASEMAP_PINNED_CACHE,
  );
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
// loses connectivity. Keep this list short — anything hashed by
// ManifestStaticFilesStorage can't be precached by stable URL, and
// stale-while-revalidate already handles the shell on the second visit.
const OFFLINE_FALLBACK = '/static/offline.html';
const PRECACHE_URLS = [OFFLINE_FALLBACK];

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
 * @returns {Promise<void>}
 */
function _hydrateBasemapOrigins() {
  if (_basemapHydration) return _basemapHydration;
  _basemapHydration = (async () => {
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
      // request just falls through to network-only, same as before
      // SNOW-487. A transient failure memoises this empty result for the
      // worker's lifetime; recovery comes from the next live page's
      // register-basemap-origins message, which resets _basemapHydration.
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
  return _basemapHydration;
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
  const fetchPromise = fetch(request)
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
  // SNOW-490: stamp the synthesized fallback so pwa_offline.js can't
  // mistake an offline cache miss for a successful sync.
  return new Response('', {
    status: 504,
    statusText: 'Gateway Timeout',
    headers: { 'X-SW-Cache': 'miss' },
  });
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
// issued the whole list in one tick — up to 2048 fetches for a
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

/**
 * SNOW-568: thin delegator — see basemap_cache_core.js's module header.
 *
 * @param {string|null} a
 * @param {string|null} b
 * @returns {string|null}
 */
function _warmCacheWorseReason(a, b) {
  if (self.pwaBasemapCacheCore && self.pwaBasemapCacheCore.worseReason) {
    return self.pwaBasemapCacheCore.worseReason(a, b);
  }
  return a || b || null;
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
 * @param {string[]} urls
 * @param {{pinned?: boolean, areaId?: string, onProgress?: (done: number,
 *   total: number) => void}} [options] ``areaId`` is REQUIRED when
 *   ``pinned`` is true — the caller (the ``message`` handler below)
 *   refuses the run rather than calling this with ``pinned`` and no
 *   ``areaId``, so this function can assume the pair is already valid.
 * @returns {Promise<{ok: number, failed: number, reason: string|null,
 *   bytes: number}>}
 */
async function _warmCache(urls, options) {
  const opts = options || {};
  const pinned = !!opts.pinned;
  const areaId = opts.areaId;
  const onProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;
  const shellCache = await caches.open(CACHE_VERSION);
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
    onProgress(done, total, settled);
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
      await cache.put(url.toString(), response.clone());
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
    await _trimCache(basemapCache, BASEMAP_CACHE_MAX_ENTRIES).catch(() => {});
  }
  return { ok, failed, reason, bytes };
}

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
 * SNOW-586: iterates every LIVE pinned bucket (``_pinnedCacheNames()``,
 * re-derived per call — see its own docstring for why it is deliberately
 * not memoised here), not one shared cache, since a tile from ANY
 * deliberate download should still serve offline regardless of which
 * area's bucket holds it. Stops at the first hit; a tile shared by two
 * overlapping areas is identical bytes in either bucket, so which one
 * answers first makes no difference to what is served.
 */
async function _basemapStaleWhileRevalidate(request) {
  const cache = await caches.open(BASEMAP_CACHE);
  const cached = await cache.match(request);
  const fetchPromise = fetch(request)
    .then(async (response) => {
      if (response && response.ok && response.type === 'cors') {
        await cache.put(request, response.clone()).catch(() => {});
        await _trimCache(cache, BASEMAP_CACHE_MAX_ENTRIES).catch(() => {});
      }
      return response;
    })
    .catch(() => null);
  if (cached) return cached;
  // SNOW-586: BASEMAP_CACHE miss — check every live pinned bucket before
  // falling through to the network. Defensive: a pinned-cache lookup
  // failure must not break the existing miss -> network -> 504 chain.
  try {
    const pinnedNames = await _pinnedCacheNames();
    for (const name of pinnedNames) {
      const pinnedCache = await caches.open(name);
      const pinnedHit = await pinnedCache.match(request);
      if (pinnedHit) return pinnedHit;
    }
  } catch (_err) {
    // Fall through to the network/504 path below.
  }
  // Cache miss (both partitions): fall through to the network. If that
  // also fails (e.g. offline and never previously cached), this
  // deliberately does NOT throw — the caller (_guardedRespond via the
  // fetch handler) still needs a Response, and a 504 here is more
  // informative than an unhandled rejection reaching the page as a raw
  // network error.
  const network = await fetchPromise;
  if (network) return network;
  // SNOW-490: stamp the synthesized fallback so pwa_offline.js can't
  // mistake an offline cache miss for a successful sync.
  return new Response('', {
    status: 504,
    statusText: 'Gateway Timeout',
    headers: { 'X-SW-Cache': 'miss' },
  });
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
 */
async function _networkFirst(request) {
  const cache = await caches.open(CACHE_VERSION);
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === 'basic') {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return _stampCacheHit(cached);
    if (request.mode === 'navigate' || request.destination === 'document') {
      const searchless = await cache.match(request, { ignoreSearch: true });
      if (searchless) return _stampCacheHit(searchless);
      const fallback = await cache.match(OFFLINE_FALLBACK);
      if (fallback) return _stampCacheHit(fallback);
    }
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
  // The page sends this when the user clicks "Reload" on the update
  // banner. Activating the waiting worker triggers ``activate`` (and its
  // ``clients.claim()``), which hands control to this new shell.
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
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
  // — see ``basemap_download_core.js``'s ``areaIdForRegion``/
  // ``CUSTOM_AREA_ID``) selects WHICH pinned bucket a pinned run writes
  // into. ``pinned`` with no ``areaId`` is a programming error, not a
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
      event.source?.postMessage({
        type: 'warm-cache-done',
        ok: 0,
        failed: event.data.urls.length,
        reason: 'other',
        bytes: 0,
        requestId,
      });
    } else {
      const onProgress = (done, total, settled) => {
        event.source?.postMessage({
          type: 'warm-cache-progress',
          done,
          total,
          // Tile-grid rework: indices into the posted URL list that succeeded since
          // the last message — the page turns them back into tiles.
          settled,
          requestId,
        });
      };
      const warm = _warmCache(event.data.urls, { pinned, areaId, onProgress }).then((result) => {
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
          requestId,
        });
      });
      if (typeof event.waitUntil === 'function') event.waitUntil(warm);
    }
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
async function _selfDrainMutations() {
  // Fall back to a hand-rolled version of the shared helpers if
  // importScripts failed at startup — keeps this path self-contained
  // rather than a hard dependency on the earlier try/catch succeeding.
  const core = self.pwaMutationQueueCore || {
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
  };

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

    if (outcome === 'success') {
      await _idbDelete(db, 'queue:mutations', row.id);
      successCount += 1;
      continue;
    }

    if (outcome === 'permanent') {
      // This attempt just happened — increment before persisting/
      // reporting so `attempts` (and the telemetry it feeds) reflects the
      // failed attempt rather than reading as "never attempted".
      row.attempts = (row.attempts || 0) + 1;
      row.status = 'failed';
      await _idbPut(db, 'queue:mutations', row);
      _postTelemetry('pwa.mutation.failed_permanent', {
        method: row.method,
        url: row.url,
        idempotency_key: row.idempotency_key,
        attempts: row.attempts,
        reason: 'permanent_4xx',
      });
      continue;
    }

    // 'retry'
    const attempts = (row.attempts || 0) + 1;
    row.attempts = attempts;
    if (attempts >= core.MAX_ATTEMPTS) {
      row.status = 'failed';
      await _idbPut(db, 'queue:mutations', row);
      _postTelemetry('pwa.mutation.failed_permanent', {
        method: row.method,
        url: row.url,
        idempotency_key: row.idempotency_key,
        reason: 'max_attempts',
      });
    } else {
      row.status = 'retry-scheduled';
      row.next_attempt_at = now + core.backoffDelayMs(attempts);
      await _idbPut(db, 'queue:mutations', row);
      retryableRemaining = true;
    }
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
