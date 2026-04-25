/*
 * static/js/offline.js — "Save offline" icon-button controller for /map/.
 *
 * Registers the service worker at /sw.js, then listens for a click on
 * the #offline-toggle icon pill (SNOW-36) to trigger a precache pass.
 * Progress and completion messages from the SW are reflected in the
 * button's aria-label / title / data-state so the SR announcement
 * still fires and the icon can tint/spin via CSS on state changes.
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

  const btnEl = document.getElementById('offline-toggle');
  const mapEl = document.getElementById('map');

  // Guard: if the button element is absent (e.g. template variant without
  // the utility cluster), do nothing.
  if (!btnEl || !mapEl) {
    return;
  }

  // Guard: if the browser does not support service workers, hide the button
  // so the user is not presented with an icon that can never work.
  if (!('serviceWorker' in navigator)) {
    btnEl.style.display = 'none';
    return;
  }

  // The icon pill has an inner <svg>; textContent writes would wipe it.
  // Instead, this helper routes user-facing state through aria-label +
  // title (hover tooltip) and data-state (CSS hook for tint/spin).
  const setState = (stateName, label) => {
    btnEl.dataset.state = stateName;
    btnEl.setAttribute('aria-label', label);
    btnEl.setAttribute('title', label);
  };

  // Build the "saved"-state CTA label. If any tiles failed during the last
  // precache pass we suffix the count so the user can see the bundle is
  // partial; otherwise the label is the unadorned "tap to update".
  // i18n: translatable
  const savedLabel = (failed) => {
    if (!failed || failed <= 0) return 'Saved — tap to update';
    const noun = failed === 1 ? 'tile' : 'tiles';
    return `Saved — ${failed} ${noun} missed, tap to retry`;
  };

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
    const storedFailed =
      parseInt(localStorage.getItem('offline-map-failed-count'), 10) || 0;
    setState('saved', savedLabel(storedFailed));
  }

  // -------------------------------------------------------------------------
  // Click — trigger precache via SW message
  // -------------------------------------------------------------------------

  btnEl.addEventListener('click', async () => {
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

    btnEl.disabled = true;
    setState('saving', 'Saving…'); // i18n: translatable
  });

  // -------------------------------------------------------------------------
  // Message handler — reflect progress back into the button state
  // -------------------------------------------------------------------------

  navigator.serviceWorker.addEventListener('message', (ev) => {
    if (!ev.data) return;

    switch (ev.data.type) {
      case 'progress': {
        const { cached, total, failed } = ev.data;
        const suffix = failed > 0 ? `, ${failed} missed` : '';
        setState('saving', `Saving… (${cached} of ${total}${suffix})`); // i18n: translatable
        btnEl.disabled = true;
        break;
      }

      case 'complete': {
        const failed = ev.data.failed | 0;
        setState('saved', savedLabel(failed));
        btnEl.disabled = false;
        localStorage.setItem('offline-map-saved', new Date().toISOString());
        if (failed > 0) {
          localStorage.setItem('offline-map-failed-count', String(failed));
        } else {
          localStorage.removeItem('offline-map-failed-count');
        }
        break;
      }

      case 'error': {
        setState('error', 'Save failed — tap to retry'); // i18n: translatable
        btnEl.disabled = false;
        console.error('[offline] Precache error:', ev.data);
        break;
      }

      default:
        break;
    }
  });
})();
