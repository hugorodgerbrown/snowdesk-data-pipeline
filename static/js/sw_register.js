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
 * Update flow (SNOW-1025)
 * -----------------------
 * The contract, end-user-facing: *a deploy asks nothing of you.* Updates
 * apply on their own, out of sight. The one message anyone sees, "Snowdesk
 * needs a refresh", appears only when the service worker is stuck and
 * cannot update without help.
 *
 * **Routine update: silent.** The PWA shell SW (``static/js/sw.js``) does
 * NOT ``skipWaiting()`` on install, so a new worker parks in "waiting".
 * This script records it (``queueSilentUpdate``) and posts
 * ``{ type: 'SKIP_WAITING' }`` to it. When depends on the page:
 *
 *   * If the waiting worker holds the same shell as this page's
 *     ``<meta name="pwa-shell">`` (SNOW-1027), post at once. The page came
 *     off the network as that worker's build; only the old worker is out of
 *     date. This is the fresh-tab case, where opening the tab is what
 *     installed the new worker.
 *   * Otherwise, post the next time the page is hidden (tab switch, app
 *     switch, screen lock). The page was served by the old worker and may
 *     still read the old shell cache that activation sweeps.
 *
 * Either way, an in-flight ``warmCache`` run holds it back: the worker
 * that owns a basemap download must not be retired halfway through it. The new worker claims the page;
 * the ``controllerchange`` that follows does NOT reload. The open page
 * keeps running and its next navigation lands on the new shell.
 *
 * **Stuck worker: the banner.** ``pwa_version_check.js`` notices when the
 * server has moved on (an ``X-App-Version`` drift, confirmed by
 * ``/api/version``) and offers the banner through
 * ``window.pwaUpdateBanner.reveal``. Two gates in series decide it:
 *
 *   1. ``shellIsStale`` (SNOW-952): does the worker in control hold a
 *      different shell from the one the server would serve today? If not,
 *      there is nothing to update.
 *   2. ``workerIsStuck`` (SNOW-1025): can the browser fix that on its own?
 *      It asks the registration for an update and waits for any installing
 *      worker to settle. A worker that reaches "waiting" will be applied
 *      silently, so it is not stuck. A worker that goes "redundant" (one
 *      bad precache entry rejects ``install``'s atomic ``cache.addAll``),
 *      or no new worker at all, is stuck. That is the one case where a
 *      person has to act.
 *
 * SNOW-952's gate alone was not enough. It is true after every deploy that
 * touches a shell source, which is most of them, since the shell hash
 * covers every ``static/js`` file. So the banner kept interrupting people
 * during ordinary use.
 *
 * The copy is plain (SNOW-1025): no build SHAs, no release labels. The
 * versioned copy (SNOW-869) and the controller-identity labelling
 * (SNOW-933) are gone, and with them the build identity ``serve_sw`` used
 * to bake into the worker. That identity made the worker's bytes change on
 * every deploy, Python-only ones included.
 *
 * Clicking "Reload" runs ``handleReloadClick`` below:
 *
 *   * If a fresh SW is waiting → post ``{ type: 'SKIP_WAITING' }``. The
 *     worker activates, calls ``clients.claim()``, and the browser fires
 *     ``controllerchange``. Because the user asked for it, we reload the
 *     page exactly once (guarded by ``refreshing``) onto the new shell.
 *
 *   * If no worker is waiting (the stuck case) → clear the SW's shell
 *     caches and reload. Without clearing, the SW's ``_networkFirst``
 *     navigate handler can hand back cached HTML carrying the stale
 *     ``pwa-app-version`` meta tag, so the version check would re-show the
 *     banner and the user would be stuck in a reload loop.
 *
 *   Either way the click always ends in a reload: the waiting worker is
 *   re-resolved from the live registration (a captured reference can go
 *   redundant, and messaging a redundant worker is a silent no-op), and a
 *   fallback timer downgrades to the cache-clearing reload if activation
 *   never fires ``controllerchange``.
 *
 *   Either way it also takes a moment, so the click is acknowledged on
 *   screen before any of it starts. See ``showBannerBusy``.
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

  // SNOW-1027: the shell cache name this page was built against, the same
  // CACHE_VERSION serve_sw injects into sw.js (base.html renders it). A
  // waiting worker reporting this same name holds exactly this page's
  // build, so it can take over at once. Empty on pages that do not extend
  // public/base.html (the admin), which then keep the hide-to-apply rule.
  const PAGE_SHELL = (
    document.querySelector('meta[name="pwa-shell"]')?.getAttribute('content') || ''
  ).trim();

  // SNOW-620: copy for the self-injected fallback banner (ensureBanner
  // below), which is what admin pages get — they don't include the public
  // chrome, so includes/_sw_update_banner.html and its already-translated
  // strings aren't there. Rendered into a strings template by
  // admin/base_site.html; the literals are the English fallback, and are
  // deliberately the same three strings that partial already translates.
  // See static/js/i18n_strings.js.
  const STRINGS = self.pwaStrings.read('sw-update-strings-template', {
    'update-title': 'Snowdesk needs a refresh',
    'update-body': "It couldn't update itself. Reload to finish.",
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

  // The installed-but-waiting worker (the pending update), captured by
  // ``queueSilentUpdate`` so ``applyWaitingWorker`` and the Reload handler
  // can message it.
  let waitingWorker = null;
  // Set true only when the user clicks "Reload". A ``controllerchange`` is
  // turned into a reload only then: a first-install ``clients.claim()`` and
  // a silent activation (SNOW-1025) both fire the same event, and neither
  // may move the page under the user.
  let userTriggeredUpdate = false;
  // SNOW-1025: set when ``applyWaitingWorker`` has posted SKIP_WAITING, so
  // the ``controllerchange`` that follows is recorded as an applied update
  // even though it does not reload.
  let silentUpdatePosted = false;
  // SNOW-1027: a waiting worker whose shell matches this page's, remembered
  // when a basemap download held it back, so the end of the download can
  // apply it without waiting for the page to be hidden.
  let matchedWorker = null;
  // Guards against a double reload if controllerchange fires more than
  // once.
  let refreshing = false;
  // SNOW-384 / SNOW-1025: ``pwa.sw.update_available`` counts stuck-worker
  // banners actually shown, once per page.
  let announcedStuck = false;
  // SNOW-952 / SNOW-1025: the banner decision, remembered against the
  // server shell it answered. ``pwa_version_check.js`` re-offers the banner
  // for every response replaying a drifting version header, and each
  // answer costs a worker round trip and a ``registration.update()``.
  // Keyed rather than latched, because a second deploy in one session names
  // a different shell and needs its own answer. Holds the PROMISE, so
  // overlapping offers share one evaluation. Cleared on ``controllerchange``:
  // since SNOW-1025 the controller can change under a live page (a silent
  // activation) and the old answer described the old worker.
  /** @type {{shell: string, show: Promise<boolean>} | null} */
  let bannerAnswer = null;

  // SNOW-492: slot for the in-flight "Cache this area" call, if any. Only
  // one warm-cache run can be in flight at a time — see ``_warmCacheChain``
  // below, which is what now makes that true — so a single slot is enough.
  //
  // SNOW-493 finding 9: the slot now carries a ``requestId`` alongside the
  // ``resolve`` function. Without it, a worker reply that arrives AFTER
  // ``warmCache()`` has already given up (timed out and resolved ``null``)
  // would still find ``_warmCacheSlot.resolve`` set — a later call's own
  // ``resolve`` — and wrongly settle it with the stale reply's numbers.
  // Correlating on ``requestId`` (minted per call, echoed back by the
  // worker) means a reply only ever resolves the call it actually answers.
  let _warmCacheSlot = null;

  // SNOW-951 review: the queue that keeps the single slot above honest.
  //
  // "Only one run at a time" was a claim about the CALLERS, and it held
  // only while one click-guarded button was the sole entry point. It is
  // not true any more: a per-area "Sync now" runs two warm calls of its
  // own (tiles, then content), several areas can be pressed before the
  // first settles, and the offline report warms the shell on its own
  // schedule. A second call landing mid-run overwrote ``requestId``, so
  // the worker's reply to the FIRST no longer matched the slot, and that
  // caller sat out the full silence timeout before resolving
  // ``'timeout'`` — a sync reported as failed while its documents were
  // landing on disk.
  //
  // Serialised rather than multi-slotted, because the constraint is not
  // only the slot: ``sw.js``'s ``_warmCache`` is itself one run with one
  // progress stream, and overlapping runs would interleave their
  // ``settled`` batches. Queueing costs a caller the wait for whatever is
  // ahead of it, which is the truthful price — the work was never going
  // to happen in parallel.
  //
  // The chain only ever holds a SETTLED-or-pending promise that cannot
  // reject: ``_warmCacheRun`` resolves on every path (including its own
  // timeout), and the ``catch`` below is belt and braces so one throw can
  // never wedge every later call.
  let _warmCacheChain = Promise.resolve();

  // SNOW-1025: how many ``warmCache`` calls are queued or running. A silent
  // activation waits for this to reach zero: activating retires the worker
  // that is doing the download, and the new worker knows nothing about the
  // run. Counted at the call rather than read from ``_warmCacheSlot``,
  // because a queued call holds no slot while it waits its turn.
  let _warmCacheRunsPending = 0;

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
   * SNOW-951 review: calls QUEUE. The worker runs one warm at a time and
   * this module tracks it in one slot, so a second caller waits for the
   * first to settle rather than overwriting it — see ``_warmCacheChain``.
   * Nothing about a single call's own result changes; what changes is that
   * a caller who arrives during another's run gets its own answer instead
   * of that run's silence timeout.
   *
   * @param {string[]} urls
   * @param {{pinned?: boolean, areaId?: string, onProgress?: (done: number,
   *   total: number, settled?: number[], bytes?: number) => void}} [opts]
   * @returns {Promise<{ok: number, failed: number, reason: string|null,
   *   bytes: number, cancelled: boolean} | null>}
   */
  async function warmCache(urls, opts) {
    _warmCacheRunsPending += 1;
    const mine = _warmCacheChain.then(() => _warmCacheRun(urls, opts));
    _warmCacheChain = mine.then(
      () => undefined,
      () => undefined,
    );
    // SNOW-1025: the last run to finish releases any update that was held
    // back for it, if the page went hidden in the meantime.
    const release = () => {
      _warmCacheRunsPending -= 1;
      if (document.visibilityState === 'hidden') applyWaitingWorker();
      else if (matchedWorker) applyWaitingWorker(matchedWorker);
    };
    mine.then(release, release);
    return mine;
  }

  /**
   * One warm-cache run, dispatched with the slot to itself.
   *
   * Split out of ``warmCache`` by the SNOW-951 review so the queueing
   * above wraps the WHOLE of a run — the controller wait included, since
   * a call that takes the slot before waiting for a controller would hold
   * it while doing nothing.
   *
   * @param {string[]} urls
   * @param {{pinned?: boolean, areaId?: string, glyphPrefix?: string,
   *   onProgress?: (done: number, total: number, settled?: number[],
   *   bytes?: number) => void}} [opts]
   * @returns {Promise<{ok: number, failed: number, reason: string|null,
   *   bytes: number, cancelled: boolean} | null>}
   */
  async function _warmCacheRun(urls, opts) {
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
        // SNOW-742: the active basemap style's glyph URL prefix. The worker
        // uses it to promote already-cached glyphs into the pinned bucket, so
        // a downloaded area keeps its labels once the passive cache trims its
        // copies. Undefined for a non-pinned call.
        glyphPrefix: options.glyphPrefix,
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
   * property (admin fallback) via ``revealUpdateBanner`` /
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
    copy.append(title, sub);
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
   * Remember a waiting worker and apply it silently at the next chance
   * (SNOW-1025, SNOW-1027).
   *
   * A waiting worker is a routine update, not a problem, and nobody needs to
   * be told about it. When it is applied depends on whether this page
   * already is that worker's build:
   *
   *   * **It is** (SNOW-1027): the worker reports the shell this page's
   *     ``<meta name="pwa-shell">`` names. The page came off the network
   *     (navigations are network-first) and the old worker is the only
   *     thing out of date, which is exactly what happens on a fresh tab:
   *     opening it is what installed the new worker. Apply it now, visible
   *     or not. Nothing on screen changes, and the page and its worker stop
   *     disagreeing from the first second rather than for a whole session.
   *   * **It is not, or the worker does not answer**: the page was served
   *     by the old worker, usually offline, and may still read the old shell
   *     cache that activation sweeps. Apply the next time the page is hidden
   *     (SNOW-1025). If it is already hidden (an update check that finished
   *     in a background tab), that is now.
   *
   * @param {ServiceWorker} worker The installed, waiting worker.
   * @returns {void}
   */
  function queueSilentUpdate(worker) {
    if (worker) waitingWorker = worker;
    if (document.visibilityState === 'hidden') {
      applyWaitingWorker();
      return;
    }
    if (!worker || !PAGE_SHELL) return;
    workerShell(worker).then((shell) => {
      if (shell !== PAGE_SHELL) return;
      matchedWorker = worker;
      applyWaitingWorker(worker);
    });
  }

  /**
   * Post SKIP_WAITING to the waiting worker, if it is safe to (SNOW-1025).
   *
   * Two callers, two rules:
   *
   *   * With no argument, the hide-to-apply path: only while the page is
   *     hidden. The waiting worker is re-read from the live registration
   *     rather than trusted from ``waitingWorker``, for the reason
   *     ``handleReloadClick`` gives: a captured reference can have gone
   *     redundant, and posting to a redundant worker is a silent no-op.
   *   * With ``matched``, a worker ``queueSilentUpdate`` has just confirmed
   *     holds this page's shell (SNOW-1027): applied whether or not the page
   *     is visible, but only while it is still the worker waiting. A newer
   *     install that has since replaced it was not checked, so it falls back
   *     to the hide-to-apply rule.
   *
   * Both are held back while a ``warmCache`` run is queued or in flight:
   * activating retires the worker doing the download. ``warmCache`` calls
   * back in here when its last run settles.
   *
   * Both are also held back while ANY other window is on screen or running
   * a download (``activationIsSafe``). Activation is origin-wide, so this
   * page's own state is not enough. That window gets its own turn: it runs
   * this same function when it is hidden or its download ends, and the last
   * window to go quiet applies the update.
   *
   * Posting SKIP_WAITING to a worker that is already activating is
   * harmless. ``skipWaiting()`` resolves at once on a worker that is not
   * waiting.
   *
   * @param {ServiceWorker} [matched] A waiting worker known to hold this
   *   page's shell.
   * @returns {Promise<void>}
   */
  async function applyWaitingWorker(matched) {
    if (_warmCacheRunsPending > 0) return;
    let waiting =
      waitingWorker && waitingWorker.state === 'installed' ? waitingWorker : null;
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      if (registration && registration.waiting) waiting = registration.waiting;
    } catch (_err) {
      // getRegistration failure: proceed with what we have.
    }
    if (!waiting) return;
    // Re-checked after the await: a download can have started, or the page
    // come back into view, while the registration was being read.
    if (_warmCacheRunsPending > 0) return;
    const immediate = matched !== undefined && waiting === matched;
    if (!immediate && document.visibilityState !== 'hidden') return;
    // Every window, not just this one: another may be on screen or mid-download.
    if (!(await activationIsSafe())) return;
    // Re-checked after that await too.
    if (_warmCacheRunsPending > 0) return;
    if (!immediate && document.visibilityState !== 'hidden') return;
    matchedWorker = null;
    silentUpdatePosted = true;
    try {
      waiting.postMessage({ type: 'SKIP_WAITING' });
    } catch (_err) {
      // Called from event handlers with nobody awaiting it, so a throw here
      // would be an unhandled rejection. The next hide tries again.
      silentUpdatePosted = false;
    }
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
   * @returns {void}
   */
  function revealUpdateBanner() {
    if (DEV_SHELL_BYPASS_ACTIVE) return;
    if (!banner) return;
    if (banner.dataset.fallback === '1') {
      banner.style.display = 'flex';
    } else {
      banner.classList.remove('hidden');
    }
  }

  /**
   * How long to wait for a worker to name its shell.
   *
   * The reply is a synchronous read of a constant in the worker's own
   * scope, so a live worker answers in single-digit milliseconds. The
   * budget is for the worker that will never answer: one that predates the
   * ``shell-identity`` handler, or one wedged in a long ``waitUntil``.
   * Bounded rather than open-ended for the reason
   * docs/decisions/bounded-offline-read-paths.md gives: an unanswered read
   * must resolve, not hang.
   */
  const WORKER_SHELL_TIMEOUT_MS = 2000;

  /**
   * Send one question to a service worker down its own MessageChannel, and
   * resolve with the reply (SNOW-952, SNOW-1027).
   *
   * @param {ServiceWorker | null | undefined} worker
   * @param {string} type The message type; the reply must carry the same.
   * @returns {Promise<Object | null>} The reply's data, or ``null`` when there
   *   is no worker, no ``MessageChannel``, a reply of another type, or no
   *   reply inside ``WORKER_SHELL_TIMEOUT_MS``.
   */
  function askWorker(worker, type) {
    return new Promise((resolve) => {
      if (!worker || typeof MessageChannel !== 'function') {
        resolve(null);
        return;
      }
      /** @type {MessageChannel} */
      let channel;
      try {
        channel = new MessageChannel();
      } catch (_err) {
        resolve(null);
        return;
      }
      let settled = false;
      /**
       * Resolve once, and close the port however we got here, so a page
       * re-offered the banner does not pile up live ports. ``canOpenOffline``
       * in pwa_network_mode.js closes its port the same way.
       *
       * @param {Object | null} value
       */
      const settle = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try {
          channel.port1.close();
        } catch (_err) {
          // Non-fatal.
        }
        resolve(value);
      };
      const timer = setTimeout(() => settle(null), WORKER_SHELL_TIMEOUT_MS);
      channel.port1.onmessage = (event) => {
        const data = event.data;
        settle(data && data.type === type ? data : null);
      };
      try {
        worker.postMessage({ type: type }, [channel.port2]);
      } catch (_err) {
        settle(null);
      }
    });
  }

  /**
   * Ask a service worker which shell cache it holds (SNOW-952, SNOW-1027).
   *
   * The worker answers ``shell-identity`` with its ``CACHE_VERSION``,
   * derived from the shell content hash. Asked of the controlling worker by
   * ``shellIsStale``, and of a waiting worker by ``queueSilentUpdate``: a
   * waiting worker's ``message`` handler runs like any other's, which is
   * what SKIP_WAITING already relies on.
   *
   * @param {ServiceWorker | null | undefined} worker
   * @returns {Promise<string>} The cache name, or ``''`` when the worker
   *   cannot be asked or does not answer. Both callers read ``''`` as
   *   "cannot tell", each in its own safe direction.
   */
  function workerShell(worker) {
    return askWorker(worker, 'shell-identity').then((data) =>
      data ? String(data.cache || '').trim() : '',
    );
  }

  /**
   * Is it safe, for every open window and not just this one, to replace the
   * controlling worker now? (SNOW-1027)
   *
   * Activation is origin-wide. It claims every window and sweeps the old
   * shell cache, so a decision this page makes alone can pull the worker out
   * from under another window that is still on screen, or retire the worker
   * that is running another window's basemap download (this page's own
   * ``_warmCacheRunsPending`` sees only its own). The controlling worker is
   * the one that can see all of it: it serves every window's downloads and
   * can list every window.
   *
   * @returns {Promise<boolean>} ``false`` when another window is visible or
   *   any download is running. ``true`` when neither, and also when the
   *   worker does not answer: a worker from before this check existed
   *   cannot be asked, and holding the update back until it could be would
   *   hold it back forever. That is the pre-SNOW-1027 behaviour, and it
   *   lasts one deploy.
   */
  function activationIsSafe() {
    return askWorker(navigator.serviceWorker.controller, 'activation-check').then(
      (data) => !data || (!data.othersVisible && !data.warming),
    );
  }

  /**
   * Is this device holding an out-of-date offline shell? (SNOW-952)
   *
   * The first of the banner's two gates. The server names the shell it
   * would serve today (``shell`` on ``/api/version``), the controlling
   * worker names the one it holds, and a difference between them is the
   * question. A deploy that changed no shell source is never stale.
   *
   * Three answers rather than two, and the failure directions are
   * deliberate and opposite:
   *
   *   * **No controller** → ``false``. Nothing is cached, so nothing can
   *     be stale, and the page in front of the user came off the network.
   *   * **A controller that cannot be read**: no ``shell`` from the
   *     server, or no reply from the worker → ``true``. An unanswering
   *     worker is itself a symptom of the state the banner exists for.
   *     ``workerIsStuck`` still has to agree before anything is shown.
   *   * **Both readable** → the comparison.
   *
   * @param {string} server The server's shell cache name, ``''`` if absent.
   * @returns {Promise<boolean>}
   */
  function shellIsStale(server) {
    if (!navigator.serviceWorker || !navigator.serviceWorker.controller) {
      return Promise.resolve(false);
    }
    return workerShell(navigator.serviceWorker.controller).then(
      (held) => !server || !held || server !== held,
    );
  }

  /**
   * How long ``workerIsStuck`` waits for an installing worker to settle.
   *
   * An install precaches a handful of shell URLs, which takes seconds on a
   * poor connection. An install still running after this long is treated
   * as stuck, which fails toward offering the banner, as ``shellIsStale``
   * does.
   */
  const STUCK_INSTALL_TIMEOUT_MS = 30000;

  /**
   * Wait for an installing worker to finish, one way or the other.
   *
   * @param {ServiceWorker} worker
   * @returns {Promise<boolean>} ``true`` when it failed (``redundant``) or
   *   did not finish inside ``STUCK_INSTALL_TIMEOUT_MS``; ``false`` once it
   *   reaches ``installed`` or later.
   */
  function installFailed(worker) {
    return new Promise((resolve) => {
      const verdict = () => {
        if (worker.state === 'redundant') return true;
        if (worker.state === 'installing') return null;
        return false;
      };
      const now = verdict();
      if (now !== null) {
        resolve(now);
        return;
      }
      const onChange = () => {
        const next = verdict();
        if (next === null) return;
        clearTimeout(timer);
        worker.removeEventListener('statechange', onChange);
        resolve(next);
      };
      const timer = setTimeout(() => {
        worker.removeEventListener('statechange', onChange);
        resolve(true);
      }, STUCK_INSTALL_TIMEOUT_MS);
      worker.addEventListener('statechange', onChange);
    });
  }

  /**
   * Can the browser NOT bring this device's shell up to date on its own?
   * (SNOW-1025)
   *
   * The banner's second gate, asked only once ``shellIsStale`` has said
   * yes. A stale shell is normal for the minutes after any deploy that
   * touched a shell source: the new worker is still downloading, or it is
   * waiting for the page to be hidden so it can be applied silently. None
   * of that needs a person. What needs a person is a worker that cannot
   * install. ``install`` precaches with one atomic ``cache.addAll``, so a
   * single bad entry rejects it and no worker ever reaches "waiting".
   *
   *   * A worker already waiting → not stuck. It will be applied.
   *   * Otherwise, ask for an update. A worker that installs → not stuck.
   *     One that goes ``redundant``, or does not finish in time → stuck.
   *   * No worker appears at all, though the server's shell differs from
   *     the one held → stuck. The browser does not see the new worker, so
   *     nothing will change without a reload.
   *   * ``update()`` rejects, or there is no registration → stuck. The
   *     server has just answered ``/api/version``, so this is not a device
   *     that is simply offline.
   *
   * @returns {Promise<boolean>}
   */
  async function workerIsStuck() {
    let registration;
    try {
      registration = await navigator.serviceWorker.getRegistration();
    } catch (_err) {
      return true;
    }
    if (!registration) return true;
    if (registration.waiting) return false;
    try {
      await registration.update();
    } catch (_err) {
      return true;
    }
    if (registration.waiting) return false;
    if (registration.installing) return installFailed(registration.installing);
    return true;
  }

  /**
   * Reveal the banner if, and only if, the worker is stuck on a stale shell.
   *
   * The one entry point for the banner, published as
   * ``window.pwaUpdateBanner.reveal`` and called by ``pwa_version_check.js``
   * once ``/api/version`` confirms the server has moved on. Both gates live
   * here, so no caller can reveal the banner past them. ``revealNow`` is the
   * ungated DOM primitive underneath, which only this function and the
   * tests that exercise the banner's copy have any business calling.
   *
   * @returns {Promise<boolean>} Whether the banner was revealed.
   */
  function revealUpdateBannerIfStuck() {
    const info = window.pwaVersionInfo;
    const verified =
      info && typeof info.verified === 'function'
        ? Promise.resolve(info.verified()).catch(() => null)
        : Promise.resolve(null);
    return verified
      .then((verdict) => {
        const server = verdict && verdict.shell ? verdict.shell : '';
        if (bannerAnswer && bannerAnswer.shell === server) return bannerAnswer.show;
        const show = shellIsStale(server)
          .then((stale) => (stale ? workerIsStuck() : false))
          .catch(() => true);
        bannerAnswer = { shell: server, show: show };
        return show;
      })
      .then((show) => {
        if (!show) return false;
        revealUpdateBanner();
        if (!announcedStuck && !DEV_SHELL_BYPASS_ACTIVE) {
          announcedStuck = true;
          try {
            window.pwaTelemetry?.emit('pwa.sw.update_available', {});
          } catch (_err) {
            // Ignore — telemetry must never break the update banner.
          }
        }
        return true;
      });
  }

  // SNOW-623: the banner has one owner. `pwa_version_check.js` offers the
  // same element when the server declares a version drift, and used to
  // carry its own copy of the reveal. It now delegates here.
  //
  // `reveal` is the GATED entry point (SNOW-952, SNOW-1025): it reveals
  // only for a worker stuck on a stale shell. It keeps the name on purpose,
  // so the obvious thing to reach for is the safe one. `revealNow` is the
  // ungated DOM primitive. It exists for the tests that exercise the
  // banner's copy; calling it from a reveal path would put the interruption
  // straight back.
  window.pwaUpdateBanner = Object.freeze({
    reveal: revealUpdateBannerIfStuck,
    revealNow: revealUpdateBanner,
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

  // A new worker called ``clients.claim()`` and now controls the page.
  //
  // The answer ``revealUpdateBannerIfStuck`` remembered described the old
  // worker, so it is dropped whatever happens next.
  //
  // Reload once onto the new shell, but only if the user pressed Reload. A
  // first-install claim must not bounce someone's very first visit, and a
  // silent activation (SNOW-1025) must not move a page the user just came
  // back to. That page keeps running, and its next navigation lands on the
  // new shell. A banner still showing from before is taken down: the worker
  // it was about has just been replaced.
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    bannerAnswer = null;
    if (!userTriggeredUpdate) {
      if (!silentUpdatePosted) return;
      silentUpdatePosted = false;
      hideUpdateBanner();
      // SNOW-585: the dev bypass already serves fresh shell assets, so an
      // applied update describes nothing a user would have seen.
      if (DEV_SHELL_BYPASS_ACTIVE) return;
      try {
        window.pwaTelemetry?.emit('pwa.sw.update_applied', {});
      } catch (_err) {
        // Ignore — telemetry must never break the update flow.
      }
      return;
    }
    if (refreshing) return;
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

  // SNOW-1025: the moment a waiting worker is applied. Hidden means the
  // user has switched away (tab, app, screen lock), so nobody is watching
  // the worker change. Registered here, not inside ``register().then``, so
  // a worker recorded before registration settles is still picked up.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') applyWaitingWorker();
  });

  /**
   * Watch a service worker for the install→installed transition. When
   * it lands on ``installed`` and there is still an existing controller
   * on the page, that means an update is ready (the existing controller
   * is the OLD SW; this newly-installed one is the new shell). It is
   * queued to be applied silently (SNOW-1025).
   *
   * When it lands on ``redundant`` instead, the install failed, and the
   * two banner gates are asked directly. This is the stuck-worker path
   * that needs no version drift. After a deploy, the first navigation
   * fetches fresh HTML (navigations are network-first), so the page's
   * ``pwa-app-version`` already matches every response header and
   * ``pwa_version_check.js`` never offers the banner. A failed install
   * seen here would otherwise go unreported. The remembered answer is
   * dropped first, because it may predate the failure. A worker that
   * reached ``installed`` and was later superseded by a newer install
   * also ends ``redundant``, but that is not a failure, so it is excluded.
   *
   * @param {ServiceWorker} sw
   */
  function watchForInstall(sw) {
    let installed = false;
    sw.addEventListener('statechange', () => {
      if (!navigator.serviceWorker.controller) return;
      if (sw.state === 'installed') {
        installed = true;
        queueSilentUpdate(sw);
      } else if (sw.state === 'redundant' && !installed) {
        bannerAnswer = null;
        revealUpdateBannerIfStuck();
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
        // Three entry points to "an update is ready" (each queues it to be
        // applied silently; SNOW-1025):
        //   1. ``waiting`` is non-null at register-time — a new worker has
        //      already installed and is parked waiting (the common case
        //      now that we don't auto-skipWaiting).
        //   2. ``installing`` is non-null at register-time — a SW update
        //      check started before our register() resolved.
        //   3. ``updatefound`` fires later — the common case during a
        //      normal session where the SW changes on the next deploy.
        if (registration.waiting && navigator.serviceWorker.controller) {
          queueSilentUpdate(registration.waiting);
        }
        if (registration.installing) {
          watchForInstall(registration.installing);
        }
        registration.addEventListener('updatefound', () => {
          if (registration.installing) {
            watchForInstall(registration.installing);
          }
        });

        // Pick an update up promptly for a tab left open across a deploy:
        // re-check when the tab regains focus. ``update()`` is a no-op when
        // the SW script is unchanged. A worker it installs is applied the
        // next time the page is hidden.
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
