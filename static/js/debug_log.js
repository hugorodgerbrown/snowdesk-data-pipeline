/*
 * static/js/debug_log.js — window.pwaDebugLog, the on-device diagnostic
 * trace (SNOW-812).
 *
 * The map page cannot be debugged from a phone. Half its decisions are
 * made inside the service worker, which has no console any device-side
 * devtools can reach, and nearly all of them sit behind deliberately
 * silent fallbacks — 39 `catch (_e) {}` sites in map.js, 27 in sw.js.
 * That silence is correct behaviour (a cache probe failing must never
 * break the page) and it is exactly what makes "the region downloaded but
 * the map is blank" indistinguishable from a working download.
 *
 * This module is the recorder those sites write to. It is deliberately
 * NOT map-specific: it loads from public/base.html on every page, and the
 * map is simply its first and richest producer.
 *
 * Surface
 * -------
 *   window.pwaDebugLog.record(src, evt, detail)  one line
 *   window.pwaDebugLog.on                        cheap guard for callers
 *                                                whose detail costs
 *                                                something to build
 *   window.pwaDebugLog.entries()                 in-memory, newest first
 *   window.pwaDebugLog.history(limit)            IDB read-back, newest
 *                                                first, survives reload
 *   window.pwaDebugLog.subscribe(fn)             panel repaint hook
 *   window.pwaDebugLog.setEnabled(bool)          recording on/off
 *   window.pwaDebugLog.isEnabled()
 *   window.pwaDebugLog.clear()
 *   window.pwaDebugLog.format(rows)              the copy/paste text
 *
 * Off by default, and off means off
 * ---------------------------------
 * `record()` returns on its first statement when recording is off. The
 * waffle flag decides whether this script loads AT ALL, so for everyone
 * outside GRP_DEBUG every instrumented call site is an optional-chain
 * no-op against an undefined global. Callers that must build a string or
 * walk a structure to describe themselves check `.on` first, so that work
 * is skipped too — the trace must never be the slowest thing on the page,
 * because then it would be changing the behaviour it is there to observe.
 *
 * Persistence
 * -----------
 * Lines land in an in-memory ring first (synchronous, ordered, always the
 * panel's source of truth) and are flushed to the `log:debug` IndexedDB
 * store in batches. A reload therefore keeps the trace — which matters,
 * because a fair number of the bugs worth chasing here end in a reload.
 *
 * The enabled flag is persisted three ways, mirroring exactly what
 * pwa_dev_shell_toggle.js does for `sw.devShellCache`: localStorage for
 * this page's own synchronous read at parse time, a `meta:app` row so a
 * service worker restarted for a bare `fetch` event can rehydrate it
 * itself, and a direct postMessage so the live worker reacts now rather
 * than after a restart.
 */

