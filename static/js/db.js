/*
 * static/js/db.js — Minimal IndexedDB wrapper for the Snowdesk PWA (SNOW-375).
 *
 * Spec §3.8, §7.2. The base layer every subsequent client-side spec
 * item builds on (mutation queue SNOW-376, event buffer SNOW-385, cached
 * bulletin data). One IndexedDB database per app; five object stores
 * declared up front so a schema bump only fires when the store namespace
 * itself changes.
 *
 * Object stores (spec §7.2)
 * -------------------------
 *   queue:mutations  pending mutations for SNOW-376 (autoIncrement id)
 *   queue:events     telemetry buffer for SNOW-385 (autoIncrement id)
 *   meta:sync        last-sync timestamps per resource (keyPath: 'resource')
 *   meta:app         install timestamps, first-launch flags, opt-in/out
 *                     (keyPath: 'key')
 *   data:*           reserved namespace for cached server-data copies;
 *                     added on demand by consumers via ensureStore.
 *
 * Reset Required
 * --------------
 * A migration that throws or times out puts the app in the terminal
 * "Reset Required" state: pwaDb.open() returns a rejected promise for
 * the rest of the session, pwaDb.isResetRequired() flips to true, and
 * the full-screen #pwa-reset-required overlay is revealed. That overlay
 * carries data-pwa-reset-trigger (and skip-confirm) so the existing
 * SNOW-378 pwa_reset.js wipe runs when the user taps "Reset now".
 *
 * Deliberately NOT idb / dexie / similar
 * --------------------------------------
 * The whole file is ~250 lines including copy-pasted comments; a
 * third-party wrapper would triple the bundle for zero functional gain.
 * The Promise-wrapping pattern is well-worn — every IDBRequest is
 * lifted into a Promise in the private ``_req`` helper.
 */

