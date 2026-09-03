/*
 * tests/js/test_debug_log.js — Vitest unit tests for static/js/debug_log.js
 * (SNOW-812).
 *
 * The recorder's contract is almost entirely about what it does NOT do.
 * It sits behind every silent fallback in map.js and sw.js, so a bug here
 * does not produce a wrong log line — it produces a slower page, a leaking
 * buffer, or an unhandled rejection inside a `catch` block that was
 * written specifically never to throw. None of those is visible in a
 * browser test, and all of them are directly assertable here.
 *
 * The module is an IIFE that reads localStorage at parse time, so each
 * behaviour that depends on the STARTING state re-imports it with the
 * module registry reset.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MODULE = '../../static/js/debug_log.js';

/**
 * Re-import debug_log.js with a fresh module registry, so its parse-time
 * localStorage read runs again against whatever the test has just set.
 */
async function loadRecorder() {
  vi.resetModules();
  delete window.pwaDebugLog;
  await import(MODULE);
  return window.pwaDebugLog;
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  delete window.pwaDb;
  delete window.pwaDebugLog;
});

describe('recording is off until asked for', () => {
  it('drops every line, so an unflagged device pays nothing', async () => {
    const log = await loadRecorder();
    log.record('map', 'region.focus', { id: 'CH-2145' });
    expect(log.isEnabled()).toBe(false);
    expect(log.entries()).toHaveLength(0);
  });

  it('exposes `on` as a cheap guard for callers with expensive detail', async () => {
    const log = await loadRecorder();
    expect(log.on).toBe(false);
    log.setEnabled(true);
    expect(log.on).toBe(true);
  });

  it('restores a previously-persisted opt-in at parse time', async () => {
    localStorage.setItem('pwa.debugLog.enabled', '1');
    const log = await loadRecorder();
    // Enabled from the first statement, so the boot sequence the reader
    // came for is actually in the buffer rather than starting one tick late.
    expect(log.isEnabled()).toBe(true);
    expect(log.entries().length).toBeGreaterThan(0);
  });
});

describe('the ring buffer', () => {
  it('returns newest first — the order a phone screen wants', async () => {
    const log = await loadRecorder();
    log.setEnabled(true);
    log.clear();
    log.record('map', 'first');
    log.record('map', 'second');
    expect(log.entries().map((r) => r.evt)).toEqual(['second', 'first']);
  });

  it('caps at 500 rows rather than growing for the session', async () => {
    const log = await loadRecorder();
    log.setEnabled(true);
    log.clear();
    for (let i = 0; i < 600; i += 1) log.record('map', `evt-${i}`);
    const rows = log.entries();
    expect(rows).toHaveLength(500);
    // The OLDEST are the ones discarded.
    expect(rows[0].evt).toBe('evt-599');
    expect(rows[499].evt).toBe('evt-100');
  });
});

describe('persistence', () => {
  it('batches a burst into one transaction rather than one per line', async () => {
    const appendDebugLogBatch = vi.fn().mockResolvedValue(undefined);
    window.pwaDb = { appendDebugLogBatch, put: vi.fn().mockResolvedValue(undefined) };
    const log = await loadRecorder();
    log.setEnabled(true);
    for (let i = 0; i < 50; i += 1) log.record('sw', `evt-${i}`);
    expect(appendDebugLogBatch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1000);
    expect(appendDebugLogBatch).toHaveBeenCalledTimes(1);
    // 50 lines plus the recording.started line setEnabled emits.
    expect(appendDebugLogBatch.mock.calls[0][0]).toHaveLength(51);
  });

  it('swallows an IndexedDB failure instead of rejecting into a catch block', async () => {
    window.pwaDb = {
      appendDebugLogBatch: vi.fn().mockRejectedValue(new Error('quota')),
      put: vi.fn().mockResolvedValue(undefined),
    };
    const log = await loadRecorder();
    log.setEnabled(true);
    log.record('sw', 'evt');
    // The assertion is that this does not produce an unhandled rejection:
    // every caller of record() is inside a `catch` written never to throw,
    // and a rejecting recorder would turn a swallowed cache miss into a
    // page error.
    await expect(vi.advanceTimersByTimeAsync(1000)).resolves.not.toThrow();
    // The in-memory ring is unaffected, so the panel still works offline
    // of its own storage.
    expect(log.entries()).toHaveLength(2);
  });

  it('does not retry a dropped batch, so a dead DB cannot grow the buffer', async () => {
    const appendDebugLogBatch = vi.fn().mockRejectedValue(new Error('gone'));
    window.pwaDb = { appendDebugLogBatch, put: vi.fn().mockResolvedValue(undefined) };
    const log = await loadRecorder();
    log.setEnabled(true);
    log.record('sw', 'one');
    await vi.advanceTimersByTimeAsync(1000);
    log.record('sw', 'two');
    await vi.advanceTimersByTimeAsync(1000);
    // Second flush carries only the new line — the failed one is gone, not
    // re-queued in front of it.
    expect(appendDebugLogBatch.mock.calls[1][0].map((r) => r.evt)).toEqual(['two']);
  });
});

describe('switching recording off', () => {
  it('empties the buffer and the store, so the next reader starts clean', async () => {
    const clearDebugLog = vi.fn().mockResolvedValue(undefined);
    window.pwaDb = {
      appendDebugLogBatch: vi.fn().mockResolvedValue(undefined),
      put: vi.fn().mockResolvedValue(undefined),
      clearDebugLog,
    };
    const log = await loadRecorder();
    log.setEnabled(true);
    log.record('map', 'evt');
    log.setEnabled(false);
    expect(log.entries()).toHaveLength(0);
    expect(clearDebugLog).toHaveBeenCalled();
  });

  it('mirrors the flag to meta:app so a restarted worker can rehydrate it', async () => {
    const put = vi.fn().mockResolvedValue(undefined);
    window.pwaDb = { appendDebugLogBatch: vi.fn().mockResolvedValue(undefined), put };
    const log = await loadRecorder();
    log.setEnabled(true);
    // The durable half of the three-way mirror — the one that survives the
    // browser terminating an idle service worker.
    expect(put).toHaveBeenCalledWith('meta:app', { key: 'debug.enabled', value: true });
  });
});

describe('format()', () => {
  it('renders one fixed-width line per row, k=v detail', async () => {
    const log = await loadRecorder();
    const at = new Date(2026, 0, 2, 10, 42, 3, 7).getTime();
    const text = log.format([
      { at, src: 'sw', evt: 'pinned.search', detail: { result: 'miss', searched: 1 } },
    ]);
    expect(text).toContain('10:42:03.007');
    expect(text).toContain('result=miss');
    expect(text).toContain('searched=1');
  });

  it('handles a row with no detail without printing "null"', async () => {
    const log = await loadRecorder();
    const text = log.format([{ at: Date.now(), src: 'map', evt: 'boot', detail: null }]);
    expect(text).not.toContain('null');
    expect(text.endsWith('boot')).toBe(true);
  });
});
