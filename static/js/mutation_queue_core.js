/*
 * static/js/mutation_queue_core.js — Pure, context-free mutation-queue helpers (SNOW-376).
 *
 * Shared by BOTH the page-side queue (static/js/mutation_queue.js, loaded
 * via a plain <script> tag) and the service worker's Background Sync
 * self-drain path (static/js/sw.js, loaded via ``importScripts``). Attached
 * to ``self`` — rather than ``window`` — because ``self`` resolves to the
 * same global object in a window context AND in a worker context; ``window``
 * does not exist in a worker at all, so the two consumers could not share a
 * ``window``-scoped module.
 *
 * Deliberately dependency-free: nothing in this file touches
 * ``window.pwaDb`` / ``window.pwaTelemetry`` / IndexedDB / fetch. Every
 * export is a pure function of its arguments so it behaves identically
 * whether it's called from a live page or from the SW's one-pass replay on
 * a real ``sync`` event fired after every tab has closed (spec §7.3 — the
 * actual Background Sync case, as opposed to the page-message-bridge
 * fast path used while a tab is still open).
 *
 * Public API — attached to ``self.pwaMutationQueueCore``:
 *
 *   MAX_ATTEMPTS                Retry ceiling. A row that reaches this many
 *                                attempts without a 2xx is marked ``failed``
 *                                — no further retry.
 *   SYNC_TAG                    The single shared Background Sync
 *                                registration tag both ``mutation_queue.js``
 *                                (``registration.sync.register``) and
 *                                ``sw.js`` (the ``sync`` event listener)
 *                                key off.
 *   backoffDelayMs(attempts)    Exponential backoff schedule in
 *                                milliseconds: ``min(2**attempts, 300)`` —
 *                                2/4/8/16/32/64/128/256s, capped at 300s
 *                                from attempt 9 on.
 *   classifyStatus(status)      Classifies an HTTP response status into
 *                                ``'success' | 'permanent' | 'retry'``.
 *                                Network errors have no status code to
 *                                classify — callers treat a thrown
 *                                ``fetch()`` as ``'retry'`` directly.
 *   isRowEligible(row, now)     True when a ``queue:mutations`` row is due
 *                                to be replayed right now: not already
 *                                ``failed``, and its backoff window (if
 *                                any) has elapsed.
 *   selectDrainBatch(rows, now, batchSize)
 *                                The rows one drain pass should replay:
 *                                eligible ones FIRST, then capped at
 *                                ``batchSize``. Doing it the other way
 *                                round starves the queue (SNOW-617).
 *   nextRowState(row, outcome, now)
 *                                The whole post-attempt transition, as a
 *                                pure function: what to do with the row
 *                                and what it should look like afterwards.
 *
 * SNOW-617: ``selectDrainBatch`` and ``nextRowState`` were the two pieces
 * of the state machine that lived OUTSIDE this module, spelled out once in
 * ``mutation_queue.js``'s ``drain``/``_processRow`` and again by hand in
 * ``sw.js``'s ``_selfDrainMutations``. That is how the offline guard came
 * to be missing from ``drain()`` while its mirror in ``telemetry.js`` had
 * one: the transitions were not in the tested unit, so nothing compared
 * the two copies.
 *
 * See docs/mutation-queue.md for the full row shape, state machine, and
 * backoff schedule this file implements.
 */

