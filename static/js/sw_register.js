/*
 * static/js/sw_register.js — Site-wide service-worker registration.
 *
 * Replaces the SNOW-9 ``offline.js`` precache controller with the
 * minimum needed to activate the PWA shell SW: register ``/sw.js`` at
 * scope ``/`` and reload once a fresh SW takes over so an in-flight
 * page picks up the new shell rather than running stale JS/HTML.
 *
 * Loaded from ``public/templates/public/base.html`` so the SW
 * registration runs on every public page — required for browser
 * install prompts, which only appear after the page is served by an
 * SW with a manifest carrying valid icons.
 *
 * Errors are logged but never surfaced to the user — the site is
 * fully usable without a service worker.
 *
 * i18n: this script never renders UI, so there are no translatable
 * strings.
 */

(function () {
  'use strict';

  if (!('serviceWorker' in navigator)) return;

  navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => {
    console.error('[sw] registration failed:', err);
  });

  // When a new SW skips waiting and claims the page, reload so
  // in-flight tabs pick up the fresh shell. Guard on
  // initialController so a first-time registration (no previous SW)
  // does not trigger a reload — only genuine updates do.
  const initialController = navigator.serviceWorker.controller;
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (initialController) {
      location.reload();
    }
  });
})();
