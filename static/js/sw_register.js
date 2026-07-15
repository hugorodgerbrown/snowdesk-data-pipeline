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
 * Clicking "Reload" posts ``{ type: 'SKIP_WAITING' }`` to that waiting
 * worker. The worker activates, calls ``clients.claim()``, and the
 * browser fires ``controllerchange`` — at which point we reload the page
 * exactly once (guarded by ``refreshing``) so the new shell is in
 * control. This guarantees the tab actually moves to the new version
 * instead of lingering on the old worker. Because the reload is gated on
 * the user having clicked "Reload" (``userTriggeredUpdate``), a
 * first-install ``clients.claim()`` does not reload the page, and there
 * is no dev reload-loop.
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

  if (!('serviceWorker' in navigator)) return;

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

  /**
   * Resolve the banner element, creating one if the page didn't render
   * the public toast partial. Public pages ship ``#sw-update-banner``
   * (Tailwind-styled) in base.html; the Django admin — which the SW also
   * controls (scope ``/``) — does not load the public chrome, so without
   * this a waiting worker would go unannounced there. We self-inject an
   * inline-styled banner (no Tailwind dependency) so the update contract
   * holds on EVERY page the SW controls, with no exceptions. Mirrors the
   * JS-built toast pattern in report.js.
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
    el.style.cssText =
      'position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);' +
      'z-index:2147483647;display:none;align-items:center;gap:.75rem;' +
      'max-width:28rem;padding:.5rem 1rem;border-radius:9999px;' +
      'background:#1e293b;color:#fff;font:500 14px system-ui,sans-serif;' +
      'box-shadow:0 10px 25px rgba(0,0,0,.25);';
    const span = document.createElement('span');
    span.textContent = 'An updated version is available.';
    const reload = document.createElement('button');
    reload.type = 'button';
    reload.dataset.action = 'reload';
    reload.textContent = 'Reload';
    reload.style.cssText =
      'cursor:pointer;border:0;border-radius:9999px;background:#fff;' +
      'color:#1e293b;padding:.25rem .75rem;font:600 12px system-ui,sans-serif;';
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.dataset.action = 'dismiss';
    dismiss.setAttribute('aria-label', 'Dismiss');
    dismiss.textContent = '×';
    dismiss.style.cssText =
      'cursor:pointer;border:0;background:transparent;color:#fff;' +
      'opacity:.7;font-size:16px;line-height:1;';
    el.append(span, reload, dismiss);
    document.body.appendChild(el);
    return el;
  }

  const banner = ensureBanner();

  /**
   * Reveal / hide the update banner via an inline ``display`` toggle.
   * Inline display beats Tailwind's ``hidden`` utility on the template
   * banner (specificity), and is also what the self-injected admin banner
   * relies on — one code path for both.
   */
  function showUpdateBanner(worker) {
    if (worker) waitingWorker = worker;
    if (!banner) return;
    banner.style.display = 'flex';
  }

  function hideUpdateBanner() {
    if (!banner) return;
    banner.style.display = 'none';
  }

  if (banner) {
    banner
      .querySelector('[data-action="reload"]')
      ?.addEventListener('click', () => {
        // Ask the waiting worker to take over. Its activation fires
        // ``controllerchange`` (below), which reloads the page onto the
        // new shell. Fall back to a plain reload if we somehow have no
        // waiting-worker reference (e.g. it already activated).
        userTriggeredUpdate = true;
        if (waitingWorker) {
          waitingWorker.postMessage({ type: 'SKIP_WAITING' });
        } else {
          window.location.reload();
        }
      });
    banner
      .querySelector('[data-action="dismiss"]')
      ?.addEventListener('click', hideUpdateBanner);
  }

  // The new worker called ``clients.claim()`` and now controls the page.
  // Reload once so the new shell renders — but only if this came from the
  // user accepting the update, so a first-install claim doesn't bounce
  // the page on someone's very first visit.
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (!userTriggeredUpdate || refreshing) return;
    refreshing = true;
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