(function () {
  'use strict';

  const DB_NAME = 'snowdesk-pwa-v1';
  // Schema version. Bump when you add / rename / remove an object store,
  // AND add the corresponding upgrade branch in _runMigrations below.
  const DB_VERSION = 1;

  // Static store definitions (name → createObjectStore options). Any
  // store present here is created at version 1 and never removed.
  // Additions bump DB_VERSION and land a new branch in _runMigrations.
  const STORES = Object.freeze({
    'queue:mutations': { keyPath: 'id', autoIncrement: true },
    'queue:events': { keyPath: 'id', autoIncrement: true },
    'meta:sync': { keyPath: 'resource' },
    'meta:app': { keyPath: 'key' },
  });

  // Session state — single-page-load lifetime.
  let _dbPromise = null;
  let _resetRequired = false;
  let _contextCache = null;

  /**
   * Reveal the full-screen Reset Required overlay and flip the internal
   * flag so every subsequent open() is a hard error. Called on
   * migration failure only.
   *
   * Also binds the overlay's CTA to ``window.pwaResetLocalData`` (the
   * SNOW-378 six-step wipe) if it's available. Binding here rather than
   * on page load keeps the reset code path completely inert until the
   * overlay actually needs to fire.
   */
  function _enterResetRequired(reason) {
    _resetRequired = true;
    try {
      // eslint-disable-next-line no-console
      console.error('pwa.db.reset_required', reason);
    } catch (_e) {
      // Ignore.
    }
    try {
      const el = document.getElementById('pwa-reset-required');
      if (el) el.classList.remove('hidden');
      const cta = document.getElementById('pwa-reset-required-cta');
      if (cta && typeof window.pwaResetLocalData === 'function') {
        cta.addEventListener('click', (evt) => {
          evt.preventDefault();
          // SNOW-384: forced=true — the user didn't elect to reset, the
          // app forced their hand by entering an unrecoverable state.
          // Reports pwa.reset.forced instead of pwa.reset.user_initiated.
          window.pwaResetLocalData(true).catch(() => window.location.reload());
        });
      }
    } catch (_e) {
      // Overlay markup missing (unlikely in production, but a stale
      // template shouldn't crash the whole page).
    }
  }

  /**
   * Wrap an IDBRequest in a Promise.
   * @param {IDBRequest} req
   * @returns {Promise<any>}
   */
  function _req(req) {
    return new Promise((resolve, reject) => {
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IDBRequest failed'));
    });
  }

  /**
   * Apply the schema for the requested version. Idempotent — re-running
   * against a DB already at the target version is a no-op because
   * createObjectStore is guarded by objectStoreNames.contains().
   *
   * @param {IDBDatabase} db
   */
  function _runMigrations(db) {
    for (const [name, opts] of Object.entries(STORES)) {
      if (!db.objectStoreNames.contains(name)) {
        db.createObjectStore(name, opts);
      }
    }
  }

  /**
   * SNOW-384: cold-start storage-pressure check. Fire-and-forget — never
   * awaited by ``open()`` callers, since it's diagnostic only and must
   * not add latency to every consumer of the DB.
   *
   * TODO(SNOW-XXX): tighten the heuristic once real eviction cases
   * surface. Today it is deliberately conservative to avoid false
   * positives on ordinary first-ever visits (where zero usage and a
   * modest quota are both completely normal):
   *
   *   - ``quota`` implausibly small for any browser to hand out under
   *     normal conditions (< 5 MB) — most browsers grant a meaningful
   *     fraction of free disk space; a value this low suggests the OS
   *     / browser is under real storage pressure.
   *   - ``usage`` is exactly 0 AND this device has an independent
   *     localStorage marker (``pwa.install.installed_at``, written by
   *     ``pwa_install.js``) proving the PWA was previously installed —
   *     i.e. writes were expected to exist. localStorage and IndexedDB
   *     are evicted somewhat independently, so a surviving localStorage
   *     marker alongside a wiped IndexedDB is a plausible eviction
   *     signal rather than "first visit, nothing written yet".
   */
  function _checkStorageEstimate() {
    try {
      if (!('storage' in navigator) || typeof navigator.storage.estimate !== 'function') {
        return;
      }
      navigator.storage
        .estimate()
        .then((estimate) => {
          const quota = estimate && estimate.quota;
          const usage = estimate && estimate.usage;
          const MIN_PLAUSIBLE_QUOTA_BYTES = 5 * 1024 * 1024;
          const quotaSuspiciouslyLow =
            typeof quota === 'number' && quota > 0 && quota < MIN_PLAUSIBLE_QUOTA_BYTES;
          let hadPriorInstall = false;
          try {
            hadPriorInstall = !!localStorage.getItem('pwa.install.installed_at');
          } catch (_e) {
            // Storage unavailable — treat as no prior-install evidence.
          }
          const usageZeroAfterInstall = hadPriorInstall && usage === 0;
          if (quotaSuspiciouslyLow || usageZeroAfterInstall) {
            try {
              window.pwaTelemetry?.emit('pwa.storage.evicted_probable', {
                quota,
                usage,
              });
            } catch (_e) {
              // Ignore — telemetry must never affect DB open.
            }
          }
        })
        .catch(() => {
          // storage.estimate() rejected — nothing to report.
        });
    } catch (_e) {
      // Non-fatal — this check is diagnostic only.
    }
  }

  /**
   * Open the DB (memoised for the page's lifetime). Rejects immediately
   * if a prior open() failed and put the app in Reset Required state.
   *
   * @returns {Promise<IDBDatabase>}
   */
  function open() {
    if (_resetRequired) {
      return Promise.reject(new Error('reset_required'));
    }
    if (_dbPromise) return _dbPromise;
    if (typeof indexedDB === 'undefined') {
      return Promise.reject(new Error('indexeddb_unavailable'));
    }

    _dbPromise = new Promise((resolve, reject) => {
      let req;
      try {
        req = indexedDB.open(DB_NAME, DB_VERSION);
      } catch (err) {
        _enterResetRequired(err);
        reject(err);
        return;
      }
      req.onupgradeneeded = (evt) => {
        try {
          _runMigrations(evt.target.result);
        } catch (err) {
          // Migration failure — abort the upgrade transaction and put
          // the app in Reset Required state. The onerror below fires
          // once the aborted transaction settles.
          try {
            evt.target.transaction.abort();
          } catch (_e) {
            // Already aborted — ignore.
          }
          _enterResetRequired(err);
        }
      };
      req.onsuccess = () => {
        if (_resetRequired) {
          try {
            req.result.close();
          } catch (_e) {
            // Ignore.
          }
          reject(new Error('reset_required'));
          return;
        }
        // SNOW-384: cold-start storage-pressure check — fire-and-forget,
        // runs once per successful open() (this handler fires exactly
        // once per indexedDB.open() call; _dbPromise memoisation means
        // open() itself is only actually invoked once per page load).
        _checkStorageEstimate();
        resolve(req.result);
      };
      req.onerror = () => {
        // Any open error — corrupt DB, quota exceeded, private-mode
        // blocked — is fatal for this session.
        _enterResetRequired(req.error);
        reject(req.error || new Error('idb_open_failed'));
      };
      req.onblocked = () => {
        // Another tab is holding the DB open at an older version. Do
        // NOT enter Reset Required — the block clears once the other
        // tab closes. Reject with a distinct code so callers can retry.
        reject(new Error('idb_blocked'));
      };
    }).catch((err) => {
      // Ensure a failed open doesn't sit cached — a caller can retry
      // once the block clears (e.g. after the user closes the other tab).
      _dbPromise = null;
      throw err;
    });

    return _dbPromise;
  }

  /**
   * Run one store operation inside a fresh transaction and return the
   * request's result as a Promise.
   *
   * @param {string} storeName
   * @param {'readonly'|'readwrite'} mode
   * @param {(store: IDBObjectStore) => IDBRequest} op
   */
  async function _tx(storeName, mode, op) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, mode);
      const store = tx.objectStore(storeName);
      const req = op(store);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error || new Error('IDBRequest failed'));
      tx.onabort = () =>
        reject(tx.error || new Error('IDBTransaction aborted'));
    });
  }

  async function get(store, key) {
    return _tx(store, 'readonly', (s) => s.get(key));
  }

  async function put(store, value) {
    return _tx(store, 'readwrite', (s) => s.put(value));
  }

  async function del(store, key) {
    return _tx(store, 'readwrite', (s) => s.delete(key));
  }

  async function count(store) {
    return _tx(store, 'readonly', (s) => s.count());
  }

  async function clear(store) {
    return _tx(store, 'readwrite', (s) => s.clear());
  }

  /**
   * Read up to ``limit`` entries from a store (all if limit is unset).
   * IDBObjectStore.getAll accepts null for "no query", and a numeric
   * count as the second argument.
   */
  async function getAll(storeName, limit) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeName, 'readonly');
      const req = tx.objectStore(storeName).getAll(null, limit || undefined);
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error || new Error('getAll failed'));
    });
  }

  // -------------------------------------------------------------------
  // 8-field envelope context helper (spec §16.1)
  //
  // Consumers (telemetry, mutation queue) call ``context()`` to build
  // the fixed context every event / mutation carries. Cached for the
  // page's lifetime — the values are immutable within a page load.
  // -------------------------------------------------------------------

  function _readMeta(name) {
    try {
      const el = document.querySelector(`meta[name="${name}"]`);
      return el ? el.getAttribute('content') || null : null;
    } catch (_e) {
      return null;
    }
  }

  /**
   * Draw 16 bytes of CSPRNG entropy and hex-encode them. Used as a
   * fallback session_id generator on runtimes without
   * ``crypto.randomUUID`` (older mobile Safari). Deliberately avoids
   * ``Math.random`` — CodeQL flags it as insecure in identity-adjacent
   * contexts and there's no legitimate use of it here.
   */
  function _randomHex() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    let out = '';
    for (let i = 0; i < bytes.length; i++) {
      out += bytes[i].toString(16).padStart(2, '0');
    }
    return out;
  }

  function _sessionId() {
    // Tab-scoped random identifier; regenerates on tab close.
    try {
      const KEY = 'pwa.telemetry.session_id';
      const existing = sessionStorage.getItem(KEY);
      if (existing) return existing;
      const uuid =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : _randomHex();
      sessionStorage.setItem(KEY, uuid);
      return uuid;
    } catch (_e) {
      // Safari private-mode: sessionStorage throws on set. Fall back
      // to a per-page-load random value — telemetry still groups by
      // this key within a single page but not across reloads.
      try {
        return 'anon-' + _randomHex();
      } catch (_e2) {
        // No crypto either — return a fixed sentinel so telemetry
        // doesn't crash. Distinct per-tab identity is lost; the shared
        // sentinel is caught by the "unknown user_id" branch downstream.
        return 'anon-unavailable';
      }
    }
  }

  function _platform() {
    try {
      const ua = navigator.userAgent || '';
      if (/iPad|iPhone|iPod/.test(ua)) return 'ios';
      if (/Android/.test(ua)) return 'android';
      return 'web';
    } catch (_e) {
      return 'web';
    }
  }

  function _installState() {
    try {
      if (
        window.matchMedia &&
        window.matchMedia('(display-mode: standalone)').matches
      ) {
        return 'standalone';
      }
      // iOS Safari legacy affordance.
      if (typeof navigator !== 'undefined' && navigator.standalone === true) {
        return 'standalone';
      }
      return 'browser';
    } catch (_e) {
      return 'browser';
    }
  }

  function _swState() {
    try {
      if (!('serviceWorker' in navigator)) return 'none';
      const ctrl = navigator.serviceWorker.controller;
      if (ctrl) return 'activated';
      // Registration exists but no controller yet → installing.
      return 'none';
    } catch (_e) {
      return 'none';
    }
  }

  function _connection() {
    try {
      const c =
        navigator.connection ||
        navigator.mozConnection ||
        navigator.webkitConnection;
      if (c && c.effectiveType) return c.effectiveType;
      return 'unknown';
    } catch (_e) {
      return 'unknown';
    }
  }

  function context() {
    if (_contextCache) return _contextCache;
    _contextCache = Object.freeze({
      session_id: _sessionId(),
      user_id: _readMeta('pwa-user-id'),
      client_version: _readMeta('pwa-app-version') || '',
      platform: _platform(),
      install_state: _installState(),
      sw_state: _swState(),
      connection: _connection(),
    });
    return _contextCache;
  }

  function isResetRequired() {
    return _resetRequired;
  }

  // -------------------------------------------------------------------
  // Public surface
  // -------------------------------------------------------------------
  Object.defineProperty(window, 'pwaDb', {
    value: Object.freeze({
      open,
      get,
      put,
      delete: del,
      getAll,
      count,
      clear,
      context,
      isResetRequired,
      // Exposed for tests + doc reference. Do not mutate.
      DB_NAME,
      DB_VERSION,
      STORE_NAMES: Object.freeze(Object.keys(STORES)),
    }),
    writable: false,
    configurable: false,
  });
})();
