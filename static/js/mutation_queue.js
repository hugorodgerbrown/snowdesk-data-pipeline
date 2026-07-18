/*
 * static/js/mutation_queue.js — Client mutation-queue surface (stub, SNOW-384).
 *
 * SNOW-376 will fill in the actual queue behind this surface; call sites
 * should already use these methods so they get telemetry for free once
 * the queue is real.
 *
 * Spec §7 (mutation queue with exponential backoff and Background Sync)
 * is not implemented yet — every method below is a no-op with respect to
 * actual queueing / persistence / retry. What they DO today is emit the
 * matching telemetry event via ``window.pwaTelemetry?.emit`` so the
 * SNOW-384 dashboards have real signal to build against, and so that once
 * SNOW-376 lands a working queue behind this exact surface, every caller
 * that has already adopted ``window.pwaMutationQueue`` picks up correct
 * behaviour with no call-site change.
 *
 * Public API — attached to ``window.pwaMutationQueue``:
 *
 *   enqueue(operation)          No-op today. Emits ``pwa.mutation.enqueued``
 *                                with ``operation`` as the event properties.
 *   drain()                      No-op today (nothing is queued to drain).
 *                                Emits ``pwa.mutation.drained`` with
 *                                ``{count: 0}`` — the real implementation
 *                                will report the actual drained count.
 *   markFailed(operation, reason) No-op today. Emits
 *                                ``pwa.mutation.failed_permanent`` with
 *                                ``operation`` plus ``{reason}``.
 *
 * ``operation`` is caller-supplied metadata describing the mutation (e.g.
 * ``{method: 'POST', url: '/account/', idempotency_key: '...'}``).
 * Callers must not include any of the PII keys the server-side receiver
 * blocks (``email``, ``ip``, ``token``, ``credential_id``) — the same
 * rule as every other ``window.pwaTelemetry.emit`` call site.
 *
 * Every method is defensive — wrapped in try/catch, optional chaining on
 * ``window.pwaTelemetry?.emit`` — so a missing telemetry module (this
 * file is loaded on admin pages too) or a broken emit call can never
 * break the caller's mutation flow.
 */

(function () {
  'use strict';

  /**
   * Best-effort telemetry emit — never throws.
   * @param {string} event
   * @param {object} [properties]
   */
  function emitTelemetry(event, properties) {
    try {
      window.pwaTelemetry?.emit(event, properties || {});
    } catch (_err) {
      // Ignore — telemetry must never break a mutation call site.
    }
  }

  /**
   * No-op enqueue. Emits ``pwa.mutation.enqueued`` with the caller's
   * operation metadata.
   *
   * @param {object} [operation] Caller-supplied mutation descriptor.
   * @returns {Promise<void>}
   */
  async function enqueue(operation) {
    emitTelemetry('pwa.mutation.enqueued', operation || {});
  }

  /**
   * No-op drain. Emits ``pwa.mutation.drained`` with a zero count — there
   * is nothing queued to drain until SNOW-376 lands.
   *
   * @returns {Promise<void>}
   */
  async function drain() {
    emitTelemetry('pwa.mutation.drained', { count: 0 });
  }

  /**
   * No-op permanent-failure marker. Emits
   * ``pwa.mutation.failed_permanent`` — a critical event
   * (``telemetry.js`` ``CRITICAL_EVENTS``) — with the operation metadata
   * plus the failure reason.
   *
   * @param {object} [operation] Caller-supplied mutation descriptor.
   * @param {string} [reason] Short machine-identifier for the failure.
   * @returns {Promise<void>}
   */
  async function markFailed(operation, reason) {
    emitTelemetry('pwa.mutation.failed_permanent', {
      ...(operation || {}),
      reason: reason || 'unknown',
    });
  }

  Object.defineProperty(window, 'pwaMutationQueue', {
    value: Object.freeze({ enqueue, drain, markFailed }),
    writable: false,
    configurable: false,
  });
})();
