/*
 * tests/js/test_mutation_queue_core.js — Vitest unit tests for
 * static/js/mutation_queue_core.js (SNOW-376, SNOW-496).
 *
 * Covers the pure-core cases previously exercised indirectly through
 * tests/e2e/test_mutation_queue.py's backoff/classification assertions —
 * `backoffDelayMs`, `classifyStatus`, and `isRowEligible` are pure
 * functions of their arguments, so they're unit-tested directly here
 * rather than via a real IndexedDB row + fake HTTP status round trip.
 *
 * `mutation_queue_core.js` is a browser/worker IIFE that assigns a frozen
 * `self.pwaMutationQueueCore` — jsdom's global is `window`, which is also
 * `self` in a window context, so importing it for side effects is enough.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/mutation_queue_core.js';

const core = self.pwaMutationQueueCore;

describe('constants', () => {
  it('exposes MAX_ATTEMPTS and SYNC_TAG', () => {
    expect(core.MAX_ATTEMPTS).toBe(20);
    expect(core.SYNC_TAG).toBe('mutation-queue');
  });
});

describe('backoffDelayMs', () => {
  it('follows the 2/4/8/16/32s schedule for attempts 1-5', () => {
    expect(core.backoffDelayMs(1)).toBe(2000);
    expect(core.backoffDelayMs(2)).toBe(4000);
    expect(core.backoffDelayMs(3)).toBe(8000);
    expect(core.backoffDelayMs(4)).toBe(16000);
    expect(core.backoffDelayMs(5)).toBe(32000);
  });

  it('caps at 300s for attempt counts beyond the schedule', () => {
    expect(core.backoffDelayMs(9)).toBe(300000);
    expect(core.backoffDelayMs(20)).toBe(300000);
  });
});

describe('classifyStatus', () => {
  it('classifies 2xx as success', () => {
    expect(core.classifyStatus(200)).toBe('success');
    expect(core.classifyStatus(201)).toBe('success');
    expect(core.classifyStatus(204)).toBe('success');
    expect(core.classifyStatus(299)).toBe('success');
  });

  it('classifies 408 and 429 as retry', () => {
    expect(core.classifyStatus(408)).toBe('retry');
    expect(core.classifyStatus(429)).toBe('retry');
  });

  it('classifies every 5xx as retry', () => {
    expect(core.classifyStatus(500)).toBe('retry');
    expect(core.classifyStatus(503)).toBe('retry');
    expect(core.classifyStatus(599)).toBe('retry');
  });

  it('classifies every other 4xx as permanent', () => {
    for (const status of [400, 401, 403, 404, 409, 410, 422]) {
      expect(core.classifyStatus(status)).toBe('permanent');
    }
  });

  // SNOW-1132: a code the caller declared as "already applied" wins over
  // every other rule, and only for that caller.
  it('classifies a declared done status as success', () => {
    expect(core.classifyStatus(404, [404])).toBe('success');
    expect(core.classifyStatus(410, [404])).toBe('permanent');
    expect(core.classifyStatus(404, [])).toBe('permanent');
    expect(core.classifyStatus(404, undefined)).toBe('permanent');
  });

  it('classifies unexpected 1xx/3xx as retry rather than dropping silently', () => {
    expect(core.classifyStatus(100)).toBe('retry');
    expect(core.classifyStatus(302)).toBe('retry');
  });
});

describe('isRowEligible', () => {
  it('is false for a null/undefined row', () => {
    expect(core.isRowEligible(null, Date.now())).toBe(false);
    expect(core.isRowEligible(undefined, Date.now())).toBe(false);
  });

  it('is false for a row already marked failed', () => {
    expect(core.isRowEligible({ status: 'failed', next_attempt_at: 0 }, Date.now())).toBe(false);
  });

  it('is true when next_attempt_at has elapsed', () => {
    const now = Date.now();
    expect(core.isRowEligible({ status: 'queued', next_attempt_at: now - 1000 }, now)).toBe(true);
    expect(core.isRowEligible({ status: 'retry-scheduled', next_attempt_at: now }, now)).toBe(true);
  });

  it('is false when next_attempt_at is still in the future', () => {
    const now = Date.now();
    expect(
      core.isRowEligible({ status: 'retry-scheduled', next_attempt_at: now + 5000 }, now),
    ).toBe(false);
  });

  it('treats a missing next_attempt_at as due immediately (0)', () => {
    expect(core.isRowEligible({ status: 'queued' }, Date.now())).toBe(true);
  });
});

describe('selectDrainBatch (SNOW-617)', () => {
  const NOW = 1_000_000;

  /**
   * @param {number} id
   * @param {object} [extra]
   * @returns {object}
   */
  function row(id, extra) {
    return Object.assign({ id, status: 'queued', next_attempt_at: 0 }, extra || {});
  }

  it('does not let failed rows fill the batch', () => {
    // The starvation bug: drain() read the oldest BATCH_SIZE rows and
    // filtered THOSE. A permanently-failed row is never deleted (the nav
    // badge's error state is defined on its presence), so with a batch's
    // worth at the head of the store every eligible row behind them
    // starved — silently, with no error and no telemetry.
    const rows = [];
    for (let i = 0; i < 50; i += 1) rows.push(row(i, { status: 'failed' }));
    rows.push(row(99));

    const batch = core.selectDrainBatch(rows, NOW, 50);

    expect(batch).toHaveLength(1);
    expect(batch[0].id).toBe(99);
  });

  it('still caps the batch', () => {
    const rows = [];
    for (let i = 0; i < 120; i += 1) rows.push(row(i));

    expect(core.selectDrainBatch(rows, NOW, 50)).toHaveLength(50);
  });

  it('keeps store order, so replay order is stable', () => {
    const rows = [row(1), row(2, { status: 'failed' }), row(3)];

    expect(core.selectDrainBatch(rows, NOW, 50).map((r) => r.id)).toEqual([1, 3]);
  });

  it('skips a row still inside its backoff window', () => {
    const rows = [row(1, { next_attempt_at: NOW + 1 }), row(2, { next_attempt_at: NOW })];

    expect(core.selectDrainBatch(rows, NOW, 50).map((r) => r.id)).toEqual([2]);
  });

  it('tolerates an empty or missing store', () => {
    expect(core.selectDrainBatch([], NOW, 50)).toEqual([]);
    expect(core.selectDrainBatch(null, NOW, 50)).toEqual([]);
  });
});

