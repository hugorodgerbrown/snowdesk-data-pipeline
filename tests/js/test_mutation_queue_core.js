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