(function () {
  'use strict';

  // Recording state, its localStorage key, and the meta:app row sw.js
  // reads back in _hydrateDebugLogEnabled().
  const STORAGE_KEY = 'pwa.debugLog.enabled';
  const META_KEY = 'debug.enabled';

  // The in-memory ring. Matches db.js's DEBUG_LOG_MAX_ROWS so the panel
  // and the persisted store agree on how much history exists.
  const MAX_ROWS = 500;

  // Flush cadence. Long enough that a burst of a hundred lines costs one
  // transaction, short enough that a crash loses almost nothing.
  const FLUSH_INTERVAL_MS = 1000;

  // Ring buffer, oldest first. `entries()` reverses for display.
  const rows = [];
  // Rows appended since the last successful flush.
  let pending = [];
  let flushTimer = null;
  let enabled = false;
  let seq = 0;
  const subscribers = new Set();

  /**
   * Read the persisted recording flag. Defaults to false — a debug trace
   * that switched itself on would be a performance regression nobody
   * asked for.
   *
   * @returns {boolean}
   */
  function readStoredEnabled() {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1';
    } catch (_err) {
      // Storage unavailable (private mode, quota) — off.
      return false;
    }
  }

  /**
   * Tell every subscriber the buffer changed. A throwing subscriber must
   * not take the others down, nor propagate back into a `record()` call
   * on the page's hot path.
   */
  function notify() {
    for (const fn of subscribers) {
      try {
        fn();
      } catch (_err) {
        // A broken panel must not break the recorder.
      }
    }
  }

  /**
   * Write everything appended since the last flush to `log:debug` in one
   * transaction.
   *
   * Best-effort by design: IndexedDB can be unavailable (private mode,
   * the SNOW-375 Reset Required state), and losing the persisted copy of
   * a diagnostic trace is not worth an unhandled rejection. The in-memory
   * ring is unaffected either way, so the panel keeps working.
   *
   * @returns {Promise<void>}
   */
  function flush() {
    flushTimer = null;
    if (pending.length === 0) return Promise.resolve();
    const batch = pending;
    pending = [];
    if (!window.pwaDb || typeof window.pwaDb.appendDebugLogBatch !== 'function') {
      return Promise.resolve();
    }
    return window.pwaDb.appendDebugLogBatch(batch).catch(() => {
      // Dropped: this batch is not retried. Re-queueing it would grow
      // `pending` without bound on a device whose IndexedDB is simply
      // gone, which is the one failure mode that would make the debug
      // tooling itself the memory leak.
    });
  }

  /**
   * Schedule a flush if one is not already pending.
   */
  function scheduleFlush() {
    if (flushTimer !== null) return;
    flushTimer = setTimeout(flush, FLUSH_INTERVAL_MS);
  }

  /**
   * Record one line.
   *
   * @param {string} src - Producer: 'map', 'sw', 'idb', 'net', 'cache',
   *   'download'. Drives the panel's filter chips.
   * @param {string} evt - Dotted event name, e.g. 'pinned.search'.
   * @param {object} [detail] - Small, JSON-serialisable facts. Keep it to
   *   the identifiers and outcomes a reader needs — keys looked up, cache
   *   names searched, hit/miss — never a whole GeoJSON payload.
   * @param {number} [at] - Epoch ms; defaults to now. Passed explicitly
   *   when relaying a service-worker line, so the panel shows when the
   *   worker recorded it rather than when the page received it.
   */
  function record(src, evt, detail, at) {
    if (!enabled) return;
    const row = {
      at: at || Date.now(),
      seq: (seq += 1),
      src: src || 'app',
      evt: evt || '',
      detail: detail || null,
    };
    rows.push(row);
    if (rows.length > MAX_ROWS) rows.splice(0, rows.length - MAX_ROWS);
    pending.push(row);
    scheduleFlush();
    notify();
  }

  /**
   * The in-memory buffer, newest first — what the panel paints.
   *
   * @returns {Array<object>}
   */
  function entries() {
    return rows.slice().reverse();
  }

  /**
   * The persisted trace, newest first, including lines from before the
   * last reload. Resolves to an empty array when IndexedDB is
   * unavailable rather than rejecting — the panel treats "no history" and
   * "history unreadable" the same way.
   *
   * @param {number} [limit]
   * @returns {Promise<Array<object>>}
   */
  function history(limit) {
    if (!window.pwaDb || typeof window.pwaDb.getDebugLog !== 'function') {
      return Promise.resolve([]);
    }
    return window.pwaDb.getDebugLog(limit).catch(() => []);
  }

  /**
   * Mirror the recording flag into localStorage, the durable `meta:app`
   * row, and the live service worker. Every step is best-effort and
   * independent — a failure in one must not skip the others.
   *
   * @param {boolean} value
   */
  function persistEnabled(value) {
    try {
      localStorage.setItem(STORAGE_KEY, value ? '1' : '0');
    } catch (_err) {
      // Storage unavailable — the meta:app mirror below still carries it.
    }
    try {
      window.pwaDb?.put('meta:app', { key: META_KEY, value }).catch(() => {});
    } catch (_err) {
      // IndexedDB unavailable — the worker falls back to "off", which is
      // the safe direction.
    }
    try {
      navigator.serviceWorker?.controller?.postMessage({
        type: 'debug-log-enabled',
        enabled: value,
      });
    } catch (_err) {
      // No controller yet — the worker reads meta:app on its next start.
    }
  }

  /**
   * Turn recording on or off.
   *
   * Switching OFF also empties the persisted store. A trace is a
   * point-in-time diagnostic, and leaving one on the device after the
   * session that wanted it just means the next reader is looking at
   * somebody else's afternoon.
   *
   * @param {boolean} value
   */
  function setEnabled(value) {
    const next = !!value;
    if (next === enabled) return;
    enabled = next;
    window.pwaDebugLog.on = next;
    persistEnabled(next);
    if (!next) {
      rows.length = 0;
      pending = [];
      if (flushTimer !== null) {
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      try {
        window.pwaDb?.clearDebugLog?.().catch(() => {});
      } catch (_err) {
        // Nothing to clear.
      }
    } else {
      record('app', 'recording.started', {
        href: location.pathname + location.search + location.hash,
        online: navigator.onLine,
      });
    }
    notify();
  }

  /**
   * Drop every line, in memory and on disk, without changing whether
   * recording is on.
   */
  function clear() {
    rows.length = 0;
    pending = [];
    try {
      window.pwaDb?.clearDebugLog?.().catch(() => {});
    } catch (_err) {
      // Nothing to clear.
    }
    notify();
  }

  /**
   * Render rows as the fixed-width text the panel's Copy control puts on
   * the clipboard — one line each, `HH:MM:SS.mmm  src  evt  k=v k=v`.
   *
   * This is the actual export format: a device with no devtools has no
   * other way to get a trace to a laptop, so it has to survive being
   * pasted into a Linear comment and still line up.
   *
   * @param {Array<object>} list
   * @returns {string}
   */
  function format(list) {
    return (list || [])
      .map((row) => {
        const d = new Date(row.at);
        const pad = (n, w) => String(n).padStart(w || 2, '0');
        const ts =
          `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
          `.${pad(d.getMilliseconds(), 3)}`;
        const detail = row.detail
          ? Object.entries(row.detail)
              .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
              .join(' ')
          : '';
        return `${ts}  ${String(row.src).padEnd(8)}  ${String(row.evt).padEnd(24)}  ${detail}`.trimEnd();
      })
      .join('\n');
  }

  /**
   * Subscribe to buffer changes. Returns an unsubscribe function.
   *
   * @param {Function} fn
   * @returns {Function}
   */
  function subscribe(fn) {
    subscribers.add(fn);
    return () => subscribers.delete(fn);
  }

  window.pwaDebugLog = {
    record,
    entries,
    history,
    subscribe,
    setEnabled,
    isEnabled: () => enabled,
    clear,
    format,
    // Mutable mirror of `enabled`, so a caller can skip building an
    // expensive detail object with one property read.
    on: false,
  };

  // Restore the persisted flag before anything else runs. Deliberately
  // synchronous off localStorage: scripts loaded after this one start
  // recording during their own init, and an async IndexedDB read would
  // resolve well after the boot sequence worth tracing had gone by.
  enabled = readStoredEnabled();
  window.pwaDebugLog.on = enabled;
  if (enabled) {
    // Re-assert the flag to the worker on every load. A worker that was
    // idle-terminated and restarted since the toggle was flipped has an
    // in-memory `_debugEnabled` of false until something tells it
    // otherwise — the same restart hazard that loses `_basemapOrigins`.
    persistEnabled(true);
    record('app', 'page.load', {
      href: location.pathname + location.search + location.hash,
      online: navigator.onLine,
      controlled: !!navigator.serviceWorker?.controller,
    });
  }

  // Fold service-worker lines into the same buffer. sw.js broadcasts
  // {type:'debug-log', entries:[{at, src, evt, detail}]} to every client;
  // relaying rather than having the worker write to IndexedDB itself
  // keeps one writer on the store and one ordering of the trace.
  try {
    navigator.serviceWorker?.addEventListener('message', (event) => {
      const data = event.data;
      if (!data || data.type !== 'debug-log' || !Array.isArray(data.entries)) return;
      for (const entry of data.entries) {
        record(entry.src || 'sw', entry.evt, entry.detail, entry.at);
      }
    });
  } catch (_err) {
    // No service-worker support — page-side lines still record.
  }

  // Flush what is buffered before the page goes away. 'pagehide' fires on
  // the bfcache path and on mobile app-switching where 'unload' does not,
  // which is precisely when someone debugging a phone backgrounds the
  // browser to go and read what just happened.
  window.addEventListener('pagehide', () => {
    if (flushTimer !== null) {
      clearTimeout(flushTimer);
      flushTimer = null;
    }
    flush();
  });
})();
