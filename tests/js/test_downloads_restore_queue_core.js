/*
 * tests/js/test_downloads_restore_queue_core.js — Vitest tests for
 * static/js/downloads_restore_queue_core.js (SNOW-1140).
 *
 * The "Download all" queue's ordering and stop rules, driven through
 * injected dependencies: a fake page-wide lock, a fake set of areas on this
 * device and a fake start. Fake timers stand in for the wait's poll and
 * bound, so nothing here waits on the clock.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/downloads_restore_queue_core.js';

const core = window.pwaDownloadsRestoreQueueCore;

/**
 * A page with one download lock. `start` takes it and records the area
 * once `finish()` is called, as a real run does when it settles.
 */
function fakePage(options = {}) {
  const page = {
    busy: false,
    online: true,
    present: new Set(options.present || []),
    listeners: new Set(),
    running: null,
    calls: [],
    outcomes: options.outcomes || {},
  };
  page.deps = {
    online: () => page.online,
    isBusy: () => page.busy,
    subscribe: (listener) => {
      page.listeners.add(listener);
      return () => page.listeners.delete(listener);
    },
    presentIds: async () => new Set(page.present),
    start: vi.fn(async (item) => {
      page.calls.push(item.areaId);
      // Concurrency is the bug this guards: a start while the lock is held.
      if (page.busy) return { started: false, reason: 'busy' };
      const outcome = page.outcomes[item.areaId];
      if (outcome) return outcome;
      page.busy = true;
      page.running = item.areaId;
      return { started: true, reason: '' };
    }),
  };
  /** Settle the running download: record it (or not) and free the lock. */
  page.finish = (recorded = true) => {
    if (recorded && page.running) page.present.add(page.running);
    page.running = null;
    page.busy = false;
    for (const listener of page.listeners) listener();
  };
  return page;
}

/** Flush pending promise callbacks without advancing the clock. */
async function flush() {
  for (let i = 0; i < 10; i += 1) await Promise.resolve();
}

