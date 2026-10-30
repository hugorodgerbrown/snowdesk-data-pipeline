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
 *   nextRowState(row, outcome, now, detail)
 *                                The whole post-attempt transition, as a
 *                                pure function: what to do with the row
 *                                and what it should look like afterwards.
 *                                SNOW-1262: ``detail`` (optional) is the
 *                                response's ``{status, error}``, recorded
 *                                on a failed or rescheduled row as
 *                                ``last_status`` / ``last_error``.
 *   failureDetail(status, body)  The ``{status, error}`` a response leaves
 *                                on its row: the HTTP status and the
 *                                endpoint's JSON ``error``, bounded to
 *                                ``ERROR_MAX_LENGTH`` characters.
 *   ackRecord(row, body, now)    The ``meta:app`` record a successful replay
 *                                of a row with a ``kind`` leaves behind
 *                                (SNOW-1262), or null for a row without
 *                                one: ``{key: ACK_KEY_PREFIX + key,
 *                                value: {kind, uuid, at}}``.
 *   ackExpired(record, now)      Whether an acknowledgement is past
 *                                ``ACK_TTL_MS`` and can be pruned.
 *   ACK_KEY_PREFIX, ACK_TTL_MS, DISCARD_NOTICE_KEY, ERROR_MAX_LENGTH
 *                                The constants behind the three above,
 *                                shared so the page and the worker write
 *                                the same records.
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

  // SNOW-1262: a replayed row that names its ``kind`` leaves an
  // acknowledgement in ``meta:app`` before it is deleted, keyed on its
  // Idempotency-Key, so a consumer holding that key (route_draw.js's
  // draft) can tell "it landed" from "it was never sent" on a later load —
  // whether the page or the service worker did the replay. Kept a week:
  // long enough to outlast any realistic gap between a Background Sync
  // replay and the next time the map is opened, short enough that the
  // store never grows.
  var ACK_KEY_PREFIX = 'mutations.ack:';
  var ACK_TTL_MS = 7 * 24 * 60 * 60 * 1000;

  // SNOW-1262: written by the worker's principal guard when it discards a
  // row with a ``kind`` — a worker has no page to tell — and read (then
  // deleted) by the page on its next load.
  var DISCARD_NOTICE_KEY = 'mutations.discard-notice';

  // The longest endpoint ``error`` string a row keeps. Every first-party
  // endpoint answers a short machine code or one sentence; the bound only
  // stops an unexpected body from bloating a row that may sit for days.
  var ERROR_MAX_LENGTH = 200;

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
   * SNOW-1132: ``doneStatuses`` lists the codes that mean "already applied"
   * for THIS mutation, and they classify as 'success' ahead of every rule
   * above. A delete of a row that is already gone is the case: the
   * downloads forget endpoint answers 404 for it, and a permanent failure
   * there left a red badge that could never clear for an outcome that was
   * exactly what the user asked for.
   *
   * @param {number} status
   * @param {number[]} [doneStatuses] Codes the caller declared as done.
   * @returns {'success'|'permanent'|'retry'}
   */
  function classifyStatus(status, doneStatuses) {
    if (Array.isArray(doneStatuses) && doneStatuses.indexOf(status) !== -1) {
      return 'success';
    }
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
   * @param {{status: ?number, error: ?string}} [detail] SNOW-1262: what
   *   the response said, from ``failureDetail``. When given it is stamped
   *   on a failed or rescheduled row as ``last_status`` / ``last_error``,
   *   so a reader can tell a validation refusal from the route cap, a
   *   sign-out or a missing endpoint. Omitted, the row is unchanged in
   *   shape — every caller from before SNOW-1262 gets what it always got.
   * @returns {{action: 'delete'|'fail'|'reschedule', row?: object,
   *   reason?: string}} ``delete`` — it succeeded, drop it. ``fail`` —
   *   persist ``row`` (already stamped ``status: 'failed'``) and report
   *   ``reason``. ``reschedule`` — persist ``row`` with its new backoff.
   */
  function nextRowState(row, outcome, now, detail) {
    if (outcome === 'success') return { action: 'delete' };

    var attempts = (row && row.attempts ? row.attempts : 0) + 1;
    var recorded = {};
    if (detail) {
      recorded.last_status = detail.status == null ? null : detail.status;
      recorded.last_error = detail.error == null ? null : detail.error;
    }

    if (outcome === 'permanent') {
      return {
        action: 'fail',
        row: Object.assign({}, row, recorded, { attempts: attempts, status: 'failed' }),
        reason: 'permanent_4xx',
      };
    }

    // 'retry' — {408, 429}, 5xx, or a network error.
    if (attempts >= MAX_ATTEMPTS) {
      return {
        action: 'fail',
        row: Object.assign({}, row, recorded, { attempts: attempts, status: 'failed' }),
        reason: 'max_attempts',
      };
    }

    return {
      action: 'reschedule',
      row: Object.assign({}, row, recorded, {
        attempts: attempts,
        status: 'retry-scheduled',
        next_attempt_at: now + backoffDelayMs(attempts),
      }),
    };
  }

  /**
   * What a response leaves on its row (SNOW-1262): its status and the
   * endpoint's JSON ``error``, bounded. A body that is not JSON, or has no
   * string ``error``, leaves ``error: null`` — the status alone still says
   * which kind of refusal it was.
   *
   * @param {?number} status The HTTP status, or null for a network error.
   * @param {*} body The parsed JSON body, or null.
   * @returns {{status: ?number, error: ?string}}
   */
  function failureDetail(status, body) {
    var error = body && typeof body.error === 'string' ? body.error.slice(0, ERROR_MAX_LENGTH) : null;
    return { status: typeof status === 'number' ? status : null, error: error };
  }

  /**
   * The acknowledgement a successful replay of ``row`` leaves in
   * ``meta:app`` (SNOW-1262), or null when the row names no ``kind`` —
   * nothing is waiting to hear about it, so nothing is written.
   *
   * @param {object} row The replayed row.
   * @param {*} body The parsed JSON response body, or null.
   * @param {number} now epoch milliseconds
   * @returns {?{key: string, value: {kind: string, uuid: ?string, at: number}}}
   */
  function ackRecord(row, body, now) {
    if (!row || !row.kind || !row.idempotency_key) return null;
    var uuid = body && typeof body.uuid === 'string' ? body.uuid : null;
    return {
      key: ACK_KEY_PREFIX + row.idempotency_key,
      value: { kind: row.kind, uuid: uuid, at: now },
    };
  }

  /**
   * Whether a ``meta:app`` record is an acknowledgement past its week.
   *
   * @param {*} record A ``meta:app`` row.
   * @param {number} now epoch milliseconds
   * @returns {boolean}
   */
  function ackExpired(record, now) {
    if (!record || typeof record.key !== 'string' || record.key.indexOf(ACK_KEY_PREFIX) !== 0) {
      return false;
    }
    var at = record.value && typeof record.value.at === 'number' ? record.value.at : 0;
    return now - at > ACK_TTL_MS;
  }

  self.pwaMutationQueueCore = Object.freeze({
    MAX_ATTEMPTS: MAX_ATTEMPTS,
    SYNC_TAG: SYNC_TAG,
    backoffDelayMs: backoffDelayMs,
    classifyStatus: classifyStatus,
    isRowEligible: isRowEligible,
    selectDrainBatch: selectDrainBatch,
    nextRowState: nextRowState,
    failureDetail: failureDetail,
    ackRecord: ackRecord,
    ackExpired: ackExpired,
    ACK_KEY_PREFIX: ACK_KEY_PREFIX,
    ACK_TTL_MS: ACK_TTL_MS,
    DISCARD_NOTICE_KEY: DISCARD_NOTICE_KEY,
    ERROR_MAX_LENGTH: ERROR_MAX_LENGTH,
  });
})();
