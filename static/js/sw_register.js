/*
 * static/js/sw_register.js — Site-wide service-worker registration.
 *
 * Loaded deferred from ``public/templates/public/base.html`` so the SW
 * registration runs on every public page — required for browser
 * install prompts, which only appear after the page is served by an
 * SW with a manifest carrying valid icons.
 *
 * Kill-switch config gate (SNOW-373, spec §6.2 Mechanism A)
 * ---------------------------------------------------------
 * Before registering any SW we fetch ``/api/sw-config`` (SNOW-372) —
 * ``cache: 'no-store'`` so ops sees the live value. Two possible
 * outcomes drive the branch:
 *
 *   - ``{kill: true}``  → unregister every SW on this origin and stop.
 *     The next navigation goes straight to the network. Nothing on the
 *     origin is served through a SW until ops flips ``kill`` back.
 *
 *   - ``{kill: false, sw_url: <path>}`` → register the SW URL the
 *     config returned. Defaults to ``/sw.js``; ops can flip it to
 *     ``/sw-kill.js`` (Mechanism B, ``static/js/sw-kill.js``) to swap
 *     every installed client onto the wipe-and-unregister worker
 *     without a code deploy.
 *
 * If ``/api/sw-config`` is unreachable (server down, network dropped),
 * fall back to ``{sw_url: '/sw.js', kill: false}`` — the failure mode
 * of the config endpoint must never block SW registration on a working
 * server. Non-reachability is the state the endpoint would report
 * before the SNOW-372 deploy, so this also handles the transition
 * gracefully.
 *
 * Update flow
 * -----------
 * The contract this implements, end-user-facing: *if there is an
 * update, you see one "Reload" message; if there is no message, you are
 * already on the latest version.*
 *
 * The PWA shell SW (``static/js/sw.js``) does NOT ``skipWaiting()`` on
 * install — a freshly-installed worker sits in the "waiting" state, and
 * that waiting worker IS the pending update. This script reveals the
 * ``#sw-update-banner`` markup baked into ``base.html`` whenever a fresh
 * SW has finished installing AND an old SW still controls the page
 * (= a real update, not a first-time install).
 *
 * A second reveal path lives in ``pwa_version_check.js`` (SNOW-374):
 * when the server's ``X-App-Version`` header drifts from the shell's
 * ``<meta name="pwa-app-version">`` we surface the same banner even if
 * ``sw.js`` itself didn't change. Both paths land in the same DOM node.
 *
 * Clicking "Reload" runs ``handleReloadClick`` below:
 *
 *   * If a fresh SW is waiting → post ``{ type: 'SKIP_WAITING' }``. The
 *     worker activates, calls ``clients.claim()``, and the browser fires
 *     ``controllerchange`` — at which point we reload the page exactly
 *     once (guarded by ``refreshing``) onto the new shell.
 *
 *   * If no worker is waiting (the version-header path) → clear the SW's
 *     shell caches and reload. Without clearing, the SW's
 *     ``_networkFirst`` navigate handler can hand back the cached HTML
 *     that carries the stale ``pwa-app-version`` meta tag, so the
 *     version-check would immediately re-show the banner and the user
 *     would be stuck in a reload loop.
 *
 *   Either way the click always ends in a reload: the waiting worker is
 *   re-resolved from the live registration (a captured reference can go
 *   redundant, and messaging a redundant worker is a silent no-op), and a
 *   fallback timer downgrades to the cache-clearing reload if activation
 *   never fires ``controllerchange``.
 *
 *   Either way it also takes a moment, so the click is acknowledged on
 *   screen before any of it starts — see ``showBannerBusy``.
 *
 * The build line
 * --------------
 * The banner names both builds: the one this shell was delivered on and
 * the one the reload will land on. Neither side can render that alone —
 * the shell's build is baked into its own ``<meta>`` tags and the
 * server's is only in ``/api/version`` — so the partial ships an empty
 * slot (``detail_id``) and ``fillBuildLine`` composes the line into it.
 * The version-check path passes the verdict it already has; the SW path
 * borrows that module's probe. Neither is load-bearing: a line that
 * cannot be composed stays hidden and the banner works exactly as before.
 *
 * Because the reload is gated on the user having clicked "Reload"
 * (``userTriggeredUpdate``), a first-install ``clients.claim()`` does not
 * reload the page, and there is no dev reload-loop.
 *
 * ``register`` passes ``updateViaCache: 'none'`` so the SW script is
 * never served from the HTTP cache during an update check — a changed
 * ``sw.js`` is always detected. We also call ``registration.update()``
 * when the tab regains focus, so a long-open tab learns about a new
 * version without needing a navigation.
 *
 * Errors from ``register()`` are logged but never surfaced to the
 * user — the site is fully usable without a service worker.
 *
 * i18n: on public pages every user-visible string lives in the banner
 * template under ``{% trans %}`` and this script only toggles visibility.
 * The self-injected admin fallback banner carries English strings inline
 * (the admin is staff-only and English-only), so it is the one exception.
 */