const ITEMS = [{ areaId: 'region-CH-1' }, { areaId: 'custom-a' }, { areaId: 'region-CH-2' }];

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('runQueue', () => {
  it('runs every item in order, one at a time', async () => {
    const page = fakePage();
    const done = core.runQueue(ITEMS, page.deps);

    await flush();
    expect(page.calls).toEqual(['region-CH-1']);
    // Still running: nothing else may start.
    await vi.advanceTimersByTimeAsync(10000);
    expect(page.calls).toEqual(['region-CH-1']);

    page.finish();
    await flush();
    expect(page.calls).toEqual(['region-CH-1', 'custom-a']);

    page.finish();
    await flush();
    expect(page.calls).toEqual(['region-CH-1', 'custom-a', 'region-CH-2']);

    page.finish();
    const result = await done;
    expect(result).toMatchObject({ started: 3, skipped: 0, stopped: null });
  });

  it('never starts while the lock is held, even by another download', async () => {
    const page = fakePage();
    page.busy = true;
    const done = core.runQueue(ITEMS.slice(0, 1), page.deps);
    await flush();
    expect(page.calls).toEqual([]);

    page.finish(false);
    await flush();
    expect(page.calls).toEqual(['region-CH-1']);
    page.finish();
    await done;
  });

  it('notices a lock released without a broadcast, through the poll', async () => {
    // A failed run records nothing, so nothing is broadcast.
    const page = fakePage();
    page.busy = true;
    const done = core.runQueue(ITEMS.slice(0, 1), page.deps);
    await flush();
    page.busy = false;
    expect(page.calls).toEqual([]);

    await vi.advanceTimersByTimeAsync(core.DEFAULT_POLL_MS);
    expect(page.calls).toEqual(['region-CH-1']);
    page.finish();
    await done;
  });

  it('stops at the first refusal with its own reason, leaving the rest', async () => {
    const page = fakePage({ outcomes: { 'custom-a': { started: false, reason: 'over-ceiling' } } });
    const done = core.runQueue(ITEMS, page.deps);
    await flush();
    page.finish();

    const result = await done;
    expect(page.calls).toEqual(['region-CH-1', 'custom-a']);
    expect(result).toMatchObject({ started: 1, stopped: 'over-ceiling', silent: false });
    expect(result.item.areaId).toBe('custom-a');
  });

  it('stops quietly when the previous run settled without its area', async () => {
    // It failed or was declined — the run's own toast (or the user's own
    // "no") already said so.
    const page = fakePage();
    const done = core.runQueue(ITEMS, page.deps);
    await flush();
    page.finish(false);

    const result = await done;
    expect(page.calls).toEqual(['region-CH-1']);
    expect(result).toMatchObject({ started: 1, stopped: 'failed', silent: true });
  });

  it('stops quietly when a restore settles unrecorded', async () => {
    const page = fakePage({ outcomes: { 'region-CH-1': { started: true, reason: '', ok: false } } });
    const result = await core.runQueue(ITEMS, page.deps);
    expect(page.calls).toEqual(['region-CH-1']);
    expect(result).toMatchObject({ stopped: 'failed', silent: true });
  });

  it('skips an area that became present while the queue ran', async () => {
    const page = fakePage();
    const done = core.runQueue(ITEMS, page.deps);
    await flush();
    // Restored from its own row, or in another tab, meanwhile.
    page.present.add('custom-a');
    page.finish();
    await flush();
    expect(page.calls).toEqual(['region-CH-1', 'region-CH-2']);
    page.finish();

    expect(await done).toMatchObject({ started: 2, skipped: 1, stopped: null });
  });

  it('stops when the network mode goes offline', async () => {
    const page = fakePage();
    const done = core.runQueue(ITEMS, page.deps);
    await flush();
    page.online = false;
    page.finish();

    const result = await done;
    expect(page.calls).toEqual(['region-CH-1']);
    expect(result).toMatchObject({ stopped: 'offline' });
  });

  it('starts nothing offline', async () => {
    const page = fakePage();
    page.online = false;
    const result = await core.runQueue(ITEMS, page.deps);
    expect(page.calls).toEqual([]);
    expect(result.stopped).toBe('offline');
  });

  it('gives up on a run that never releases the lock', async () => {
    const page = fakePage();
    const done = core.runQueue(ITEMS, page.deps, { timeoutMs: 60000 });
    await flush();

    await vi.advanceTimersByTimeAsync(60000);
    const result = await done;
    expect(page.calls).toEqual(['region-CH-1']);
    expect(result).toMatchObject({ stopped: 'timeout' });
    // And it let go of the broadcast.
    expect(page.listeners.size).toBe(0);
  });

  it('stops rather than rejects when a dependency throws', async () => {
    const page = fakePage();
    page.deps.presentIds = async () => {
      throw new Error('IndexedDB gone');
    };
    const result = await core.runQueue(ITEMS, page.deps);
    expect(result.stopped).toBe('failed');
  });
});

describe('waitForIdle', () => {
  it('resolves at once when nothing is running', async () => {
    const page = fakePage();
    await expect(core.waitForIdle(page.deps)).resolves.toBe(true);
  });

  it('resolves on the broadcast once the lock is free', async () => {
    const page = fakePage();
    page.busy = true;
    const waiting = core.waitForIdle(page.deps, { pollMs: 1e9 });
    page.finish(false);
    await expect(waiting).resolves.toBe(true);
  });

  it('ignores a broadcast while the lock is still held', async () => {
    const page = fakePage();
    page.busy = true;
    let settled = false;
    const waiting = core.waitForIdle(page.deps, { timeoutMs: 5000, pollMs: 1e9 }).then((v) => {
      settled = true;
      return v;
    });
    for (const listener of page.listeners) listener();
    await flush();
    expect(settled).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);
    await expect(waiting).resolves.toBe(false);
  });
});
