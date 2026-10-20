/*
 * tests/js/test_offline_ready_core.js — Vitest tests for
 * static/js/offline_ready_core.js (SNOW-1141).
 *
 * The one "Get ready for offline" press: the queue drains first, then every
 * area runs in turn, and the result is one summary line plus a queue line
 * when edits are left behind. Every effect is injected, so nothing here
 * touches IndexedDB, Cache Storage or the network.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/offline_ready_core.js';

const core = window.pwaOfflineReadyCore;

const STRINGS = {
  'ready-running': 'Getting ready…',
  'ready-progress': 'Getting ready… (%(n)s of %(total)s)',
  'ready-all': 'Ready — %(total)s areas up to date',
  'ready-all-one': 'Ready — 1 area up to date',
  'ready-some': '%(ready)s of %(total)s ready',
  'queue-waiting': '%(n)s changes still waiting to send',
  'queue-waiting-one': '1 change still waiting to send',
};

describe('run', () => {
  it('drains the queue before the first area, then runs each area in order', async () => {
    const calls = [];
    const result = await core.run({
      drain: async () => {
        calls.push('drain');
      },
      waiting: async () => {
        calls.push('count');
        return 0;
      },
      areas: ['region-CH-4115', 'custom-a1', 'region-CH-4116'],
      syncOne: async (id) => {
        calls.push(id);
        return 'ready';
      },
    });

    expect(calls).toEqual(['drain', 'count', 'region-CH-4115', 'custom-a1', 'region-CH-4116']);
    expect(result).toMatchObject({ total: 3, ready: 3, waiting: 0 });
  });

  it('runs one area at a time, never two at once', async () => {
    let inFlight = 0;
    let most = 0;
    await core.run({
      drain: () => undefined,
      waiting: () => 0,
      areas: ['a', 'b', 'c'],
      syncOne: async () => {
        inFlight += 1;
        most = Math.max(most, inFlight);
        await Promise.resolve();
        inFlight -= 1;
        return 'ready';
      },
    });
    expect(most).toBe(1);
  });

  it('reports each step, so the button can count up', async () => {
    const steps = [];
    await core.run({
      drain: () => undefined,
      waiting: () => 0,
      areas: ['a', 'b'],
      syncOne: async (id) => (id === 'a' ? 'ready' : 'absent'),
      onStep: (step) => steps.push(step),
    });
    expect(steps).toEqual([
      { phase: 'queue' },
      { phase: 'area', n: 1, total: 2, id: 'a' },
      { phase: 'done', id: 'a', outcome: 'ready' },
      { phase: 'area', n: 2, total: 2, id: 'b' },
      { phase: 'done', id: 'b', outcome: 'absent' },
    ]);
  });

  it('keeps going past an area that throws, and counts it as not ready', async () => {
    const result = await core.run({
      drain: () => undefined,
      waiting: () => 0,
      areas: ['a', 'b'],
      syncOne: async (id) => {
        if (id === 'a') throw new Error('boom');
        return 'ready';
      },
    });
    expect(result.outcomes).toEqual([
      { id: 'a', outcome: 'partial' },
      { id: 'b', outcome: 'ready' },
    ]);
    expect(result.ready).toBe(1);
  });

  it('does not let a hung drain stop the areas', async () => {
    // A dead radio hangs rather than rejects; the areas behind it must still
    // be tried once the bound has passed.
    const syncOne = vi.fn(async () => 'ready');
    const result = await core.run({
      drain: () => new Promise(() => {}),
      waiting: () => 2,
      areas: ['a'],
      syncOne,
      wait: () => Promise.resolve(),
    });
    expect(syncOne).toHaveBeenCalledWith('a');
    expect(result.waiting).toBe(2);
  });

  it('survives a drain that throws', async () => {
    const result = await core.run({
      drain: () => {
        throw new Error('no queue');
      },
      waiting: () => 0,
      areas: ['a'],
      syncOne: async () => 'ready',
    });
    expect(result.ready).toBe(1);
  });
});

describe('areaOutcome', () => {
  it.each([
    [{ tiles: 'none', content: true }, 'ready'],
    [{ tiles: 'ok', content: true }, 'ready'],
    [{ tiles: 'none', content: false }, 'partial'],
    [{ tiles: 'failed', content: true }, 'partial'],
    [{ tiles: 'unknown', content: true }, 'partial'],
    [{ tiles: 'absent', content: false }, 'absent'],
    [null, 'partial'],
  ])('reads %j as %s', (result, outcome) => {
    expect(core.areaOutcome(result)).toBe(outcome);
  });
});

describe('waitingCount', () => {
  it('counts the rows still to send and not the ones that failed for good', () => {
    expect(
      core.waitingCount([{ status: 'pending' }, { status: 'failed' }, { status: 'retry' }]),
    ).toBe(2);
  });

  it('is zero for anything that is not a list', () => {
    expect(core.waitingCount(undefined)).toBe(0);
  });
});

describe('describe', () => {
  it('says every area is ready when every area is', () => {
    expect(core.describe({ total: 5, ready: 5, waiting: 0 }, STRINGS)).toEqual({
      summary: 'Ready — 5 areas up to date',
      queue: '',
    });
  });

  it('has its own sentence for a single area', () => {
    expect(core.describe({ total: 1, ready: 1, waiting: 0 }, STRINGS).summary).toBe(
      'Ready — 1 area up to date',
    );
  });

  it('counts the ready ones when some fell short', () => {
    expect(core.describe({ total: 5, ready: 3, waiting: 0 }, STRINGS).summary).toBe(
      '3 of 5 ready',
    );
  });

  it('adds a queue line only when edits were left behind', () => {
    expect(core.describe({ total: 2, ready: 2, waiting: 2 }, STRINGS).queue).toBe(
      '2 changes still waiting to send',
    );
    expect(core.describe({ total: 2, ready: 2, waiting: 1 }, STRINGS).queue).toBe(
      '1 change still waiting to send',
    );
  });
});

describe('progressText', () => {
  it('counts the areas while they run', () => {
    expect(core.progressText({ phase: 'area', n: 2, total: 5, id: 'a' }, STRINGS)).toBe(
      'Getting ready… (2 of 5)',
    );
  });

  it('has no count while the queue drains', () => {
    expect(core.progressText({ phase: 'queue' }, STRINGS)).toBe('Getting ready…');
  });
});
