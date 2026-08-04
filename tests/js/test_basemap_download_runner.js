/*
 * tests/js/test_basemap_download_runner.js — Vitest unit tests for
 * static/js/basemap_download_runner.js (SNOW-611).
 *
 * The point of extracting the run was to make its ORDER testable. Both map
 * download controls used to spell the sequence out themselves, and one of
 * the two drifted — SNOW-607 (D1) had to move the tile-template check back
 * ahead of the eviction, because a run that aborted after evicting left
 * the user with neither the area they had sacrificed nor the download they
 * asked for.
 *
 * Every dependency is a fake, and each one records the order it was called
 * in, so the abort-before-evict property can be asserted directly rather
 * than inferred from a rendered roundel.
 *
 * `basemap_download_runner.js` is a browser IIFE with no exports —
 * importing it for side effects publishes `self.pwaBasemapDownloadRunner`.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_download_runner.js';

/** Calls recorded across every fake, in the order they happened. */
let calls;

/**
 * Build a dependency bundle whose every member succeeds, recording each
 * call into `calls`. Tests override the one member they are about.
 *
 * @param {Object} [overrides]
 * @returns {Object}
 */
function deps(overrides) {
  const base = {
    clearError: vi.fn(() => calls.push('clearError')),
    revealError: vi.fn((reason) => calls.push(`revealError:${reason}`)),
    fitsQuota: vi.fn(async () => {
      calls.push('fitsQuota');
      return true;
    }),
    core: vi.fn(() => ({
      tileGridPlan: () => ({ urls: ['/tile/1', '/tile/2'], cells: [] }),
      CUSTOM_AREA_ID: 'custom',
    })),
    tileTemplate: vi.fn(() => {
      calls.push('tileTemplate');
      return 'https://tiles/{z}/{x}/{y}.png';
    }),
    planBudget: vi.fn(async () => {
      calls.push('planBudget');
      return null;
    }),
    confirmEviction: vi.fn(async () => {
      calls.push('confirmEviction');
      return true;
    }),
    evict: vi.fn(async () => {
      calls.push('evict');
    }),
    feedUrls: vi.fn(() => ['/api/feed']),
    progressGrid: vi.fn(() => ({
      update: vi.fn(),
      finish: vi.fn(async () => {}),
    })),
    warmCache: vi.fn(async () => {
      calls.push('warmCache');
      return { ok: 3, failed: 0, bytes: 1024 };
    }),
    isOnline: vi.fn(() => true),
  };
  return Object.assign(base, overrides || {});
}

/**
 * Build a run's options, recording paint calls and the blob load.
 *
 * @param {Object} [overrides]
 * @returns {Object}
 */
function options(overrides) {
  const base = {
    areaId: 'region:ch-4115',
    mb: 12,
    loadBlob: vi.fn(async () => {
      calls.push('loadBlob');
      return { z: 12, band: 'micro', mb: 12 };
    }),
    paint: vi.fn((state) => calls.push(`paint:${state}`)),
    finish: vi.fn(async () => {
      calls.push('finish');
    }),
  };
  return Object.assign(base, overrides || {});
}

/**
 * A budget plan that asks for one eviction and can be satisfied.
 *
 * @returns {Object}
 */
function evictingPlan() {
  return {
    impossible: false,
    evict: ['region:ch-1000'],
    areasById: new Map([['region:ch-1000', { id: 'region:ch-1000', name: 'Valais' }]]),
  };
}

/**
 * Run one download and let the fire-and-forget warm-cache tail settle.
 *
 * `run` resolves once the run is DISPATCHED; `finish` is called off the
 * warm-cache promise, so a test asserting on it has to yield first.
 *
 * @param {Object} d
 * @param {Object} o
 * @returns {Promise<void>}
 */
async function runAndSettle(d, o) {
  await self.pwaBasemapDownloadRunner.run(d, o);
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  calls = [];
});