(function () {
  'use strict';

  // SNOW-585: only present when settings.SW_DEV_SHELL_BYPASS is on (base.html;
  // always false in production). The bypass already serves fresh shell assets
  // on the very next reload — even from a worker that hasn't picked up the
  // new one yet — so the update banner would be actively misleading; showing
  // it, and its pwa.sw.update_available telemetry, is suppressed at the one
  // call site below. See docs/decisions/dev-bypasses-the-shell-cache.md.
  const DEV_SHELL_BYPASS_ACTIVE =
    document.querySelector('meta[name="pwa-dev-shell-bypass"]')?.getAttribute('content') === '1';

  // SNOW-620: copy for the self-injected fallback banner (ensureBanner
  // below), which is what admin pages get — they don't include the public
  // chrome, so includes/_sw_update_banner.html and its already-translated
  // strings aren't there. Rendered into a strings template by
  // admin/base_site.html; the literals are the English fallback, and are
  // deliberately the same three strings that partial already translates.
  // See static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('sw-update-strings-template', {
    'update-title': 'Update available',
    'update-body':
      'A newer version of Snowdesk is ready. Your downloaded maps and ' +
      'saved data are kept.',
    reload: 'Reload',
    dismiss: 'Dismiss',
    updating: 'Updating…',
    'updating-title': 'Updating Snowdesk',
    'updating-body':
      'Applying the new version — the page will reload in a moment.',
  });

  // Guard on the truthy value, not ``'serviceWorker' in navigator``: the
  // e2e SW-stripping helpers define the property with ``value: undefined``
  // (key present, value nullish), and the ``navigator.serviceWorker``
  // dereferences below (``.addEventListener`` etc.) would otherwise throw
  // (SNOW-484). Equivalent to the ``in`` check wherever the SW is genuinely
  // supported.
  if (!navigator.serviceWorker) return;

  // The installed-but-waiting worker (the pending update), captured when
  // we show the banner so the "Reload" handler can message it.
  let waitingWorker = null;
  // Set true only when the user clicks "Reload", so a first-install
  // ``clients.claim()`` (which also fires controllerchange) never reloads
  // the page — only a user-accepted update does.
  let userTriggeredUpdate = false;
  // Guards against a double reload if controllerchange fires more than
  // once.
  let refreshing = false;
  // SNOW-384: idempotency guard for pwa.sw.update_available — showUpdateBanner
  // can be reached from three entry points (registration.waiting at
  // register-time, registration.installing, and updatefound); this
  // ensures the telemetry event fires once per distinct waiting worker
  // rather than once per entry point that happens to observe it.
  let announcedUpdateWorker = null;

  // SNOW-492: slot for the in-flight "Cache this area" call, if any. Only
  // one warm-cache run can be in flight at a time — map.js click-guards its
  // button while busy — so a single slot is enough.
  //
  // SNOW-493 finding 9: the slot now carries a ``requestId`` alongside the
  // ``resolve`` function. Without it, a worker reply that arrives AFTER
  // ``warmCache()`` has already given up (timed out and resolved ``null``)
  // would still find ``_warmCacheSlot.resolve`` set — a later call's own
  // ``resolve`` — and wrongly settle it with the stale reply's numbers.
  // Correlating on ``requestId`` (minted per call, echoed back by the
  // worker) means a reply only ever resolves the call it actually answers.
  let _warmCacheSlot = null;

  /**
   * Mint a request id for a ``warmCache()`` call. ``crypto.randomUUID`` is
   * available on every browser this feature targets; the hex fallback
   * mirrors ``mutation_queue.js``'s ``_mintIdempotencyKey`` /
   * ``db.js``'s ``_randomHex`` for the rare runtime without it.
   * @returns {string}
   */
  function _mintRequestId() {
    try {
      if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
      }
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      let out = '';
      for (let i = 0; i < bytes.length; i++) {
        out += bytes[i].toString(16).padStart(2, '0');
      }
      return out;
    } catch (_e) {
      return 'warm-' + Date.now().toString(16) + '-' + Math.floor(Math.random() * 1e9).toString(16);
    }
  }

  // SNOW-384: SW → page telemetry bridge. sw.js (and sw-kill.js) have no
  // ``window`` and so cannot call ``window.pwaTelemetry`` directly; they
  // instead post ``{type: 'pwa-telemetry', event, properties}`` to every
  // client they control. This single listener forwards any such message,
  // regardless of which SW script sent it, to ``window.pwaTelemetry.emit``.
  //
  // SNOW-376: the same channel also carries ``{type: 'drain-mutations'}``
  // when a Background Sync fires for the mutation-queue tag while this
  // tab is open — see static/js/sw.js's ``_handleMutationSync``.
  //
  // SNOW-492: and ``{type: 'warm-cache-done', ok, failed}`` — the
  // completion summary for a ``warmCache()`` call below.
  navigator.serviceWorker.addEventListener('message', (event) => {
    const data = event.data;
    if (!data) return;
    if (data.type === 'pwa-telemetry' && data.event) {
      try {
        window.pwaTelemetry?.emit(data.event, data.properties || {});
      } catch (_err) {
        // Ignore — telemetry must never break the SW message channel.
      }
      return;
    }
    // SNOW-376: a Background Sync fired for the mutation-queue tag while
    // this tab was open — sw.js delegates to the real
    // window.pwaMutationQueue.drain() (which already has window.pwaDb /
    // window.pwaTelemetry wired up) rather than duplicating that logic
    // in the worker.
    if (data.type === 'drain-mutations') {
      try {
        window.pwaMutationQueue?.drain();
      } catch (_err) {
        // Ignore — a broken drain must never break the SW message channel.
      }
      return;
    }
    // SNOW-521: a throttled progress step (~every 5%, plus the final one)
    // for the in-flight warm-cache call. Only forwarded to the caller's
    // onProgress if it matches the in-flight slot's requestId (same stale-
    // reply guard as 'warm-cache-done') — and, critically, resets the
    // silence timeout below so a long multi-tile download's own progress
    // keeps it alive rather than resolving null mid-transfer.
    if (data.type === 'warm-cache-progress') {
      if (_warmCacheSlot && data.requestId === _warmCacheSlot.requestId) {
        _warmCacheSlot.onTimeoutReset?.();
        try {
          // Tile-grid rework: ``data.settled`` (indices into the posted URL list
          // that succeeded since the last message) rides along as a third
          // argument. An older worker still serving a cached shell won't
          // send it — callers treat ``undefined`` as "counts only" and
          // fall back to a proportional fill.
          //
          // SNOW-632: ``data.bytes`` rides along as a fourth argument — the
          // run's on-disk bytes so far, for a live MB readout. An older
          // worker won't send it either — ``undefined`` reads the same way
          // ``settled`` does above.
          _warmCacheSlot.onProgress?.(data.done, data.total, data.settled, data.bytes);
        } catch (_err) {
          // A broken onProgress callback must never break the SW message
          // channel or abort the in-flight call.
        }
      }
      return;
    }
    if (data.type === 'warm-cache-done') {
      // SNOW-493 finding 9: only resolve if this reply matches the
      // in-flight slot's requestId — a stale reply for a call that already
      // timed out (and whose slot was replaced or cleared) must not
      // resolve a different, later call.
      if (_warmCacheSlot && data.requestId === _warmCacheSlot.requestId) {
        // SNOW-568: ``reason`` rides along so the caller can tell a full
        // disk from a flaky network. An older worker still serving a
        // cached shell won't send it — ``null`` then reads as "failed,
        // cause unknown", which is what callers already assumed.
        _warmCacheSlot.resolve({
          ok: data.ok,
          failed: data.failed,
          reason: data.reason || null,
          // SNOW-586: the run's total on-disk bytes, so the caller can
          // record it against the area's standing budget entry. An older
          // worker still serving a cached shell won't send it — 0 then
          // reads as "unknown size", which callers already treat safely
          // (nothing is ever evicted on the strength of a zero).
          bytes: data.bytes || 0,
          // SNOW-632: whether the run stopped early on a cancelWarmCache()
          // request rather than running every URL to completion. An older
          // worker won't send it — ``false`` is the correct read, since
          // that worker has no cancellation protocol to have honoured.
          cancelled: !!data.cancelled,
        });
        _warmCacheSlot = null;
      }
    }
  });

  // SNOW-492: how long warmCache() waits for the worker's warm-cache-done
  // reply before giving up. A worker terminated (idle-killed, crashed)
  // between receiving the message and posting the reply would otherwise
  // leave the caller's promise pending forever — cacheNowInit's button
  // would stay aria-disabled for the rest of the page's life.
  //
  // SNOW-521: this is now "30s of no progress", not a flat 30s from the
  // call's start — every 'warm-cache-progress' message rearms the timer
  // (see the message listener above), so a large "Download basemap" run
  // that's still making progress past the 30s mark is never cut short.
  const WARM_CACHE_TIMEOUT_MS = 30000;

  // SNOW-622: floor on how often a `visibilitychange` can trigger a
  // `registration.update()`. That call is a network round trip for the
  // worker script every time — `/sw.js` is served `Cache-Control: no-cache`
  // — and the event fires on every tab switch, app switch and screen
  // unlock. One minute is well under how often the site is deployed, so
  // nothing is surfaced meaningfully later than before.
  const SW_UPDATE_CHECK_INTERVAL_MS = 60000;

  // SNOW-605: how long ``_activeWorker()`` waits for a controller before
  // declaring the page uncontrolled. Sized for the activation window of an
  // SW update — the gap between the new worker activating and its
  // ``clients.claim()`` reaching this page — which is milliseconds, not
  // seconds. It is deliberately NOT sized for a shift-reloaded page, where
  // no controller is ever coming and the only cure is a reload: waiting
  // longer there would just stall the download control with a spinner
  // before showing the same message.
  const CONTROLLER_WAIT_MS = 3000;

  /**
   * SNOW-605: the worker that should receive this page's messages, waiting
   * briefly for one if the page is momentarily uncontrolled.
   *
   * ``navigator.serviceWorker.controller`` is null in three situations that
   * look identical from here but are not: the activation window of an
   * update (a controller is about to arrive), a shift-reloaded document
   * (none ever will — Chrome loads it uncontrolled on purpose), and a first
   * load whose worker has not yet claimed. Waiting for ``controllerchange``
   * resolves the first and third; the second falls through to the timeout,
   * which is the honest answer for it.
   *
   * ``navigator.serviceWorker.ready`` is awaited first so a page whose
   * worker is still installing doesn't time out on a technicality — it
   * resolves once a registration is active, which is the earliest a
   * controller could exist.
   *
   * @returns {Promise<ServiceWorker | null>}
   */
  function _activeWorker() {
    if (navigator.serviceWorker.controller) {
      return Promise.resolve(navigator.serviceWorker.controller);
    }
    return new Promise((resolve) => {
      let done = false;
      const finish = (worker) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        navigator.serviceWorker.removeEventListener('controllerchange', onChange);
        resolve(worker);
      };
      const onChange = () => finish(navigator.serviceWorker.controller);
      const timer = setTimeout(() => finish(navigator.serviceWorker.controller), CONTROLLER_WAIT_MS);
      navigator.serviceWorker.addEventListener('controllerchange', onChange);
      // A registration that is already active may still not have claimed
      // this page; re-check once ``ready`` settles rather than waiting out
      // the full timeout for a controller that is already there.
      navigator.serviceWorker.ready
        .then(() => {
          if (navigator.serviceWorker.controller) finish(navigator.serviceWorker.controller);
        })
        .catch(() => {});
    });
  }

  /**
   * SNOW-492: bridge for map.js's "Download basemap" control. Posts the
   * given URL list to the active worker's ``warm-cache`` message handler
   * (``static/js/sw.js``) and resolves with its ``{ok, failed}`` summary
   * once the worker posts ``warm-cache-done`` back.
   *
   * SNOW-605: an uncontrolled page no longer fails on the spot. A page can
   * have no ``controller`` while a perfectly healthy worker exists — during
   * an update's activation window, and (for the whole life of the document)
   * after a shift-reload, which Chrome deliberately loads uncontrolled. This
   * used to resolve ``null`` on the first line, which the download controls
   * report as a flat "Download failed. Check your connection and try again."
   * having dispatched nothing — no request, nothing to see in the network
   * panel, and a message pointing at the one thing that isn't wrong. So:
   * wait ``CONTROLLER_WAIT_MS`` for a controller to arrive, and only if none
   * does resolve ``{ok: 0, failed: 0, reason: 'no-worker', bytes: 0}`` — a
   * distinct reason the caller can word accurately (reload the page), rather
   * than ``null``, which is indistinguishable from a run that fetched
   * nothing.
   *
   * SNOW-568: if the worker goes ``WARM_CACHE_TIMEOUT_MS`` without a reply
   * OR a progress message (see that constant's comment), this resolves
   * ``{ok: 0, failed: 0, reason: 'timeout'}`` rather than ``null``. The two
   * used to be conflated, which meant a worker that died mid-download was
   * indistinguishable from a browser that never had one — and both were
   * silently swallowed by the caller's "nothing to report" branch.
   *
   * SNOW-493 finding 9: mints a ``requestId`` for this call and posts it
   * alongside the URL list; ``sw.js`` echoes it back in both
   * ``warm-cache-progress`` and ``warm-cache-done``. On timeout, this
   * call's own promise still resolves ``null``, but the slot is only
   * cleared if it still belongs to THIS request — otherwise a request that
   * arrived and repopulated the slot in the interim (vanishingly unlikely
   * given map.js's click-guard, but not impossible if a caller bypasses
   * it) would have its slot wiped out from under it by this stale timeout.
   *
   * SNOW-521: ``opts.pinned`` is forwarded verbatim in the posted message
   * (``sw.js`` reads ``event.data.pinned``); ``opts.onProgress(done,
   * total, settled)``, if supplied, is invoked from the message listener
   * above on every ``warm-cache-progress`` reply matching this call's
   * requestId. A caller passing no ``opts`` at all (the pre-SNOW-521 call
   * shape) still works — ``pinned`` defaults false and progress is simply
   * not observed.
   *
   * SNOW-586: ``opts.areaId`` is likewise forwarded verbatim
   * (``event.data.areaId``) — REQUIRED whenever ``opts.pinned`` is true,
   * since it selects which per-area pinned bucket the run writes into;
   * ``sw.js`` refuses a pinned run with no ``areaId`` rather than falling
   * back to a shared bucket (see its own docstring). The resolved
   * summary's ``bytes`` field is the run's total on-disk size
   * (``sw.js``'s ``_warmCache`` sums it as it writes), which the caller
   * records against the area's standing budget entry.
   *
   * Tile-grid rework: ``settled`` is the batch of ``urls`` indices that succeeded
   * since the previous report — see ``_warmCache`` in ``sw.js``.
   *
   * SNOW-632: ``opts.onProgress``'s fourth argument, ``bytes``, is the
   * run's on-disk total so far — see ``_warmCache`` in ``sw.js``. The
   * resolved summary's ``cancelled`` field is true when the run stopped
   * early on a ``cancelWarmCache()`` request rather than running every URL
   * to completion; a cancelled run always has ``failed: 0`` (nothing
   * skipped by the cancel was ever attempted), so a caller MUST check
   * ``cancelled`` before treating a short ``ok`` count as evidence of
   * trouble.
   *
   * @param {string[]} urls
   * @param {{pinned?: boolean, areaId?: string, onProgress?: (done: number,
   *   total: number, settled?: number[], bytes?: number) => void}} [opts]
   * @returns {Promise<{ok: number, failed: number, reason: string|null,
   *   bytes: number, cancelled: boolean} | null>}
   */
  async function warmCache(urls, opts) {
    const active = await _activeWorker();
    // SNOW-605: no controller after the wait — the page is uncontrolled and
    // will stay that way until it reloads. Report it as its own reason so
    // the user is told to reload rather than to check their connection.
    if (!active) return { ok: 0, failed: 0, reason: 'no-worker', bytes: 0, cancelled: false };
    const options = opts || {};
    const requestId = _mintRequestId();
    return new Promise((resolve) => {
      let settled = false;
      let timeoutHandle = null;

      const settle = (value) => {
        if (settled) return;
        settled = true;
        if (timeoutHandle) clearTimeout(timeoutHandle);
        resolve(value);
      };

      // SNOW-521: (re)arms the silence timeout — called once up front and
      // again from the message listener on every progress reply, so the
      // effective wait is "30s since the last sign of life", not a flat
      // 30s from the start of a possibly-long download.
      const armTimeout = () => {
        if (timeoutHandle) clearTimeout(timeoutHandle);
        timeoutHandle = setTimeout(() => {
          if (_warmCacheSlot && _warmCacheSlot.requestId === requestId) {
            _warmCacheSlot = null;
          }
          // SNOW-568: a summary with reason 'timeout', not ``null``.
          // ``null`` keeps its single pre-existing meaning — "there is no
          // active worker, so there was nothing to warm" — which callers
          // treat as a non-event; a worker that went silent mid-run IS an
          // event, and the user needs telling.
          settle({ ok: 0, failed: 0, reason: 'timeout', bytes: 0, cancelled: false });
        }, WARM_CACHE_TIMEOUT_MS);
      };

      _warmCacheSlot = {
        requestId,
        resolve: settle,
        onProgress: options.onProgress,
        onTimeoutReset: armTimeout,
      };
      armTimeout();
      active.postMessage({
        type: 'warm-cache',
        urls: urls || [],
        requestId,
        pinned: !!options.pinned,
        // SNOW-586: which pinned bucket a pinned run writes into —
        // undefined for a non-pinned call, same as before this ticket.
        areaId: options.areaId,
      });
    });
  }

  Object.defineProperty(window, 'pwaWarmCache', {
    value: warmCache,
    writable: false,
    configurable: false,
  });

  /**
   * SNOW-632: ask the worker to stop DISPATCHING further URLs for the
   * in-flight ``warmCache()`` call, if there is one — the page-side end of
   * the overlay's Cancel button. Posts ``{type: 'warm-cache-cancel',
   * requestId}`` for ``_warmCacheSlot``'s own requestId, the same
   * correlation ``warmCache()`` itself relies on, so a cancel can never be
   * misdirected at a later, unrelated call.
   *
   * Not an abort: ``sw.js``'s pool can already have up to
   * ``WARM_CACHE_CONCURRENCY`` fetches in flight, and those are left to
   * finish and write — see ``_warmCache``'s docstring in ``sw.js`` for why.
   * The eventual ``warm-cache-done`` reply carries ``cancelled: true``,
   * which resolves this call's own ``warmCache()`` promise exactly as any
   * other completion does; ``cancelWarmCache()`` itself resolves once the
   * request has been posted, not once the run has actually stopped.
   *
   * A safe no-op with no run in flight (``_warmCacheSlot`` is ``null``) or
   * on an uncontrolled page (mirrors ``warmCache()``'s own wait via
   * ``_activeWorker()``, re-checking the slot afterwards in case the run
   * settled while we were waiting for a controller).
   *
   * @returns {Promise<void>}
   */
  async function cancelWarmCache() {
    if (!_warmCacheSlot) return;
    const active = await _activeWorker();
    if (!active || !_warmCacheSlot) return;
    active.postMessage({ type: 'warm-cache-cancel', requestId: _warmCacheSlot.requestId });
  }

  Object.defineProperty(window, 'pwaWarmCacheCancel', {
    value: cancelWarmCache,
    writable: false,
    configurable: false,
  });

  /**
   * Resolve the banner element, creating one if the page didn't render
   * the public partial. Public pages ship ``#sw-update-banner``
   * (Tailwind-styled, ``templates/includes/_sw_update_banner.html``) in
   * base.html; the Django admin — which the SW also controls (scope
   * ``/``) — does not load the public chrome, so without this a waiting
   * worker would go unannounced there. We self-inject an inline-styled
   * card (no Tailwind dependency) that mirrors the public banner's shape
   * so the update contract holds on EVERY page the SW controls, with no
   * exceptions.
   *
   * The reveal contract for both variants is the same: toggling the
   * ``hidden`` class (public banner) or the equivalent ``display``
   * property (admin fallback) via ``showUpdateBanner`` /
   * ``hideUpdateBanner`` below.
   *
   * @returns {HTMLElement | null}
   */
  function ensureBanner() {
    let el = document.getElementById('sw-update-banner');
    if (el || !document.body) return el;
    el = document.createElement('div');
    el.id = 'sw-update-banner';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.dataset.fallback = '1';
    el.style.cssText =
      'position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);' +
      'z-index:2147483647;display:none;align-items:center;gap:.75rem;' +
      'width:calc(100vw - 2rem);max-width:28rem;padding:.75rem 1rem;' +
      'border-radius:12px;background:#ffffff;color:#0f172a;' +
      'border:1px solid rgba(15,23,42,.12);font:500 14px system-ui,sans-serif;' +
      'box-shadow:0 10px 30px rgba(0,0,0,.15);';
    const copy = document.createElement('div');
    copy.style.cssText = 'flex:1;min-width:0;';
    const title = document.createElement('div');
    title.id = 'sw-update-banner-title';
    title.textContent = STRINGS['update-title'];
    title.style.cssText = 'font-weight:600;font-size:14px;';
    const sub = document.createElement('div');
    sub.id = 'sw-update-banner-body';
    sub.textContent = STRINGS['update-body'];
    sub.style.cssText = 'color:#475569;font-size:12px;margin-top:2px;';
    // The build line, matching the public partial's `detail_id` slot. Same
    // id on both variants so `fillBuildLine` below is one code path rather
    // than a third fork of "which banner am I on".
    const versions = document.createElement('div');
    versions.id = 'sw-update-banner-versions';
    versions.hidden = true;
    versions.style.cssText =
      'color:#64748b;font:400 11px ui-monospace,SFMono-Regular,monospace;' +
      'margin-top:2px;';
    copy.append(title, sub, versions);
    const reload = document.createElement('button');
    reload.type = 'button';
    reload.id = 'sw-update-banner-reload';
    reload.textContent = STRINGS.reload;
    reload.style.cssText =
      'cursor:pointer;border:0;border-radius:9999px;background:#0f172a;' +
      'color:#fff;padding:.4rem .9rem;font:600 13px system-ui,sans-serif;';
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.dataset.action = 'dismiss';
    dismiss.setAttribute('aria-label', STRINGS.dismiss);
    dismiss.textContent = '×';
    dismiss.style.cssText =
      'cursor:pointer;border:0;background:transparent;color:#64748b;' +
      'font-size:20px;line-height:1;padding:0 .25rem;';
    el.append(copy, reload, dismiss);
    document.body.appendChild(el);
    return el;
  }

  const banner = ensureBanner();

  /**
   * Reveal the update banner. Public pages toggle the ``hidden`` class the
   * partial ships with; the self-injected admin fallback (data-fallback="1"
   * — no Tailwind) uses inline ``display: flex`` instead. One entry point
   * so callers don't have to care which page they're on.
   */
  function showUpdateBanner(worker) {
    // SNOW-585: suppress both the DOM reveal and the pwa.sw.update_available
    // emit below — see DEV_SHELL_BYPASS_ACTIVE's comment above.
    if (DEV_SHELL_BYPASS_ACTIVE) return;
    if (worker) waitingWorker = worker;
    // SNOW-384: emit once per distinct waiting worker, not once per call
    // site that happens to observe it (see announcedUpdateWorker above).
    if (worker && worker !== announcedUpdateWorker) {
      announcedUpdateWorker = worker;
      try {
        window.pwaTelemetry?.emit('pwa.sw.update_available', {});
      } catch (_err) {
        // Ignore — telemetry must never break the update banner.
      }
    }
    revealUpdateBanner();
  }

  function hideUpdateBanner() {
    if (!banner) return;
    if (banner.dataset.fallback === '1') {
      banner.style.display = 'none';
    } else {
      banner.classList.add('hidden');
    }
  }

  /**
   * Reveal the update banner, honouring the fallback markup's own idiom.
   *
   * Public pages ship the `_toast.html` partial, hidden with the `hidden`
   * CLASS; the admin fallback this file synthesises is inline-styled and
   * uses `display`. One fork, one place — see `window.pwaUpdateBanner`.
   *
   * @param {{current: string, released_at: string} | undefined} detail
   *   What the SERVER says it is now serving, when the caller already
   *   knows: `pwa_version_check.js` reaches this off the back of its own
   *   `/api/version` round trip and passes the verdict straight through.
   *   Omitted by the SW-driven path — a waiting worker is a fact about
   *   this browser and says nothing about the server's build — which is
   *   why `fillBuildLine` probes when it is not given one.
   * @returns {void}
   */
  function revealUpdateBanner(detail) {
    if (DEV_SHELL_BYPASS_ACTIVE) return;
    if (!banner) return;
    if (banner.dataset.fallback === '1') {
      banner.style.display = 'flex';
    } else {
      banner.classList.remove('hidden');
    }
    fillBuildLine(detail);
  }

  // ------------------------------------------------------------------
  // The build line — "which version am I on, which one is coming"
  // ------------------------------------------------------------------
  //
  // "Update available" answers neither question, and the answer is not
  // one the server can render: the shell knows the build it was DELIVERED
  // on (its own <meta>), and only /api/version knows the build the reload
  // will land on. So the line is assembled here, from both halves, and
  // the partial ships the slot for it empty (`detail_id`).
  //
  // Timestamps are shown as absolute local times rather than ages ("2
  // hours ago"). `relative_time.js` already owns that arithmetic, but it
  // is loaded on the three pages that render observation rows, and this
  // banner is on every page including the admin — so relative ages here
  // would mean a second copy of its unit ladder. `toLocaleString` needs
  // no ladder and no module, and for a build stamp the absolute instant
  // is the more precise answer anyway.

  /** The build this shell was delivered on, and when it was released.
   * Both from <meta> in public/base.html; both empty on an admin page,
   * where the line degrades to nothing rather than to half a comparison.
   * @type {string}
   */
  const SHELL_BUILD = readMeta('pwa-app-version');
  /** @type {string} */
  const SHELL_RELEASED_AT = readMeta('pwa-app-released-at');

  /** Latch — set only once a line has actually been WRITTEN, so a reveal
   * that had nothing to say (no verdict yet, or a server serving the build
   * this shell already has) doesn't block a later one that does.
   * @type {boolean}
   */
  let buildLineFilled = false;
  /** Separate latch for the probe: one `/api/version` round trip per page,
   * however many times a replayed cached response re-reveals the banner.
   * @type {boolean}
   */
  let buildLineProbed = false;

  /**
   * Read a `content` value from a `<meta name="…">` tag, or `""`.
   *
   * @param {string} name
   * @returns {string}
   */
  function readMeta(name) {
    const el = document.querySelector(`meta[name="${name}"]`);
    return el ? (el.getAttribute('content') || '').trim() : '';
  }

  /**
   * Shorten a build identifier for display.
   *
   * `APP_VERSION` resolves to a full git SHA on a deployed environment and
   * to something short and human ("dev") elsewhere. Forty hex characters
   * in a toast is noise, and the leading seven identify the commit as well
   * as GitHub's own abbreviation does; anything that is not a SHA is left
   * exactly as it is, since we have no idea what it means.
   *
   * @param {string} value
   * @returns {string}
   */
  function shortBuild(value) {
    const build = String(value || '').trim();
    return /^[0-9a-f]{12,40}$/i.test(build) ? build.slice(0, 7) : build;
  }

  /** @type {Intl.DateTimeFormatOptions} */
  const DATE_PARTS = { day: 'numeric', month: 'short' };
  /** @type {Intl.DateTimeFormatOptions} */
  const TIME_PARTS = { hour: '2-digit', minute: '2-digit' };

  /**
   * Format an ISO-8601 instant as a short local date and time, or `""`
   * when it is absent or unparseable — a build stamp we cannot date is
   * shown without a date rather than with "Invalid Date" beside it.
   *
   * @param {string} iso
   * @returns {string}
   */
  function stampTime(iso) {
    const at = Date.parse(String(iso || '').trim());
    if (Number.isNaN(at)) return '';
    const when = new Date(at);
    // Date and time formatted separately and joined with a space, rather
    // than one `toLocaleString` call: the combined form inserts the
    // locale's date/time separator ("27 Aug, 09:12"), and two of those
    // commas inside a line that already carries brackets and an arrow is
    // more punctuation than a build stamp can carry. Each half is still
    // the locale's own — only the joint is ours.
    return `${localise(when, DATE_PARTS)} ${localise(when, TIME_PARTS)}`;
  }

  /**
   * Format one instant in the document's language, falling back to the
   * platform default — an unparseable `lang` tag is not worth losing the
   * stamp over.
   *
   * @param {Date} when
   * @param {Intl.DateTimeFormatOptions} parts
   * @returns {string}
   */
  function localise(when, parts) {
    const lang = document.documentElement.getAttribute('lang') || undefined;
    try {
      return when.toLocaleString(lang, parts);
    } catch (_err) {
      return when.toLocaleString(undefined, parts);
    }
  }

  /**
   * One side of the comparison: `a1b2c3d` or `a1b2c3d (27 Aug 09:12)`.
   *
   * @param {string} build
   * @param {string} releasedAt
   * @returns {string}
   */
  function stampBuild(build, releasedAt) {
    const short = shortBuild(build);
    const when = stampTime(releasedAt);
    return when ? `${short} (${when})` : short;
  }

  /**
   * Compose the whole line, or `""` when there is nothing honest to show.
   *
   * Both halves are required: naming the build you are on without naming
   * the one you are moving to is not a delta, and the reverse is worse —
   * a lone identifier reads as the version you are about to get and might
   * equally be the one you already have. Identical builds are the same
   * case: the banner is up because a WORKER is waiting, the server is
   * serving what this shell already has, and a line saying "abc1234 →
   * abc1234" would flatly contradict it.
   *
   * @param {string} nextBuild
   * @param {string} nextReleasedAt
   * @returns {string}
   */
  function composeBuildLine(nextBuild, nextReleasedAt) {
    const from = shortBuild(SHELL_BUILD);
    const to = shortBuild(nextBuild);
    if (!from || !to || from === to) return '';
    return `${stampBuild(SHELL_BUILD, SHELL_RELEASED_AT)} → ${stampBuild(
      nextBuild,
      nextReleasedAt,
    )}`;
  }

  /**
   * Fill and unhide the banner's build line.
   *
   * Given a verdict, uses it. Given none — the SW-driven reveal, which
   * knows a worker is waiting and nothing about the server — it borrows
   * `pwa_version_check.js`'s probe rather than growing a second copy of
   * the same `/api/version` round trip. Read lazily off `window` for
   * exactly that reason: this file loads first, and the global does not
   * exist yet at IIFE time.
   *
   * Every failure is silent and leaves the line hidden. The banner's job
   * is to offer the update; the build line is an explanation of it, and
   * an explanation that cannot be produced is not worth a broken one.
   *
   * Two latches, not one: `buildLineFilled` is set only when a line was
   * actually written, so a reveal with nothing to say leaves the next one
   * free to speak, while `buildLineProbed` caps the round trips at one
   * per page however often the banner is re-revealed.
   *
   * @param {{current: string, released_at: string} | undefined} detail
   * @returns {void}
   */
  function fillBuildLine(detail) {
    if (buildLineFilled) return;
    const el = document.getElementById('sw-update-banner-versions');
    if (!el) return;

    if (detail && detail.current) {
      writeBuildLine(el, composeBuildLine(detail.current, detail.released_at));
      return;
    }

    if (buildLineProbed) return;
    const probe = window.pwaVersionProbe;
    if (typeof probe !== 'function') return;
    buildLineProbed = true;
    probe()
      .then((verdict) => {
        if (!verdict) return;
        writeBuildLine(el, composeBuildLine(verdict.current, verdict.released_at));
      })
      .catch(() => {
        // Unreachable endpoint — the banner stands without the line.
      });
  }

  /**
   * Write the composed line into the slot, revealing it only if there is
   * something in it.
   *
   * @param {HTMLElement} el
   * @param {string} text
   * @returns {void}
   */
  function writeBuildLine(el, text) {
    if (!text) return;
    // No suppression needed and none wanted: `text` is build identifiers,
    // an arrow and a timestamp the platform localised — there is no prose
    // in it. The word that explains what the line IS lives in the
    // partial's `detail_label`, where the catalogue can see it.
    el.textContent = text;
    el.hidden = false;
    buildLineFilled = true;
  }

  // SNOW-623: the banner has one owner. `pwa_version_check.js` reveals the
  // same element when the server declares a version drift, and used to
  // carry its own copy of the reveal — with a docstring instructing the
  // reader to "mirror the same fork sw_register.js uses in
  // showUpdateBanner", which describes a copy rather than a delegation.
  //
  // Only the DOM half is shared. `showUpdateBanner` above also latches the
  // waiting worker and emits `pwa.sw.update_available`, neither of which
  // the version-check path has or wants: there is no waiting worker when
  // the drift is a server header rather than a new SW script.
  window.pwaUpdateBanner = Object.freeze({
    reveal: revealUpdateBanner,
    hide: hideUpdateBanner,
  });

  if (banner) {
    // Reload button is queried by ID on the public partial and on the
    // self-injected admin fallback — both variants use the same ID so this
    // single lookup covers them.
    document
      .getElementById('sw-update-banner-reload')
      ?.addEventListener('click', handleReloadClick);
    banner
      .querySelector('[data-action="dismiss"]')
      ?.addEventListener('click', hideUpdateBanner);
  }

  /**
   * Drop the SW shell caches, then reload. Clearing first means the
   * navigation goes to the network for fresh HTML with the current
   * APP_VERSION baked into ``<meta>`` — without it, ``_networkFirst``'s
   * runtime cache could hand back HTML carrying the stale
   * ``pwa-app-version`` meta tag (the original reload-loop bug). Bulletin
   * JSON and other network-only paths are unaffected; the shell caches
   * are the only thing that would keep the stale meta tag alive.
   *
   * Scoped to ``snowdesk-shell-*`` / ``map-shell-*`` on purpose: the
   * user's pinned basemap buckets, and everything in IndexedDB, are not
   * code and survive a code update. SNOW-609 made this the forced-update
   * path's wipe too (see the ``window.pwaClearShellCachesAndReload``
   * export below).
   *
   * @returns {Promise<void>}
   */
  async function clearShellCachesAndReload() {
    try {
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(
          keys
            .filter(
              (k) =>
                k.startsWith('snowdesk-shell-') || k.startsWith('map-shell-'),
            )
            .map((k) => caches.delete(k)),
        );
      }
    } catch (_err) {
      // Cache API unavailable / eviction race — reload anyway.
    }

    window.location.reload();
  }

  // SNOW-609: exported so ``pwa_version_check.js``'s forced-update path
  // reuses this wipe rather than carrying a third copy of one (the others
  // being ``pwa_reset.js``'s everything-goes reset and ``sw-kill.js``'s
  // activate-time sweep). Non-writable / non-configurable, matching
  // ``window.pwaResetLocalData`` (pwa_reset.js) — the named export is
  // deliberate so a third-party script can't swap the implementation.
  //
  // This script is loaded before ``pwa_version_check.js`` (load order is
  // documented in ``pwa_client_version.js``), so the global is present by
  // the time that module binds its modal handler. The one exception is a
  // browser with no ``navigator.serviceWorker``, where this IIFE returns
  // before reaching here — hence the plain-reload fallback on the calling
  // side.
  Object.defineProperty(window, 'pwaClearShellCachesAndReload', {
    value: clearShellCachesAndReload,
    writable: false,
    configurable: false,
  });

  /**
   * Put the banner into its working state, the moment the click lands.
   *
   * The click used to change nothing on screen. Both reload paths are
   * asynchronous — the SW path posts SKIP_WAITING and waits for the new
   * worker to activate, backstopped by a three-second timer; the
   * version-header path enumerates and deletes Cache Storage entries
   * first — so between the press and the reload there was a stretch of
   * seconds in which the only feedback was a button that had stopped
   * responding. That reads as a dead control, and the natural response to
   * a dead control is to press it again.
   *
   * Three things change, in the order the eye finds them: the CTA says
   * what it is doing and is visibly disabled (`disabled:opacity-60`,
   * `disabled:cursor-not-allowed` — see `_button_chrome_classes` in
   * apps/public/templatetags/components.py), the banner's refresh icon
   * spins, and the copy switches from an offer to a progress report.
   *
   * No state is restored afterwards, deliberately: every path out of here
   * ends in `location.reload()`, so the busy banner's successor is a
   * fresh document. A restore would only ever run if the reload itself
   * failed, and a banner that quietly went back to "Update available"
   * would be claiming the update had not started when it had.
   *
   * The icon spin is public-page only — the admin fallback banner has no
   * icon to spin and no Tailwind to spin it with. The label and copy
   * changes, which are the load-bearing half, land on both.
   *
   * @returns {void}
   */
  function showBannerBusy() {
    const btn = document.getElementById('sw-update-banner-reload');
    if (btn) {
      btn.setAttribute('aria-busy', 'true');
      btn.textContent = STRINGS.updating;
    }
    const title = document.getElementById('sw-update-banner-title');
    if (title) title.textContent = STRINGS['updating-title'];
    const body = document.getElementById('sw-update-banner-body');
    if (body) body.textContent = STRINGS['updating-body'];
    const icon = banner && banner.querySelector('[data-overlay-icon]');
    // `motion-safe:` so the spin honours prefers-reduced-motion; the copy
    // and the disabled CTA carry the message on their own without it.
    if (icon) icon.classList.add('motion-safe:animate-spin');
  }

  /**
   * Reload click handler. Handles both the SW-driven path (a fresh worker
   * is waiting) and the version-header-driven path (the server's
   * ``X-App-Version`` drifted from the shell's ``<meta>`` but ``sw.js`` did
   * not change so no worker is waiting).
   *
   * The waiting worker is re-resolved from the live registration rather
   * than trusted from the reference captured when the banner was shown:
   * that worker can have gone redundant since (superseded by a newer
   * install, a failed update, or browser eviction), and ``postMessage`` to
   * a redundant worker is a silent no-op — the click would do nothing and
   * the busy-latch below would leave the button dead. For the same
   * reason, the SW path arms a fallback timer: if ``controllerchange``
   * hasn't fired shortly after ``SKIP_WAITING`` was posted, fall back to
   * the cache-clearing reload so the click ALWAYS lands on a fresh shell.
   *
   * SNOW-609: this used to also clear ``pwa.update.first_shown_at``, the
   * stamp behind the 24h escalation to the blocking modal. That
   * escalation is gone — it blocked the app on elapsed time rather than
   * on any server statement that the build was unacceptable — and the
   * stamp is no longer written, so there is nothing to clear.
   */
  async function handleReloadClick() {
    const btn = document.getElementById('sw-update-banner-reload');
    if (btn) {
      if (btn.dataset.busy === '1') return;
      btn.dataset.busy = '1';
      btn.setAttribute('disabled', 'disabled');
    }
    showBannerBusy();

    userTriggeredUpdate = true;

    // Prefer the registration's live waiting worker; the captured
    // reference is only a fallback and only while still actually waiting
    // (state 'installed' — anything else can no longer be activated).
    let waiting =
      waitingWorker && waitingWorker.state === 'installed'
        ? waitingWorker
        : null;
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration && registration.waiting) {
        waiting = registration.waiting;
      }
    } catch (_err) {
      // getRegistration failure — proceed with what we have.
    }

    if (waiting) {
      // SW-driven path: activation fires ``controllerchange`` which
      // triggers the guarded reload below.
      waiting.postMessage({ type: 'SKIP_WAITING' });
      // Safety net: a worker that never activates (stuck install, killed
      // SW process) must not strand the user on a disabled button. The
      // cache-clearing reload is safe even if activation later succeeds —
      // ``refreshing`` ensures whichever path runs first wins and the
      // other becomes a no-op.
      setTimeout(() => {
        if (refreshing) return;
        refreshing = true;
        clearShellCachesAndReload();
      }, 3000);
      return;
    }

    // Version-header-driven path (no worker waiting).
    await clearShellCachesAndReload();
  }

  // The new worker called ``clients.claim()`` and now controls the page.
  // Reload once so the new shell renders — but only if this came from the
  // user accepting the update, so a first-install claim doesn't bounce
  // the page on someone's very first visit.
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!userTriggeredUpdate || refreshing) return;
    refreshing = true;
    // SNOW-384: best-effort — the reload immediately below can tear the
    // page down before the async emit() (IndexedDB write) settles. This
    // mirrors the same race telemetry.js accepts for other reload-adjacent
    // events; there is no synchronous alternative here.
    try {
      window.pwaTelemetry?.emit('pwa.sw.update_applied', {});
    } catch (_err) {
      // Ignore — telemetry must never block the reload.
    }
    window.location.reload();
  });

  /**
   * Watch a service worker for the install→installed transition. When
   * it lands on ``installed`` and there is still an existing controller
   * on the page, that means an update is ready (the existing controller
   * is the OLD SW; this newly-installed one is the new shell).
   *
   * @param {ServiceWorker} sw
   */
  function watchForInstall(sw) {
    sw.addEventListener('statechange', () => {
      if (sw.state === 'installed' && navigator.serviceWorker.controller) {
        showUpdateBanner(sw);
      }
    });
  }

  /**
   * Ask the server whether the SW should be registered and, if so, from
   * which URL. See the header comment for the two branches. Failure to
   * reach the endpoint falls back to registering the default ``/sw.js``.
   *
   * @returns {Promise<{sw_url: string, kill: boolean}>}
   */
  async function fetchSwConfig() {
    try {
      const res = await fetch('/api/sw-config', { cache: 'no-store' });
      if (!res.ok) throw new Error('sw-config non-2xx: ' + res.status);
      const json = await res.json();
      return {
        sw_url: typeof json.sw_url === 'string' ? json.sw_url : '/sw.js',
        kill: json.kill === true,
      };
    } catch (_err) {
      return { sw_url: '/sw.js', kill: false };
    }
  }

  fetchSwConfig().then((config) => {
    if (config.kill) {
      // SNOW-384: Mechanism A's kill decision — this IS the "pre-register
      // kill fetch" point: the client learns to kill before it has ever
      // registered a SW this session. Critical event (CRITICAL_EVENTS in
      // telemetry.js) so it fires via sendBeacon immediately.
      try {
        window.pwaTelemetry?.emit('pwa.kill_switch.activated', {
          mechanism: 'a',
        });
      } catch (_err) {
        // Ignore — telemetry must never block the kill switch.
      }
      // Mechanism A activated. Unregister every SW on this origin so the
      // next navigation runs without a controller. Caches are deliberately
      // left alone: that is the kill-switch SW's job when the flip goes
      // through Mechanism B (``/sw-kill.js``), and not wiping a user's
      // deliberate 500 MB of downloaded basemaps because ops flipped a
      // temporary switch is the right trade.
      //
      // SNOW-615: this used to say "the user can hard-refresh to clear
      // anything else". A hard refresh does not clear Cache Storage, and
      // unregistering the worker also removes the only worker-side reaper
      // (the activate sweep) — so that sentence described a recovery that
      // does not exist. Nothing is permanently stranded: the page-side
      // paths still work (``pwa_reset.js``, ``clearShellCachesAndReload``
      // below, and ``evictBasemapAreas`` in map.js).
      navigator.serviceWorker
        .getRegistrations()
        .then((regs) => Promise.all(regs.map((r) => r.unregister())))
        .catch((err) => console.error('[sw] kill unregister failed:', err));
      return;
    }

    navigator.serviceWorker
      .register(config.sw_url, { scope: '/', updateViaCache: 'none' })
      .then((registration) => {
        // Three entry points to "an update is ready":
        //   1. ``waiting`` is non-null at register-time — a new worker has
        //      already installed and is parked waiting (the common case
        //      now that we don't auto-skipWaiting).
        //   2. ``installing`` is non-null at register-time — a SW update
        //      check started before our register() resolved.
        //   3. ``updatefound`` fires later — the common case during a
        //      normal session where the SW changes on the next deploy.
        if (registration.waiting && navigator.serviceWorker.controller) {
          showUpdateBanner(registration.waiting);
        }
        if (registration.installing) {
          watchForInstall(registration.installing);
        }
        registration.addEventListener('updatefound', () => {
          if (registration.installing) {
            watchForInstall(registration.installing);
          }
        });

        // Surface an update promptly for a tab left open across a deploy:
        // re-check when the tab regains focus. ``update()`` is a no-op when
        // the SW script is unchanged — so no banner means you really are
        // latest.
        //
        // SNOW-622: throttled. ``update()`` is a network round trip for the
        // worker script (``/sw.js`` is ``Cache-Control: no-cache``, so it
        // revalidates every time), and visibilitychange fires on every
        // tab switch, every app switch on mobile, and every screen unlock.
        // A user flicking between apps paid one request each way. A deploy
        // the user has not noticed in the last minute is not more urgent
        // than one they have.
        let lastUpdateCheck = 0;
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState !== 'visible') return;
          const now = Date.now();
          if (now - lastUpdateCheck < SW_UPDATE_CHECK_INTERVAL_MS) return;
          lastUpdateCheck = now;
          registration.update().catch(() => {});
        });
      })
      .catch((err) => {
        console.error('[sw] registration failed:', err);
      });
  });
})();
