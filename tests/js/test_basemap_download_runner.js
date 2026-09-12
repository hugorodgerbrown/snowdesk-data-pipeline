/*
 * tests/js/test_basemap_download_runner.js — Vitest unit tests for
 * static/js/basemap_download_runner.js (SNOW-611).
 *
 * The point of extracting the run was to make its ORDER testable. Both map
 * download controls used to spell the sequence out themselves, and one of
 * the two drifted — SNOW-607 (D1) had to move the tile-source check back
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
      // SNOW-843: the runner scales the caller's per-tile `mb` estimate by
      // the number of vector sources before spending it on a pre-flight.
      // SNOW-868 widened the seam with the blob's tile `count`, which the
      // real implementation prices per basemap; this stub keeps the simple
      // multiple, so the runner's own tests stay about the ORDER of the
      // pre-flights rather than the arithmetic inside them (that is
      // tests/js/test_basemap_download_core.js's job).
      sourceScaledMb: (mb, spec, _count) => mb * Math.max(1, (spec || []).length),
      // SNOW-856: `topUpBaseLayer` gates on this before asking for a base
      // layer — a run that did not succeed is almost always offline,
      // cancelled or out of quota, none of which is improved by eight more
      // megabytes.
      downloadSucceeded: (result) =>
        !!(result && !result.cancelled && result.ok > 0 && result.failed === 0),
      CUSTOM_AREA_ID: 'custom',
    })),
    tileSources: vi.fn(() => {
      calls.push('tileSources');
      return [['https://tiles/{z}/{x}/{y}.png']];
    }),
    basemapKey: vi.fn(() => {
      calls.push('basemapKey');
      return 'openfreemap_liberty';
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
  it('never evicts when the tile sources are missing', async () => {
    const d = deps({
      tileSources: () => {
        calls.push('tileSources');
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

    // SNOW-843: `tileSources` is READ before the quota check — the estimate
    // that check spends is scaled by how many vector sources the style
    // fetches — while the refusal it drives still comes after, in its
    // original place. Nothing destructive has happened by either point.
    expect(calls).toEqual([
      'clearError',
      'paint:busy',
      'tileSources',
      'fitsQuota',
      'basemapKey',
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

  it('hands beforeWarm the blob, the area id and the resolved sources (SNOW-632)', async () => {
    const d = deps();
    const o = options({
      beforeWarm: vi.fn(async () => {}),
    });

    await runAndSettle(d, o);

    expect(o.beforeWarm).toHaveBeenCalledWith(
      { z: 12, band: 'micro', mb: 12 },
      'region:ch-4115',
      [['https://tiles/{z}/{x}/{y}.png']],
    );
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
    // SNOW-632: the same sources beforeWarm saw, so a caller recording
    // what was downloaded can never disagree with what was fetched.
    expect(extras.tileSources).toEqual([['https://tiles/{z}/{x}/{y}.png']]);
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

  it("hands finish the run's own render dependencies (SNOW-844)", async () => {
    // Resolved at run start, alongside `tileSources`, so a basemap
    // switched mid-download cannot leave the record naming documents this
    // run never fetched.
    const d = deps({ renderDeps: vi.fn(() => ['/style.json', '/tiles.json']) });
    const o = options();

    await runAndSettle(d, o);

    const [, , extras] = o.finish.mock.calls[0];
    expect(extras.renderDeps).toEqual(['/style.json', '/tiles.json']);
  });

  it('yields an empty dependency list for a deps bundle without one', async () => {
    // An older cached shell mid-rollout. Empty means UNKNOWN to every
    // reader, never "nothing needed".
    const d = deps();
    delete d.renderDeps;
    const o = options();

    await runAndSettle(d, o);

    const [, , extras] = o.finish.mock.calls[0];
    expect(extras.renderDeps).toEqual([]);
  });
});

/*
 * SNOW-844: `repair` — the short path for an area that has its tiles and
 * not the documents needed to draw them.
 *
 * Its defining property is a NEGATIVE one, which is why it is asserted
 * first below: it must never reach the eviction sequence. Repairing four
 * small documents through a path that can ask the user to delete a whole
 * downloaded region to make room is a worse outcome than the fault.
 */
