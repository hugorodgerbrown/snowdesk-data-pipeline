/*
 * static/js/pwa_dev_shell_toggle.js — Dev-only shell-cache bypass opt-in
 * toggle for the /_sw-version/ staff debug page (SNOW-585).
 *
 * Only loaded from templates/_debug/sw_version.html, and only when
 * settings.SW_DEV_SHELL_BYPASS is on — see that template and
 * apps/public/debug_views.py::sw_version. While the bypass is active,
 * static/js/sw.js's _staleWhileRevalidate skips the shell cache entirely
 * (no read, no write) so a stale worker can never keep serving pre-pull
 * assets. This checkbox is the escape hatch for anyone who deliberately
 * wants to exercise ordinary stale-while-revalidate behaviour locally
 * (e.g. to debug the cache itself) without unsetting the env var.
 *
 * Persistence mirrors the durable-mirror pattern static/js/map.js's
 * registerBasemapOrigins() uses for 'basemap.origins': a localStorage
 * flag for the page's own synchronous read on load, PLUS a mirrored
 * {key, value} row in the 'meta:app' IndexedDB store so a service worker
 * that gets idle-terminated and later restarted for a fresh 'fetch'
 * event can rehydrate the opt-in itself (see sw.js's
 * _hydrateDevShellCacheOptIn()). The live worker is also told directly
 * via postMessage({type: 'dev-shell-cache', enabled}) so the toggle takes
 * effect immediately, without waiting for a worker restart — mirrors
 * 'register-basemap-origins' exactly.
 *
 * Progressive enhancement only: absent checkbox, absent window.pwaDb, or
 * no controlling service worker are all silently tolerated.
 */

'use strict';

(function () {
  const STORAGE_KEY = 'pwa.devShellCache.optIn';
  const META_KEY = 'sw.devShellCache';

  const checkbox = document.getElementById('sw-dev-shell-cache-optin');
  if (!checkbox) return;

  /**
   * Read the persisted opt-in flag from localStorage. Defaults to false
   * (opted-out) — the bypass is "off means off" until a developer
   * deliberately restores the cache.
   *
   * @returns {boolean}
   */
  function readStoredOptIn() {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1';
    } catch (_err) {
      // Storage unavailable (Safari private mode, quota) — default false.
      return false;
    }
  }

  /**
   * Persist the opt-in flag to localStorage, mirror it into the durable
   * 'meta:app' IndexedDB store, and tell the live controller directly.
   * Every step is best-effort — a failure in any one of them must never
   * throw back into the checkbox's change handler.
   *
   * @param {boolean} enabled
   */
  function persist(enabled) {
    try {
      localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0');
    } catch (_err) {
      // Storage unavailable — continue; the in-memory worker state
      // (set via postMessage below) still takes effect for this session.
    }

    if (window.pwaDb && typeof window.pwaDb.put === 'function') {
      try {
        window.pwaDb.put('meta:app', { key: META_KEY, value: enabled }).catch(() => {});
      } catch (_err) {
        // Ignore — persistence is best-effort.
      }
    }

    if (navigator.serviceWorker && navigator.serviceWorker.controller) {
      try {
        navigator.serviceWorker.controller.postMessage({
          type: 'dev-shell-cache',
          enabled,
        });
      } catch (_err) {
        // Ignore — the next fetch will lazily rehydrate from meta:app
        // instead (sw.js's _hydrateDevShellCacheOptIn()).
      }
    }
  }

  checkbox.checked = readStoredOptIn();
  checkbox.addEventListener('change', () => {
    persist(checkbox.checked);
  });
})();
