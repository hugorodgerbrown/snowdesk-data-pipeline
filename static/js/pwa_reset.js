/*
 * static/js/pwa_reset.js — "Reset local data" escape hatch (SNOW-378).
 *
 * Spec §3.10 / §10.6 / §12.7 (non-negotiable). Single-tap recovery for
 * a stuck installation: any element with ``data-pwa-reset-trigger``
 * gets a click handler that runs the wipe from the spec and reloads the
 * page. Also the wipe every programmatic caller goes through:
 * ``db.js``'s Reset Required overlay CTA, and (since SNOW-615) the
 * Update Required modal in ``pwa_version_check.js``, which until then
 * carried its own copy that spared IndexedDB entirely. Both reach it via
 * ``window.pwaResetLocalData``.
 *
 * The six steps, in order:
 *   (1) Unregister every service worker via ``getRegistrations()``.
 *   (2) Delete every Cache Storage entry via ``caches.keys()``.
 *   (3) Delete every IndexedDB database. ``indexedDB.databases()`` is
 *       the preferred entry point; browsers without it (older Safari)
 *       are handled by iterating ``KNOWN_DB_NAMES`` below, which since
 *       SNOW-375 holds the one database this app owns. Extend that list
 *       rather than this comment when a second one arrives.
 *   (4) Clear ``localStorage`` and ``sessionStorage``.
 *   (5) Reload the page — but only on a clean run.
 *
 * Every step records its own outcome. A step that fails (most often a
 * ``blocked`` IndexedDB delete, meaning another tab still holds the
 * connection) is reported on the ``#pwa-reset-required`` overlay and the
 * reload is skipped: reloading would land the user back in the state the
 * reset was meant to clear, with nothing said about why.
 *
 * Emits ``pwa.reset.user_initiated`` (manage-page button, this file's own
 * ``[data-pwa-reset-trigger]`` binding) or ``pwa.reset.forced`` (the
 * ``db.js`` Reset Required overlay CTA, called with ``forced=true`` —
 * SNOW-384) via ``window.pwaTelemetry?.emit``, carrying the outcome as
 * ``{ ok, failed_steps }`` properties.
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

  // Human labels for the wipe steps, keyed by the step ids recorded in
  // the outcome and sent to telemetry. Used to name the failed step in
  // the overlay copy, so the user is told what survived the reset.
  const STEP_LABELS = Object.freeze({
    service_workers: 'the offline service worker',
    caches: 'cached pages and map tiles',
    indexeddb: 'the offline database',
    web_storage: 'saved preferences',
  });

  /**
   * Build the copy shown on the Reset Required overlay when a step did
   * not complete. Names the data that survived, then the action that
   * clears the common cause — a second tab holding the database open.
   *
   * @param {string[]} failed Step ids that did not complete.
   * @returns {string}
   */
  function failureMessage(failed) {
    const names = failed.map((step) => STEP_LABELS[step] || step).join(', ');
    return (
      `Snowdesk could not finish resetting local data: ${names} could not ` +
      'be cleared. This usually means Snowdesk is open in another tab or ' +
      'window — close the others, then tap Reset now to try again.'
    );
  }

  /**
   * Bind the Reset Required CTA to a retry. Only called when this module
   * revealed the overlay itself; on the forced path ``db.js`` has already
   * bound the same button, and a second binding would run two wipes per
   * tap.
   *
   * @param {boolean} forced Carried into the retry so it keeps reporting
   *   under the same telemetry event as the run that failed.
   */
  function bindRetryCta(forced) {
    const cta = document.getElementById('pwa-reset-required-cta');
    if (!cta || cta.dataset.pwaResetRetryBound === '1') return;
    cta.dataset.pwaResetRetryBound = '1';
    cta.addEventListener('click', (evt) => {
      evt.preventDefault();
      resetLocalData(forced);
    });
  }

  /**
   * Surface a failed wipe on the ``#pwa-reset-required`` overlay.
   *
   * @param {string[]} failed Step ids that did not complete.
   * @param {boolean} forced Whether the run came from the forced path.
   * @returns {boolean} True when the failure reached a visible surface;
   *   false when the page carries no overlay markup (admin pages), which
   *   leaves the caller to fall back to the reload.
   */
  function reportFailure(failed, forced) {
    const overlay = document.getElementById('pwa-reset-required');
    if (!overlay) return false;
    const body = document.getElementById('pwa-reset-required-body');
    // textContent, never innerHTML — the copy is built from a fixed
    // label table, but the escaping guarantee shouldn't rest on that.
    if (body) body.textContent = failureMessage(failed);
    const alreadyOpen = !overlay.classList.contains('hidden');
    overlay.classList.remove('hidden');
    if (!alreadyOpen) bindRetryCta(forced === true);
    return true;
  }

  /**
   * Run the full six-step wipe. Every step swallows its own errors and
   * records whether it completed; the reload happens only when all of
   * them did. Returns a promise resolving to the outcome.
   *
   * @param {boolean} [forced] SNOW-384 — true when the reset was not an
   *   elective user action but was forced by the app entering an
   *   unrecoverable state (today: ``db.js``'s Reset Required overlay,
   *   ``[data-pwa-reset-required-cta]``, after an IndexedDB migration
   *   failure). Selects which telemetry event fires:
   *   ``pwa.reset.forced`` vs ``pwa.reset.user_initiated``. Defaults to
   *   ``false`` so every existing caller (the manage-page button, via
   *   ``bindTrigger`` below) keeps reporting user-initiated.
   * @returns {Promise<{ok: boolean, failed: string[]}>} The per-step
   *   outcome. ``ok`` is false when at least one step did not complete,
   *   in which case the page is not reloaded.
   */
  async function resetLocalData(forced) {
    /** @type {string[]} Step ids that did not complete. */
    const failed = [];

    // (1) Service workers.
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        const results = await Promise.all(
          regs.map((r) => r.unregister().then(() => true, () => false)),
        );
        if (results.some((ok) => !ok)) failed.push('service_workers');
      }
    } catch (_err) {
      failed.push('service_workers');
    }

    // (2) Cache Storage.
    try {
      if ('caches' in window) {
        const keys = await caches.keys();
        const results = await Promise.all(
          keys.map((k) => caches.delete(k).then(() => true, () => false)),
        );
        if (results.some((ok) => !ok)) failed.push('caches');
      }
    } catch (_err) {
      failed.push('caches');
    }

    // (3) IndexedDB.
    try {
      if ('indexedDB' in window) {
        const dbs =
          typeof indexedDB.databases === 'function'
            ? await indexedDB.databases().catch(() => [])
            : KNOWN_DB_NAMES.map((name) => ({ name }));
        const results = await Promise.all(
          dbs
            .filter((db) => db && db.name)
            .map(
              (db) =>
                new Promise((resolve) => {
                  try {
                    const req = indexedDB.deleteDatabase(db.name);
                    req.onsuccess = () => resolve(true);
                    req.onerror = () => resolve(false);
                    // A blocked delete is not a success: another tab
                    // holds the connection open, so the database
                    // survives into the page this reset lands on.
                    req.onblocked = () => resolve(false);
                  } catch (_err) {
                    resolve(false);
                  }
                }),
            ),
        );
        if (results.some((ok) => !ok)) failed.push('indexeddb');
      }
    } catch (_err) {
      failed.push('indexeddb');
    }

    // (4) Web-storage. Both stores share one step id — a browser that
    // refuses one (Safari private mode) refuses both, and the step is
    // reported as failed rather than tolerated: data the reset promised
    // to clear is still on the device either way.
    let webStorageCleared = true;
    try {
      localStorage.clear();
    } catch (_err) {
      webStorageCleared = false;
    }
    try {
      sessionStorage.clear();
    } catch (_err) {
      webStorageCleared = false;
    }
    if (!webStorageCleared) failed.push('web_storage');

    const ok = failed.length === 0;

    // Telemetry — SNOW-385 / SNOW-384. Both pwa.reset.forced and
    // pwa.reset.user_initiated are critical events, so telemetry.js
    // fires ``sendBeacon`` immediately (even opt-out clients still send
    // the signal, spec §16.6). Optional chaining because this file is
    // loaded on admin pages too, where telemetry.js is not. The outcome
    // rides as properties: the event names stay as they are, so
    // apps/analytics/schema.py's allowlist is untouched.
    try {
      window.pwaTelemetry?.emit(
        forced ? 'pwa.reset.forced' : 'pwa.reset.user_initiated',
        { ok: ok, failed_steps: failed },
      );
    } catch (_err) {
      // Ignore — analytics must never break the reset flow.
    }

    // (5) Reload, on a clean run only. Using ``location.reload()`` (no
    // argument) picks up the new SW / cache state on the next
    // navigation. In Safari private mode where storage APIs are stubs,
    // this is still safe. A failed run reports instead — reloading
    // would drop the user back into the state the reset was meant to
    // clear. Pages without the overlay markup (admin) have nowhere to
    // report, so they keep the old behaviour.
    if (ok || !reportFailure(failed, forced === true)) {
      window.location.reload();
    }
    return { ok: ok, failed: failed };
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
