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

  // SNOW-384: SW → page telemetry bridge. sw.js (and sw-kill.js) have no
  // ``window`` and so cannot call ``window.pwaTelemetry`` directly; they
  // instead post ``{type: 'pwa-telemetry', event, properties}`` to every
  // client they control. This single listener forwards any such message,
  // regardless of which SW script sent it, to ``window.pwaTelemetry.emit``.
  //
  // SNOW-376: the same channel also carries ``{type: 'drain-mutations'}``
  // when a Background Sync fires for the mutation-queue tag while this
  // tab is open — see static/js/sw.js's ``_handleMutationSync``.
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
    }
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
    title.textContent = 'Update available';
    title.style.cssText = 'font-weight:600;font-size:14px;';
    const sub = document.createElement('div');
    sub.textContent = 'A newer version of Snowdesk is ready.';
    sub.style.cssText = 'color:#475569;font-size:12px;margin-top:2px;';
    copy.append(title, sub);
    const reload = document.createElement('button');
    reload.type = 'button';
    reload.id = 'sw-update-banner-reload';
    reload.textContent = 'Reload';
    reload.style.cssText =
      'cursor:pointer;border:0;border-radius:9999px;background:#0f172a;' +
      'color:#fff;padding:.4rem .9rem;font:600 13px system-ui,sans-serif;';
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.dataset.action = 'dismiss';
    dismiss.setAttribute('aria-label', 'Dismiss');
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
    if (!banner) return;
    if (banner.dataset.fallback === '1') {
      banner.style.display = 'flex';
    } else {
      banner.classList.remove('hidden');
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
   * Also clears ``pwa.update.first_shown_at`` so a successful update
   * doesn't leave a stale escalation timer that later trips the blocking
   * modal on cold launch (SNOW-374 §3.9).
   */
  async function handleReloadClick() {
    const btn = document.getElementById('sw-update-banner-reload');
    if (btn) {
      if (btn.dataset.busy === '1') return;
      btn.dataset.busy = '1';
      btn.setAttribute('disabled', 'disabled');
    }

    try {
      localStorage.removeItem('pwa.update.first_shown_at');
    } catch (_err) {
      // Storage unavailable — ignore.
    }

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
      // next navigation runs without a controller. We don't touch caches
      // here — that's the kill-switch SW's job when the flip goes through
      // Mechanism B (``/sw-kill.js``). This path just gets the SW out of
      // the way; the user can hard-refresh to clear anything else.
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
        document.addEventListener('visibilitychange', () => {
          if (document.visibilityState === 'visible') {
            registration.update().catch(() => {});
          }
        });
      })
      .catch((err) => {
        console.error('[sw] registration failed:', err);
      });
  });
})();