describe('nextRowState (SNOW-617)', () => {
  const NOW = 1_000_000;
  const ROW = { id: 7, method: 'POST', url: '/x/', attempts: 0, status: 'queued' };

  it('deletes on success', () => {
    expect(core.nextRowState(ROW, 'success', NOW)).toEqual({ action: 'delete' });
  });

  it('fails a permanent 4xx on the first attempt', () => {
    const next = core.nextRowState(ROW, 'permanent', NOW);

    expect(next.action).toBe('fail');
    expect(next.reason).toBe('permanent_4xx');
    // attempts counts the attempt that just happened, which is what lets
    // the telemetry tell "failed first try" from "never tried".
    expect(next.row.attempts).toBe(1);
    expect(next.row.status).toBe('failed');
  });

  it('reschedules a retryable outcome with its backoff', () => {
    const next = core.nextRowState(ROW, 'retry', NOW);

    expect(next.action).toBe('reschedule');
    expect(next.row.status).toBe('retry-scheduled');
    expect(next.row.attempts).toBe(1);
    expect(next.row.next_attempt_at).toBe(NOW + core.backoffDelayMs(1));
  });

  it('fails a retryable outcome once it reaches MAX_ATTEMPTS', () => {
    const nearly = { ...ROW, attempts: core.MAX_ATTEMPTS - 1 };

    const next = core.nextRowState(nearly, 'retry', NOW);

    expect(next.action).toBe('fail');
    expect(next.reason).toBe('max_attempts');
    expect(next.row.attempts).toBe(core.MAX_ATTEMPTS);
  });

  it('reschedules one attempt short of the ceiling', () => {
    const nearly = { ...ROW, attempts: core.MAX_ATTEMPTS - 2 };

    expect(core.nextRowState(nearly, 'retry', NOW).action).toBe('reschedule');
  });

  it('never mutates the row it was handed', () => {
    const original = { ...ROW };

    core.nextRowState(original, 'retry', NOW);

    // The caller still holds the row it read; the transition hands back a
    // new one to persist.
    expect(original).toEqual(ROW);
  });

  it('treats a row with no attempts field as never attempted', () => {
    const bare = { id: 1 };

    expect(core.nextRowState(bare, 'retry', NOW).row.attempts).toBe(1);
  });
});

