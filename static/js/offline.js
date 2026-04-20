/*
 * static/js/offline.js — "Save offline" CTA controller for the /map/ page.
 *
 * Registers the service worker at /sw.js, then listens for a button click
 * on #offline-cta to trigger a precache pass. Progress and completion
 * messages from the SW are reflected in the button's label so the user
 * can see saving in progress.
 *
 * The offline-manifest URL is read from data-offline-manifest-url on #map
 * so the Django template remains the single source of truth for all API
 * paths.
 *
 * SW registration errors are logged to the console but never surfaced to
 * the user as modal interrupts — the map remains fully usable without
 * offline support.
 *
 * i18n: strings below are marked // i18n: translatable but NOT wrapped yet,
 * following the same convention as map.js. Wrap them when JS i18n is added.
 */

(function () {
  'use strict';

  const ctaEl = document.getElementById('offline-cta');
  const mapEl = document.getElementById('map');

  // Guard: if the CTA element is absent (e.g. template variant without it),
  // do nothing.
  if (!ctaEl || !mapEl) {
    return;
  }

  // Guard: if the browser does not support service workers, hide the CTA so
  // the user is not presented with a button that can never work.
  if (!('serviceWorker' in navigator)) {
    ctaEl.style.display = 'none';
    return;
  }

  // -------------------------------------------------------------------------
  // Service-worker registration
  // -------------------------------------------------------------------------

  navigator.serviceWorker
    .register('/sw.js', { scope: '/' })
    .catch((err) => {
      console.error('[offline] SW registration failed:', err);
    });

  // -------------------------------------------------------------------------
  // Restore saved state across page loads
  // -------------------------------------------------------------------------

  if (localStorage.getItem('offline-map-saved')) {
    ctaEl.textContent = 'Saved — tap to update'; // i18n: translatable
  }

  // -------------------------------------------------------------------------
  // CTA click — trigger precache via SW message
  // -------------------------------------------------------------------------

  ctaEl.addEventListener('click', async () => {
    const manifestUrl = mapEl.dataset.offlineManifestUrl;
    if (!manifestUrl) {
      console.error('[offline] No data-offline-manifest-url on #map');
      return;
    }

    let reg;
    try {
      reg = await navigator.serviceWorker.ready;
    } catch (err) {
      console.error('[offline] Could not get SW registration:', err);
      return;
    }

    if (!reg.active) {
      console.error('[offline] No active SW — try reloading the page');
      return;
    }

    reg.active.postMessage({ type: 'precache', manifestUrl });

    ctaEl.disabled = true;
    ctaEl.textContent = 'Saving…'; // i18n: translatable
  });

  // -------------------------------------------------------------------------
  // Message handler — reflect progress back into the button label
  // -------------------------------------------------------------------------

  navigator.serviceWorker.addEventListener('message', (ev) => {
    if (!ev.data) return;

    switch (ev.data.type) {
      case 'progress': {
        const { cached, total } = ev.data;
        ctaEl.textContent = `Saving… (${cached} of ${total})`; // i18n: translatable
        ctaEl.disabled = true;
        break;
      }

      case 'complete': {
        ctaEl.textContent = 'Saved — tap to update'; // i18n: translatable
        ctaEl.disabled = false;
        localStorage.setItem('offline-map-saved', new Date().toISOString());
        break;
      }

      case 'error': {
        ctaEl.textContent = 'Save failed — tap to retry'; // i18n: translatable
        ctaEl.disabled = false;
        console.error('[offline] Precache error:', ev.data);
        break;
      }

      default:
        break;
    }
  });
})();
