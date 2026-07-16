/*
 * static/js/pwa_reset.js — "Reset local data" escape hatch (SNOW-378).
 *
 * Spec §3.10 / §10.6 / §12.7 (non-negotiable). Single-tap recovery for
 * a stuck installation: any element with ``data-pwa-reset-trigger``
 * gets a click handler that runs the six-step wipe from the spec and
 * reloads the page. Also called by the Update Required modal
 * (``pwa_version_check.js``) when its own reset dance fails to land the
 * user on a fresh client.
 *
 * The six steps, in order:
 *   (1) Unregister every service worker via ``getRegistrations()``.
 *   (2) Delete every Cache Storage entry via ``caches.keys()``.
 *   (3) Delete every IndexedDB database. ``indexedDB.databases()`` is
 *       the preferred entry point; browsers without it (older Safari)
 *       are handled by iterating a known DB-name list. Snowdesk does
 *       not yet own any IndexedDB databases (SNOW-375 is a follow-up)
 *       so the fallback list is empty for now — noted so future work
 *       has an obvious place to extend.
 *   (4) Clear ``localStorage`` and ``sessionStorage``.
 *   (5) Reload the page.
 *
 * Emits ``pwa.reset.user_initiated`` (manage-page button, this file's own
 * ``[data-pwa-reset-trigger]`` binding) or ``pwa.reset.forced`` (the
 * ``db.js`` Reset Required overlay CTA, called with ``forced=true`` —
 * SNOW-384) via ``window.pwaTelemetry?.emit``.
 *
 * The reset is idempotent — calling it twice does the same work twice
 * and lands on the same page. A confirmation dialog (``window.confirm``)
 * gates the trigger by default; markup can opt out by setting
 * ``data-pwa-reset-skip-confirm`` (only used by the Update Required
 * modal path, which already carries its own dialogue).
 */

(function () {
  'use strict';

  const TRIGGER_ATTR = 'data-pwa-reset-trigger';
  const SKIP_CONFIRM_ATTR = 'data-pwa-reset-skip-confirm';

  // Known DB names — deleted explicitly in the fallback path for
  // browsers without ``indexedDB.databases()``. Kept in sync with
  // ``static/js/db.js`` DB_NAME (SNOW-375). When db.js bumps the
  // namespace (rare — see docs/indexeddb-scaffolding.md), add the new
  // name here so old databases are also wiped.
  const KNOWN_DB_NAMES = Object.freeze(['snowdesk-pwa-v1']);

  /**
   * Run the full six-step wipe. Every step swallows its own errors —
   * we always want the reload to happen. Returns a promise that
   * resolves once the reload has been scheduled.
   *
   * @param {boolean} [forced] SNOW-384 — true when the reset was not an
   *   elective user action but was forced by the app entering an
   *   unrecoverable state (today: ``db.js``'s Reset Required overlay,
   *   ``[data-pwa-reset-required-cta]``, after an IndexedDB migration
   *   failure). Selects which telemetry event fires:
   *   ``pwa.reset.forced`` vs ``pwa.reset.user_initiated``. Defaults to
   *   ``false`` so every existing caller (the manage-page button, via
   *   ``bindTrigger`` below) keeps reporting user-initiated.
   */
  async function resetLocalData(forced) {
    // (1) Service workers.
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister().catch(() => {})));
      }
    } catch (_err) {
      // Non-fatal.
    }

    // (2) Cache Storage.
    try {
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k).catch(() => {})));
      }
    } catch (_err) {
      // Non-fatal.
    }

    // (3) IndexedDB.
    try {
      if ('indexedDB' in window) {
        const dbs =
          typeof indexedDB.databases === 'function'
            ? await indexedDB.databases().catch(() => [])
            : KNOWN_DB_NAMES.map((name) => ({ name }));
        await Promise.all(
          dbs
            .filter((db) => db && db.name)
            .map(
              (db) =>
                new Promise((resolve) => {
                  try {
                    const req = indexedDB.deleteDatabase(db.name);
                    req.onsuccess = () => resolve();
                    req.onerror = () => resolve();
                    req.onblocked = () => resolve();
                  } catch (_err) {
                    resolve();
                  }
                }),
            ),
        );
      }
    } catch (_err) {
      // Non-fatal.
    }

    // (4) Web-storage.
    try {
      localStorage.clear();
    } catch (_err) {
      // Non-fatal (Safari private mode).
    }
    try {
      sessionStorage.clear();
    } catch (_err) {
      // Non-fatal.
    }

    // Telemetry — SNOW-385 / SNOW-384. Both pwa.reset.forced and
    // pwa.reset.user_initiated are critical events, so telemetry.js
    // fires ``sendBeacon`` immediately (even opt-out clients still send
    // the signal, spec §16.6). Optional chaining because this file is
    // loaded on admin pages too, where telemetry.js is not.
    try {
      window.pwaTelemetry?.emit(
        forced ? 'pwa.reset.forced' : 'pwa.reset.user_initiated',
      );
    } catch (_err) {
      // Ignore — analytics must never break the reset flow.
    }

    // (5) Reload. Using ``location.reload()`` (no argument) picks up
    // the new SW / cache state on the next navigation. In Safari
    // private mode where storage APIs are stubs, this is still safe.
    window.location.reload();
  }

  /**
   * Copy for the default confirmation dialog. Enumerates what will and
   * will not be lost so the user can judge before confirming.
   */
  const CONFIRM_MESSAGE =
    'Reset local data?\n\n' +
    'This will clear cached bulletins, offline data, and any saved ' +
    'preferences on this device.\n\n' +
    'Your subscription and account details are stored on the server ' +
    'and are not affected.';

  /**
   * Wire a single trigger element. Idempotent — safe to call twice on
   * the same node.
   *
   * @param {Element} el
   */
  function bindTrigger(el) {
    if (el.dataset.pwaResetBound === '1') return;
    el.dataset.pwaResetBound = '1';
    el.addEventListener('click', (evt) => {
      evt.preventDefault();
      const skip = el.hasAttribute(SKIP_CONFIRM_ATTR);
      const confirmed =
        skip ||
        (typeof window.confirm === 'function'
          ? window.confirm(CONFIRM_MESSAGE)
          : true);
      if (!confirmed) return;
      resetLocalData().catch(() => window.location.reload());
    });
  }

  /**
   * Bind every trigger currently in the DOM.
   */
  function bindAll() {
    document.querySelectorAll(`[${TRIGGER_ATTR}]`).forEach(bindTrigger);
  }

  // Expose the reset routine for programmatic callers (Update Required
  // modal, tests). The named export is deliberate so third-party scripts
  // can't accidentally re-bind ``window.pwaReset``.
  Object.defineProperty(window, 'pwaResetLocalData', {
    value: resetLocalData,
    writable: false,
    configurable: false,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindAll);
  } else {
    bindAll();
  }
})();