describe('what a response leaves on its row (SNOW-1262)', () => {
  const NOW = 1_000_000;
  const ROW = { id: 1, attempts: 0, status: 'queued', idempotency_key: 'k' };

  it("records a permanent refusal's status and the endpoint's error", () => {
    const next = core.nextRowState(ROW, 'permanent', NOW, core.failureDetail(409, { error: 'route_limit_reached' }));

    expect(next.row).toMatchObject({ status: 'failed', last_status: 409, last_error: 'route_limit_reached' });
  });

  it('records the status of a retry too, so the last answer is always kept', () => {
    const next = core.nextRowState(ROW, 'retry', NOW, core.failureDetail(503, null));

    expect(next.row).toMatchObject({ status: 'retry-scheduled', last_status: 503, last_error: null });
  });

  it('records a network error as no status at all', () => {
    const next = core.nextRowState(ROW, 'retry', NOW, core.failureDetail(null, null));

    expect(next.row.last_status).toBe(null);
  });

  it('leaves the row its old shape when no detail is given', () => {
    const next = core.nextRowState(ROW, 'permanent', NOW);

    expect('last_status' in next.row).toBe(false);
    expect('last_error' in next.row).toBe(false);
  });

  it('bounds the error and ignores one that is not a string', () => {
    const long = 'x'.repeat(core.ERROR_MAX_LENGTH + 50);

    expect(core.failureDetail(400, { error: long }).error).toHaveLength(core.ERROR_MAX_LENGTH);
    expect(core.failureDetail(400, { error: { nested: true } }).error).toBe(null);
    expect(core.failureDetail(400, 'not json').error).toBe(null);
  });
});

describe('acknowledgements (SNOW-1262)', () => {
  const NOW = 1_000_000;

  it('acknowledges a row with a kind, keyed on its Idempotency-Key, with the uuid', () => {
    const record = core.ackRecord({ kind: 'route-create', idempotency_key: 'k-1' }, { uuid: 'u-1' }, NOW);

    expect(record).toEqual({
      key: core.ACK_KEY_PREFIX + 'k-1',
      value: { kind: 'route-create', uuid: 'u-1', at: NOW },
    });
  });

  it('acknowledges without a uuid when the body had none', () => {
    expect(core.ackRecord({ kind: 'route-create', idempotency_key: 'k' }, null, NOW).value.uuid).toBe(null);
  });

  it('writes nothing for a row that names no kind', () => {
    expect(core.ackRecord({ idempotency_key: 'k' }, { uuid: 'u' }, NOW)).toBe(null);
  });

  it('expires an acknowledgement after a week, and nothing else', () => {
    const fresh = { key: core.ACK_KEY_PREFIX + 'a', value: { at: NOW } };
    const old = { key: core.ACK_KEY_PREFIX + 'b', value: { at: NOW - core.ACK_TTL_MS - 1 } };
    const other = { key: 'mutations.principal', value: 'user' };

    expect(core.ACK_TTL_MS).toBe(7 * 24 * 60 * 60 * 1000);
    expect(core.ackExpired(fresh, NOW)).toBe(false);
    expect(core.ackExpired(old, NOW)).toBe(true);
    expect(core.ackExpired(other, NOW + core.ACK_TTL_MS * 2)).toBe(false);
  });
});

describe("the idempotency middleware's two 409s (SNOW-1262)", () => {
  it('retries the in-flight 409: the original request is still in the view', () => {
    expect(core.classifyStatus(409, [], core.IN_FLIGHT_ERROR)).toBe('retry');
    expect(core.IN_FLIGHT_ERROR).toBe('idempotency_in_flight');
  });

  it('fails the reused-key 409, a bare 409 and the route cap for good', () => {
    expect(core.classifyStatus(409, [], core.KEY_REUSED_ERROR)).toBe('permanent');
    expect(core.classifyStatus(409, [], null)).toBe('permanent');
    expect(core.classifyStatus(409, [], 'route_limit_reached')).toBe('permanent');
  });
});
