/*
 * static/js/sw_register.js — Site-wide service-worker registration.
 *
 * Loaded deferred from ``public/templates/public/base.html`` so the SW
 * registration runs on every public page — required for browser
 * install prompts, which only appear after the page is served by an
 * SW with a manifest carrying valid icons.
 *
 * Update flow
 * -----------
 * The PWA shell SW (``static/js/sw.js``) calls ``self.skipWaiting()``
 * on install but deliberately does NOT call ``self.clients.claim()``,
 * because pairing the two with a controllerchange-based auto-reload
 * produced a tight reload loop in dev (every navigation re-triggered
 * the SW update check). Without ``claim()`` the new SW activates
 * immediately but only controls a tab on its next navigation.
 *
 * To make the user aware that an update is ready, this script reveals
 * the ``#sw-update-banner`` markup baked into ``base.html`` whenever a
 * fresh SW has finished installing AND there is still an old SW
 * controlling the page (= a real update, not first-time install).
 * Clicking "Reload" navigates the page, picking up the new shell;
 * clicking × dismisses the banner for the rest of the tab's lifetime.
 *
 * Errors from ``register()`` are logged but never surfaced to the
 * user — the site is fully usable without a service worker.
 *
 * i18n: every user-visible string lives in the banner template under
 * ``{% trans %}``; this script only toggles visibility.
 */

(function () {
  'use strict';

  if (!('serviceWorker' in navigator)) return;

  const banner = document.getElementById('sw-update-banner');

  /**
   * Reveal the update banner. Toggles the ``hidden``/``flex`` Tailwind
   * pair rather than the HTML ``hidden`` attribute, because the latter
   * loses to any explicit ``display`` utility (e.g. ``flex``) in the
   * cascade — Tailwind's reset does not flag the UA ``[hidden]`` rule
   * as ``!important``.
   */
  function showUpdateBanner() {
    if (!banner) return;
    banner.classList.remove('hidden');
    banner.classList.add('flex');
  }

  function hideUpdateBanner() {
    if (!banner) return;
    banner.classList.remove('flex');
    banner.classList.add('hidden');
  }

  if (banner) {
    banner
      .querySelector('[data-action="reload"]')
      ?.addEventListener('click', () => {
        location.reload();
      });
    banner
      .querySelector('[data-action="dismiss"]')
      ?.addEventListener('click', hideUpdateBanner);
  }

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
        showUpdateBanner();
      }
    });
  }

  navigator.serviceWorker
    .register('/sw.js', { scope: '/' })
    .then((registration) => {
      // Three entry points to "an update is ready":
      //   1. ``waiting`` is non-null at register-time (rare with
      //      skipWaiting, but possible if the new SW already raced past
      //      ``installed`` before this listener attached).
      //   2. ``installing`` is non-null at register-time — a SW update
      //      check started before our register() resolved.
      //   3. ``updatefound`` fires later — the common case during a
      //      normal session where the SW changes on the next deploy.
      if (registration.waiting && navigator.serviceWorker.controller) {
        showUpdateBanner();
      }
      if (registration.installing) {
        watchForInstall(registration.installing);
      }
      registration.addEventListener('updatefound', () => {
        if (registration.installing) {
          watchForInstall(registration.installing);
        }
      });
    })
    .catch((err) => {
      console.error('[sw] registration failed:', err);
    });
})();