describe('pre-flight ordering — the SNOW-607 regression', () => {
  it('never evicts when the tile template is missing', async () => {
    const d = deps({
      tileTemplate: () => {
        calls.push('tileTemplate');
        return null;
      },
      planBudget: vi.fn(async () => {
        calls.push('planBudget');
        return evictingPlan();
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    // This is the whole point of the extraction: a run that cannot succeed
    // must not have destroyed another area's bucket on the way to finding
    // out.
    expect(d.evict).not.toHaveBeenCalled();
    expect(d.confirmEviction).not.toHaveBeenCalled();
    expect(d.warmCache).not.toHaveBeenCalled();
    expect(o.paint).toHaveBeenLastCalledWith('error');
    expect(d.revealError).toHaveBeenCalledWith(null);
  });

  it('never evicts when the quota pre-flight fails', async () => {
    const d = deps({
      fitsQuota: async () => {
        calls.push('fitsQuota');
        return false;
      },
      planBudget: vi.fn(async () => evictingPlan()),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(d.evict).not.toHaveBeenCalled();
    // The quota check comes before the blob is fetched — the run costs a
    // click, not a whole download.
    expect(o.loadBlob).not.toHaveBeenCalled();
    expect(d.revealError).toHaveBeenCalledWith('quota');
  });

  it('never evicts when the blob fetch fails', async () => {
    const d = deps({ planBudget: vi.fn(async () => evictingPlan()) });
    const o = options({
      loadBlob: async () => {
        calls.push('loadBlob');
        throw new Error('region-basemap-tiles 502');
      },
    });

    await runAndSettle(d, o);

    expect(d.evict).not.toHaveBeenCalled();
    expect(d.confirmEviction).not.toHaveBeenCalled();
    expect(d.revealError).toHaveBeenCalledWith(null);
  });

  it('puts eviction last, after every abortable step has passed', async () => {
    const d = deps({
      planBudget: vi.fn(async () => {
        calls.push('planBudget');
        return evictingPlan();
      }),
    });

    await runAndSettle(d, options());

    expect(calls).toEqual([
      'clearError',
      'paint:busy',
      'fitsQuota',
      'tileTemplate',
      'planBudget',
      'loadBlob',
      'confirmEviction',
      'evict',
      'warmCache',
      'finish',
    ]);
  });

  it('claims busy before the first await, so a second click cannot start a run', async () => {
    const d = deps();
    const o = options();

    const pending = self.pwaBasemapDownloadRunner.run(d, o);

    // Synchronous — nothing has been awaited yet, and the caller's
    // re-entrancy guard reads exactly this state.
    expect(o.paint).toHaveBeenCalledWith('busy', 0);
    await pending;
  });
});

describe('budget ceiling', () => {
  it('refuses a run no eviction could make fit, before fetching the blob', async () => {
    const d = deps({
      planBudget: vi.fn(async () => {
        calls.push('planBudget');
        return { impossible: true, evict: [], areasById: new Map() };
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.loadBlob).not.toHaveBeenCalled();
    expect(d.evict).not.toHaveBeenCalled();
    expect(d.revealError).toHaveBeenCalledWith('budget');
    expect(o.paint).toHaveBeenLastCalledWith('error');
  });

  it('abandons the run when the user declines the eviction', async () => {
    const d = deps({
      planBudget: vi.fn(async () => evictingPlan()),
      confirmEviction: vi.fn(async () => {
        calls.push('confirmEviction');
        return false;
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(d.evict).not.toHaveBeenCalled();
    expect(d.warmCache).not.toHaveBeenCalled();
    // Declining is not a failure — the roundel goes back to actionable.
    expect(o.paint).toHaveBeenLastCalledWith('idle');
    expect(d.revealError).not.toHaveBeenCalled();
  });

  it('paints offline rather than idle when the decline happens offline', async () => {
    const d = deps({
      planBudget: vi.fn(async () => evictingPlan()),
      confirmEviction: vi.fn(async () => false),
      isOnline: () => false,
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.paint).toHaveBeenLastCalledWith('offline');
  });

  it('runs with no plan at all — no core means no budget arithmetic', async () => {
    const d = deps({ planBudget: vi.fn(async () => null) });

    await runAndSettle(d, options());

    expect(d.confirmEviction).not.toHaveBeenCalled();
    expect(d.evict).not.toHaveBeenCalled();
    expect(d.warmCache).toHaveBeenCalled();
  });
});

describe('the warm run', () => {
  it('prefixes the feed URLs and offsets the progress grid by their count', async () => {
    const d = deps();

    await runAndSettle(d, options());

    expect(d.warmCache).toHaveBeenCalledWith(
      ['/api/feed', '/tile/1', '/tile/2'],
      expect.objectContaining({ pinned: true, areaId: 'region:ch-4115' }),
    );
    // The grid maps reported URL indices back onto cells, so it has to know
    // how many non-tile URLs sit in front of the tiles.
    expect(d.progressGrid).toHaveBeenCalledWith(expect.anything(), 1);
  });

  it('runs beforeWarm after eviction and before the warm run', async () => {
    const d = deps({ planBudget: vi.fn(async () => evictingPlan()) });
    const o = options({
      beforeWarm: async () => {
        calls.push('beforeWarm');
      },
    });

    await runAndSettle(d, o);

    expect(calls.indexOf('beforeWarm')).toBeGreaterThan(calls.indexOf('evict'));
    expect(calls.indexOf('beforeWarm')).toBeLessThan(calls.indexOf('warmCache'));
  });

  it('hands finish the worker result, the blob and the core', async () => {
    const d = deps();
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    const [result, blob, extras] = o.finish.mock.calls[0];
    expect(result).toEqual({ ok: 3, failed: 0, bytes: 1024 });
    expect(blob).toEqual({ z: 12, band: 'micro', mb: 12 });
    expect(extras.core).toBeTruthy();
    expect(extras.progressFill).toBeTruthy();
  });

  it('finishes with null when there is no active worker', async () => {
    const d = deps({ warmCache: vi.fn(() => null) });
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(o.finish.mock.calls[0][0]).toBeNull();
  });

  it('finishes with null when the warm run rejects', async () => {
    const d = deps({
      warmCache: vi.fn(async () => {
        throw new Error('worker went away');
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(o.finish.mock.calls[0][0]).toBeNull();
  });

  it('paints busy percentages from the worker progress reports', async () => {
    const update = vi.fn();
    let onProgress;
    const d = deps({
      progressGrid: vi.fn(() => ({ update, finish: vi.fn(async () => {}) })),
      warmCache: vi.fn(async (_urls, opts) => {
        onProgress = opts.onProgress;
        // No fourth (bytes) argument — an older worker still serving a
        // cached shell wouldn't send one; `undefined` is the expected
        // pass-through to `paint`'s own third argument in that case.
        onProgress(1, 4, [0]);
        onProgress(2, 4, [1]);
        return { ok: 4, failed: 0, bytes: 1 };
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.paint).toHaveBeenCalledWith('busy', 25, undefined);
    expect(o.paint).toHaveBeenCalledWith('busy', 50, undefined);
    expect(update).toHaveBeenCalledWith(1, 4, [0]);
  });

  it('reports 0% rather than dividing by zero on an empty run', async () => {
    const d = deps({
      warmCache: vi.fn(async (_urls, opts) => {
        opts.onProgress(0, 0, []);
        return { ok: 0, failed: 0, bytes: 0 };
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.paint).toHaveBeenCalledWith('busy', 0);
  });

  it('forwards the running bytes total to paint as a third argument (SNOW-632)', async () => {
    let onProgress;
    const d = deps({
      warmCache: vi.fn(async (_urls, opts) => {
        onProgress = opts.onProgress;
        onProgress(1, 4, [0], 1024);
        onProgress(2, 4, [1], 2048);
        return { ok: 4, failed: 0, bytes: 2048 };
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.paint).toHaveBeenCalledWith('busy', 25, 1024);
    expect(o.paint).toHaveBeenCalledWith('busy', 50, 2048);
  });

  it('hands finish a cancelled result intact (SNOW-632)', async () => {
    const d = deps({
      warmCache: vi.fn(async () => ({ ok: 2, failed: 0, bytes: 512, cancelled: true })),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    const [result] = o.finish.mock.calls[0];
    expect(result).toEqual({ ok: 2, failed: 0, bytes: 512, cancelled: true });
  });
});