(function () {
  'use strict';

  // Retry ceiling (spec §7 / ticket SNOW-376). Chosen so a persistent
  // outage (a few hours at the 300s-capped cadence) eventually stops
  // retrying rather than growing the queue forever.
  var MAX_ATTEMPTS = 20;

  // Single shared Background Sync tag — one tag covers every mutation,
  // rather than a tag per operation type, per the ticket's resolved design
  // decision.
  var SYNC_TAG = 'mutation-queue';

  /**
   * Exponential backoff schedule for a given attempt count.
   *
   * attempts=1 → 2s, 2 → 4s, 3 → 8s, 4 → 16s, 5 → 32s, 6 → 64s, 7 → 128s,
   * 8 → 256s, 9+ → capped at 300s (2**9 = 512 is the first to exceed it).
   *
   * @param {number} attempts
   * @returns {number} delay in milliseconds
   */
  function backoffDelayMs(attempts) {
    var seconds = Math.min(Math.pow(2, attempts), 300);
    return seconds * 1000;
  }

  /**
   * Classify an HTTP response status code for the drain loop.
   *
   * - 2xx                    → 'success' — delete the row.
   * - 408, 429, or any 5xx   → 'retry'   — transient, back off and retry.
   * - any other 4xx          → 'permanent' — mark failed on first sight,
   *                              no further retry (400/401/403/404/409/410/
   *                              422/… — the request itself is wrong, not
   *                              the connection).
   * - 1xx / 3xx (unexpected from a mutation endpoint) → 'retry', so an
   *   unusual response doesn't silently drop a mutation.
   *
   * @param {number} status
   * @returns {'success'|'permanent'|'retry'}
   */
  function classifyStatus(status) {
    if (status >= 200 && status < 300) return 'success';
    if (status === 408 || status === 429) return 'retry';
    if (status >= 500) return 'retry';
    if (status >= 400) return 'permanent';
    return 'retry';
  }

  /**
   * True when ``row`` should be replayed right now.
   *
   * @param {object} row
   * @param {number} now epoch milliseconds
   * @returns {boolean}
   */
  function isRowEligible(row, now) {
    if (!row || row.status === 'failed') return false;
    var nextAttemptAt = typeof row.next_attempt_at === 'number' ? row.next_attempt_at : 0;
    return nextAttemptAt <= now;
  }

  /**
   * The rows one drain pass should replay.
   *
   * SNOW-617: the order of these two steps is the whole point. ``drain()``
   * used to read the oldest ``batchSize`` rows and filter THOSE for
   * eligibility, so a permanently-failed row — which is never deleted,
   * because the nav badge's error state is defined on its presence
   * (docs/mutation-queue.md) — occupied a slot in every pass forever. With
   * ``batchSize`` such rows at the head of the store, every eligible row
   * behind them starved: the queue would sit there, badge showing a count,
   * draining nothing, with no error and no telemetry to say why.
   *
   * Filtering first and capping after means failed rows cost nothing, and
   * the cap still bounds how much one pass can do.
   *
   * @param {object[]} rows Every row in the store, oldest first.
   * @param {number} now epoch milliseconds
   * @param {number} batchSize Maximum rows to return.
   * @returns {object[]}
   */
  function selectDrainBatch(rows, now, batchSize) {
    var batch = [];
    if (!rows) return batch;
    for (var i = 0; i < rows.length; i++) {
      if (!isRowEligible(rows[i], now)) continue;
      batch.push(rows[i]);
      if (batch.length >= batchSize) break;
    }
    return batch;
  }

  /**
   * What to do with ``row`` after an attempt that produced ``outcome``.
   *
   * The whole post-attempt transition, as a pure function of its inputs —
   * so the state machine can be tested without IndexedDB, a network, or a
   * service worker. Both drain paths call this; neither decides for
   * itself.
   *
   * ``attempts`` is incremented on every non-success branch before the row
   * is handed back, so it reflects the attempt that just happened rather
   * than reading as "never attempted" — the distinction the
   * ``pwa.mutation.failed_permanent`` telemetry relies on to tell
   * "failed on the first try" from "never tried".
   *
   * @param {object} row The stored row, as read.
   * @param {'success'|'permanent'|'retry'} outcome ``classifyStatus``'s
   *   verdict, or ``'retry'`` for a thrown ``fetch()`` (a network error
   *   has no status to classify).
   * @param {number} now epoch milliseconds
   * @returns {{action: 'delete'|'fail'|'reschedule', row?: object,
   *   reason?: string}} ``delete`` — it succeeded, drop it. ``fail`` —
   *   persist ``row`` (already stamped ``status: 'failed'``) and report
   *   ``reason``. ``reschedule`` — persist ``row`` with its new backoff.
   */
  function nextRowState(row, outcome, now) {
    if (outcome === 'success') return { action: 'delete' };

    var attempts = (row && row.attempts ? row.attempts : 0) + 1;

    if (outcome === 'permanent') {
      return {
        action: 'fail',
        row: Object.assign({}, row, { attempts: attempts, status: 'failed' }),
        reason: 'permanent_4xx',
      };
    }

    // 'retry' — {408, 429}, 5xx, or a network error.
    if (attempts >= MAX_ATTEMPTS) {
      return {
        action: 'fail',
        row: Object.assign({}, row, { attempts: attempts, status: 'failed' }),
        reason: 'max_attempts',
      };
    }

    return {
      action: 'reschedule',
      row: Object.assign({}, row, {
        attempts: attempts,
        status: 'retry-scheduled',
        next_attempt_at: now + backoffDelayMs(attempts),
      }),
    };
  }

  self.pwaMutationQueueCore = Object.freeze({
    MAX_ATTEMPTS: MAX_ATTEMPTS,
    SYNC_TAG: SYNC_TAG,
    backoffDelayMs: backoffDelayMs,
    classifyStatus: classifyStatus,
    isRowEligible: isRowEligible,
    selectDrainBatch: selectDrainBatch,
    nextRowState: nextRowState,
  });
})();