describe('repair', () => {
  /**
   * Options for a repair, mirroring `options()` above.
   *
   * @param {Object} [overrides]
   * @returns {Object}
   */
  function repairOptions(overrides) {
    const base = {
      areaId: 'region:ch-4115',
      urls: ['https://tiles/tiles.json'],
      paint: vi.fn((state) => calls.push(`paint:${state}`)),
      finish: vi.fn(async () => {
        calls.push('finish');
      }),
    };
    return Object.assign(base, overrides || {});
  }

  /** Dispatch a repair and let its warm-cache tail settle. */
  async function repairAndSettle(d, o) {
    await self.pwaBasemapDownloadRunner.repair(d, o);
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  it('never plans a budget, asks for an eviction, or evicts', async () => {
    const d = deps({ planBudget: vi.fn(async () => evictingPlan()) });
    const o = repairOptions();

    await repairAndSettle(d, o);

    expect(d.planBudget).not.toHaveBeenCalled();
    expect(d.confirmEviction).not.toHaveBeenCalled();
    expect(d.evict).not.toHaveBeenCalled();
    // Nor the quota pre-flight or the blob fetch — there is no blob.
    expect(d.fitsQuota).not.toHaveBeenCalled();
  });

  it("warms exactly the URLs given, into the area's own pinned bucket", async () => {
    const d = deps();
    const o = repairOptions({ urls: ['/a.json', '/b.png'] });

    await repairAndSettle(d, o);

    expect(d.warmCache).toHaveBeenCalledTimes(1);
    const [urls, opts] = d.warmCache.mock.calls[0];
    expect(urls).toEqual(['/a.json', '/b.png']);
    expect(opts.pinned).toBe(true);
    expect(opts.areaId).toBe('region:ch-4115');
    // No glyph promotion — glyphs are outside the dependency set this
    // ticket checks and repairs (SNOW-847).
    expect(opts.glyphPrefix).toBeUndefined();
  });

  it('claims busy synchronously, before any await', () => {
    const d = deps();
    const o = repairOptions();

    self.pwaBasemapDownloadRunner.repair(d, o);

    // Not awaited: the re-entrancy guard both callers read is this paint,
    // so an await ahead of it would leave a window for a second tap.
    expect(o.paint).toHaveBeenCalledWith('busy', 0);
  });

  it('settles finish even with nothing to fetch', async () => {
    // A caller that asked for a repair it could not describe. Painting
    // 'done' would claim the area renders when nothing was done about it,
    // and never settling would hang a caller awaiting the outcome.
    const d = deps();
    const o = repairOptions({ urls: [] });

    await repairAndSettle(d, o);

    expect(d.warmCache).not.toHaveBeenCalled();
    expect(o.paint).toHaveBeenCalledWith('error');
    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(o.finish.mock.calls[0][0]).toBeNull();
  });

  it('settles finish with null when there is no worker at all', async () => {
    const d = deps({ warmCache: vi.fn(() => null) });
    const o = repairOptions();

    await repairAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(o.finish.mock.calls[0][0]).toBeNull();
  });

  it('hands finish the warm-cache result intact', async () => {
    const d = deps({
      warmCache: vi.fn(async () => ({ ok: 1, failed: 0, bytes: 900 })),
    });
    const o = repairOptions();

    await repairAndSettle(d, o);

    expect(o.finish.mock.calls[0][0]).toEqual({ ok: 1, failed: 0, bytes: 900 });
  });
});

/*
 * SNOW-856: the shared base layer's top-up, which rides on the tail of a
 * download run rather than being a run of its own.
 *
 * Everything asserted here is about the boundary between the two: the
 * area is what the user asked for, and the base layer must never make it
 * slower, less legible, or less likely to be reported as finished. So the
 * top-up happens AFTER `finish`, its result never reaches `finish`, and
 * its failures are swallowed whole.
 */
describe('base layer top-up', () => {
  /**
   * A deps bundle that offers a base layer with `urls` still missing.
   *
   * @param {string[]} urls
   * @param {Object} [overrides]
   * @returns {Object}
   */
  function withBaseLayer(urls, overrides) {
    return deps(
      Object.assign(
        {
          baseLayer: vi.fn(async () => {
            calls.push('baseLayer');
            return { areaId: 'base-openfreemap_liberty', basemapKey: 'openfreemap_liberty', bbox: [0, 40, 10, 50], urls };
          }),
          finishBaseLayer: vi.fn(async () => {
            calls.push('finishBaseLayer');
          }),
          warmCache: vi.fn(async (list, opts) => {
            calls.push(`warmCache:${opts.areaId}`);
            return { ok: list.length, failed: 0, bytes: 1024 };
          }),
        },
        overrides || {},
      ),
    );
  }

  it('warms the base layer into its OWN bucket, after the area', () => {
    // Two properties in one assertion, because they are one decision. The
    // base layer goes to `base-…`, never into the area's bucket — sharing
    // one would tie it to that area's lifetime. And it runs after
    // `finish`, so the roundel reports the area as complete (which it is)
    // rather than staying busy through a supplementary fetch.
    const d = withBaseLayer(['/base/1', '/base/2']);

    return runAndSettle(d, options()).then(() => {
      expect(calls).toContain('warmCache:region:ch-4115');
      expect(calls.indexOf('finish')).toBeLessThan(calls.indexOf('warmCache:base-openfreemap_liberty'));
      expect(d.warmCache).toHaveBeenCalledWith(['/base/1', '/base/2'], {
        pinned: true,
        areaId: 'base-openfreemap_liberty',
        // SNOW-929: and the glyph prefix. This fake bundle offers no
        // `glyphPrefix` member — an older shell mid-rollout — so it
        // resolves to '', which the worker reads as "promote nothing".
        glyphPrefix: '',
      });
    });
  });

  it('promotes glyphs under the same prefix the area run used (SNOW-929)', async () => {
    // Two paths warm a base-layer bucket: this one, on the tail of an area
    // download, and `warmBaseLayerWideBand` when a basemap is first shown
    // (asserted in tests/js/test_basemap_base_layer_plan.js). They must
    // leave the same contents behind, and until SNOW-929 only the area
    // run passed the prefix — so whether the bucket kept its labels
    // depended on which path had filled it.
    //
    // Asserted against the AREA run's own option in the same test, not
    // just against a literal: the requirement is that the two agree, and
    // a third path added later has to satisfy the same shape.
    const prefix = 'https://tiles.example.invalid/fonts/';
    const d = withBaseLayer(['/base/1'], { glyphPrefix: vi.fn(() => prefix) });

    await runAndSettle(d, options());

    const byArea = new Map(d.warmCache.mock.calls.map(([, opts]) => [opts.areaId, opts]));
    expect(byArea.get('region:ch-4115').glyphPrefix).toBe(prefix);
    expect(byArea.get('base-openfreemap_liberty').glyphPrefix).toBe(prefix);
  });

  it('records what the top-up fetched', async () => {
    const d = withBaseLayer(['/base/1']);

    await runAndSettle(d, options());

    expect(d.finishBaseLayer).toHaveBeenCalledTimes(1);
    expect(d.finishBaseLayer.mock.calls[0][0]).toEqual({ ok: 1, failed: 0, bytes: 1024 });
    expect(d.finishBaseLayer.mock.calls[0][1].areaId).toBe('base-openfreemap_liberty');
  });

  it('does nothing when the base layer is already complete', async () => {
    // The plan resolves only the MISSING urls, so this is the ordinary
    // case from the second download onwards — and it is what makes the
    // base layer a one-off cost rather than a tax on every run.
    const d = withBaseLayer([]);

    await runAndSettle(d, options());

    expect(calls).not.toContain('warmCache:base-openfreemap_liberty');
    expect(d.finishBaseLayer).not.toHaveBeenCalled();
  });

  it('does nothing when the style has no base layer to fetch', async () => {
    const d = deps({
      baseLayer: vi.fn(async () => null),
      finishBaseLayer: vi.fn(async () => {}),
    });

    await runAndSettle(d, options());

    expect(d.finishBaseLayer).not.toHaveBeenCalled();
  });

  it('skips the top-up when the area run itself failed', async () => {
    // Offline, cancelled or out of quota — none of them improved by
    // asking for several more megabytes.
    const d = withBaseLayer(['/base/1'], {
      warmCache: vi.fn(async (list, opts) => {
        calls.push(`warmCache:${opts.areaId}`);
        return { ok: 0, failed: list.length, bytes: 0 };
      }),
    });

    await runAndSettle(d, options());

    expect(d.baseLayer).not.toHaveBeenCalled();
    expect(calls).not.toContain('warmCache:base-openfreemap_liberty');
  });

  it('skips the top-up when the area run was cancelled', async () => {
    const d = withBaseLayer(['/base/1'], {
      warmCache: vi.fn(async (list, opts) => {
        calls.push(`warmCache:${opts.areaId}`);
        return { ok: 1, failed: 0, bytes: 10, cancelled: true };
      }),
    });

    await runAndSettle(d, options());

    expect(d.baseLayer).not.toHaveBeenCalled();
  });

  it('never lets a base-layer failure reach the area run', async () => {
    // The area IS downloaded. A supplementary fetch that throws must not
    // turn that into a failed download the user is told to retry — the
    // next run tops up whatever this one left missing.
    const d = withBaseLayer(['/base/1'], {
      baseLayer: vi.fn(async () => {
        throw new Error('cache storage unavailable');
      }),
    });
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(o.finish.mock.calls[0][0]).toEqual({ ok: 3, failed: 0, bytes: 1024 });
    expect(calls).not.toContain('paint:error');
  });

  it('runs unchanged against a deps bundle with no base-layer members', async () => {
    // An older shell mid-rollout, and every existing caller of `run`.
    const d = deps();
    const o = options();

    await runAndSettle(d, o);

    expect(o.finish).toHaveBeenCalledTimes(1);
    expect(d.warmCache).toHaveBeenCalledTimes(1);
  });
});
