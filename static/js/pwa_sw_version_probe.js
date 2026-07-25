/*
 * static/js/pwa_sw_version_probe.js — Live SW-version probe for the
 * /_sw-version/ staff debug page (SNOW-517).
 *
 * Progressive enhancement only. templates/_debug/sw_version.html already
 * server-renders the deployed CACHE_VERSION and APP_VERSION into
 * [data-testid="sw-deployed-version"] / [data-testid="sw-app-version"] —
 * that baseline works with JS disabled. This script fills in the LIVE
 * value — whichever service worker is actually controlling this page
 * right now — into [data-testid="sw-live-version"], by posting the
 * `'version'` message sw.js already answers (see its `message` listener,
 * near the bottom of the file) and writing the reply back into the page.
 *
 * No controller yet at load (first-ever visit, or the register hasn't
 * claimed this client) leaves the element at its server-rendered "—" and
 * retries once on `controllerchange`, so a page loaded moments before the
 * very first SW activation still ends up populated without a manual
 * reload.
 */

'use strict';

(function () {
  const LIVE_VERSION_SELECTOR = '[data-testid="sw-live-version"]';

  function requestVersion() {
    const controller = navigator.serviceWorker && navigator.serviceWorker.controller;
    if (!controller) return;
    controller.postMessage('version');
  }

  function handleMessage(event) {
    if (!event.data || event.data.type !== 'version') return;
    const el = document.querySelector(LIVE_VERSION_SELECTOR);
    if (el) el.textContent = event.data.version;
  }

  if (!('serviceWorker' in navigator)) return;

  navigator.serviceWorker.addEventListener('message', handleMessage);
  navigator.serviceWorker.addEventListener('controllerchange', requestVersion);
  requestVersion();
})();
