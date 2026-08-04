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

  self.pwaMutationQueueCore = Object.freeze({
    MAX_ATTEMPTS: MAX_ATTEMPTS,
    SYNC_TAG: SYNC_TAG,
    backoffDelayMs: backoffDelayMs,
    classifyStatus: classifyStatus,
    isRowEligible: isRowEligible,
  });
})();
