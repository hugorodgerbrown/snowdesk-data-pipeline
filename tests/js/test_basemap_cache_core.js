/*
 * tests/js/test_basemap_cache_core.js — Vitest unit tests for
 * static/js/basemap_cache_core.js (SNOW-496).
 *
 * Covers the classification + eviction assertions moved out of
 * tests/e2e/test_offline_basemap_cache.py's ``evaluate()`` calls (that file
 * keeps only what genuinely needs a real activated service worker + real
 * CacheStorage — serving, meta:app hydration, the message race, and the
 * map.js registration wiring).
 *
 * ``basemap_cache_core.js`` is a plain IIFE that assigns a frozen
 * ``self.pwaBasemapCacheCore`` — jsdom's global is ``window``, which is also
 * ``self`` in a window context, so importing it for side effects is enough
 * (no service-worker environment needed; nothing here touches
 * ``self.location`` or IndexedDB).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_cache_core.js';

const core = self.pwaBasemapCacheCore;

const STATIC_PATHS = new Set(['/api/ratings/', '/api/regions.geojson']);
const STATIC_SHELL_EXTENSIONS = new Set(['.css', '.js', '.svg', '.png', '.woff2']);
const SELF_ORIGIN = 'https://snowdesk.example';

function req(method, mode, destination) {
  return { method: method || 'GET', mode: mode || 'no-cors', destination: destination || '' };
}

describe('classifySync', () => {
  it('classifies a non-GET request as network regardless of origin', () => {
    const url = new URL('https://snowdesk.example/api/whatever', SELF_ORIGIN);
    const result = core.classifySync(
      req('POST'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('network');
  });

  it('returns null for a cross-origin GET (caller must decide separately)', () => {
    const url = new URL('https://tiles.example.com/style.json');
    const result = core.classifySync(
      req('GET'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBeNull();
  });

  it('classifies a navigation request as navigate', () => {
    const url = new URL('/ch-4115/martigny-verbier/2026-04-08/', SELF_ORIGIN);
    const result = core.classifySync(
      req('GET', 'navigate'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('navigate');
  });

  it('classifies a document-destination request as navigate even without mode navigate', () => {
    const url = new URL('/some-page/', SELF_ORIGIN);
    const result = core.classifySync(
      req('GET', 'same-origin', 'document'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('navigate');
  });

  it('classifies a same-origin STATIC_PATHS entry as static', () => {
    const url = new URL('/api/ratings/?country=ch&d=2026-04-08', SELF_ORIGIN);
    const result = core.classifySync(
      req('GET'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('static');
  });

  it('classifies a same-origin static-shell extension as static', () => {
    const url = new URL('/static/js/map.js', SELF_ORIGIN);
    const result = core.classifySync(
      req('GET'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('static');
  });

  it('classifies a same-origin, non-static-path, non-shell-extension GET as network', () => {
    // Generic /api/* JSON — must stay fresh (bulletins, calendar partials).
    const url = new URL('/api/regions/ch-4115/', SELF_ORIGIN);
    const result = core.classifySync(
      req('GET'),
      url,
      SELF_ORIGIN,
      STATIC_PATHS,
      STATIC_SHELL_EXTENSIONS,
    );
    expect(result).toBe('network');
  });
});

describe('isBasemapOrigin', () => {
  it('is true when the origin is in the allowlist', () => {
    const origins = new Set(['https://tiles.example.com']);
    expect(core.isBasemapOrigin(new URL('https://tiles.example.com/style.json'), origins)).toBe(
      true,
    );
  });

  it('is false when the origin is not in the allowlist', () => {
    const origins = new Set(['https://tiles.example.com']);
    expect(core.isBasemapOrigin(new URL('https://other.example.com/style.json'), origins)).toBe(
      false,
    );
  });

  it('is false for an empty allowlist', () => {
    expect(core.isBasemapOrigin(new URL('https://tiles.example.com/style.json'), new Set())).toBe(
      false,
    );
  });
});

describe('shouldPersist', () => {
  const IMMUTABLE_ONLY_PATHS = new Set(['/api/bulletin-groupings.geojson']);

  function res(cacheControl) {
    return { headers: new Headers(cacheControl ? { 'Cache-Control': cacheControl } : {}) };
  }

  it('is true for a path outside immutableOnlyPaths regardless of Cache-Control', () => {
    const url = new URL('/api/ratings/?d=2026-04-08', SELF_ORIGIN);
    expect(core.shouldPersist(url, res('public, max-age=300'), IMMUTABLE_ONLY_PATHS)).toBe(true);
  });

  it('is false for a gated path whose response carries no immutable token', () => {
    const url = new URL('/api/bulletin-groupings.geojson?d=2026-04-08', SELF_ORIGIN);
    expect(core.shouldPersist(url, res('public, max-age=300'), IMMUTABLE_ONLY_PATHS)).toBe(false);
  });

  it('is false for a gated path with no Cache-Control header at all', () => {
    const url = new URL('/api/bulletin-groupings.geojson?d=2026-04-08', SELF_ORIGIN);
    expect(core.shouldPersist(url, res(null), IMMUTABLE_ONLY_PATHS)).toBe(false);
  });

  it('is true for a gated path whose response declares itself immutable', () => {
    const url = new URL('/api/bulletin-groupings.geojson?d=2025-12-01', SELF_ORIGIN);
    expect(
      core.shouldPersist(url, res('public, max-age=604800, immutable'), IMMUTABLE_ONLY_PATHS),
    ).toBe(true);
  });

  it('matches the immutable token case-insensitively', () => {
    const url = new URL('/api/bulletin-groupings.geojson?d=2025-12-01', SELF_ORIGIN);
    expect(
      core.shouldPersist(url, res('Public, Max-Age=604800, IMMUTABLE'), IMMUTABLE_ONLY_PATHS),
    ).toBe(true);
  });
});

describe('trimCache', () => {
  /**
   * A minimal in-file fake implementing just the ``Cache`` surface
   * ``trimCache`` uses: ``keys()`` (insertion order) and ``delete(key)``.
   */
  function fakeCache(initialKeys) {
    const store = [...initialKeys];
    return {
      keys: async () => [...store],
      delete: async (key) => {
        const idx = store.indexOf(key);
        if (idx === -1) return false;
        store.splice(idx, 1);
        return true;
      },
      _remaining: () => [...store],
    };
  }

  it('does nothing when the cache is at or under the max', () => {
    const cache = fakeCache(['a', 'b', 'c']);
    return core.trimCache(cache, 3).then(() => {
      expect(cache._remaining()).toEqual(['a', 'b', 'c']);
    });
  });

  it('deletes the oldest entries first down to max', async () => {
    const cache = fakeCache(['a', 'b', 'c', 'd', 'e']);
    await core.trimCache(cache, 2);
    expect(cache._remaining()).toEqual(['d', 'e']);
  });

  it('is a no-op on an already-empty cache', async () => {
    const cache = fakeCache([]);
    await core.trimCache(cache, 10);
    expect(cache._remaining()).toEqual([]);
  });
});

/*
 * SNOW-568 — the bounded-concurrency pool and failure classification that
 * replaced _warmCache's unbounded Promise.all fan-out.
 */

describe('runPool', () => {
  /**
   * A worker that records the peak number of concurrent calls, resolving
   * each after a macrotask so overlapping calls actually overlap.
   */
  function trackingWorker() {
    const seen = [];
    let inFlight = 0;
    let peak = 0;
    const worker = async (item) => {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 0));
      seen.push(item);
      inFlight -= 1;
    };
    return {
      worker,
      seen,
      peak: () => peak,
    };
  }

  it('visits every item exactly once', async () => {
    const items = Array.from({ length: 50 }, (_, i) => i);
    const tracker = trackingWorker();
    await core.runPool(items, 6, tracker.worker);
    expect(tracker.seen.slice().sort((a, b) => a - b)).toEqual(items);
  });

  it('never exceeds the concurrency limit', async () => {
    const items = Array.from({ length: 50 }, (_, i) => i);
    const tracker = trackingWorker();
    await core.runPool(items, 6, tracker.worker);
    expect(tracker.peak()).toBeLessThanOrEqual(6);
  });

  it('actually runs concurrently up to the limit', async () => {
    // The regression guard in the other direction: a pool that silently
    // degraded to sequential would pass every assertion above.
    const items = Array.from({ length: 50 }, (_, i) => i);
    const tracker = trackingWorker();
    await core.runPool(items, 6, tracker.worker);
    expect(tracker.peak()).toBe(6);
  });

  it('caps concurrency at the list length for a short list', async () => {
    const tracker = trackingWorker();
    await core.runPool([1, 2], 16, tracker.worker);
    expect(tracker.peak()).toBe(2);
    expect(tracker.seen.slice().sort()).toEqual([1, 2]);
  });

  it('runs one at a time for a limit below 1 rather than hanging', async () => {
    const tracker = trackingWorker();
    await core.runPool([1, 2, 3], 0, tracker.worker);
    expect(tracker.peak()).toBe(1);
    expect(tracker.seen).toEqual([1, 2, 3]);
  });

  it('passes the index alongside the item', async () => {
    const pairs = [];
    await core.runPool(['a', 'b', 'c'], 1, async (item, index) => {
      pairs.push([item, index]);
    });
    expect(pairs).toEqual([
      ['a', 0],
      ['b', 1],
      ['c', 2],
    ]);
  });

  it('resolves immediately for an empty or non-array list', async () => {
    let calls = 0;
    const count = async () => {
      calls += 1;
    };
    await core.runPool([], 6, count);
    await core.runPool(null, 6, count);
    await core.runPool(undefined, 6, count);
    expect(calls).toBe(0);
  });
});

describe('classifyFailure', () => {
  it('classifies a QuotaExceededError as quota', () => {
    const err = new Error('no room');
    err.name = 'QuotaExceededError';
    expect(core.classifyFailure(err)).toBe('quota');
  });

  it('classifies a fetch TypeError as network', () => {
    // What a fetch() rejects with when it cannot reach the network at all
    // — including the ERR_INSUFFICIENT_RESOURCES burst this ticket fixes.
    expect(core.classifyFailure(new TypeError('Failed to fetch'))).toBe('network');
  });

  it('classifies anything else as other', () => {
    expect(core.classifyFailure(new Error('boom'))).toBe('other');
    expect(core.classifyFailure(null)).toBe('other');
    expect(core.classifyFailure(undefined)).toBe('other');
  });
});

describe('worseReason', () => {
  it('keeps quota over every other reason, in either argument order', () => {
    expect(core.worseReason('quota', 'network')).toBe('quota');
    expect(core.worseReason('network', 'quota')).toBe('quota');
    expect(core.worseReason('quota', 'other')).toBe('quota');
    expect(core.worseReason('other', 'quota')).toBe('quota');
  });

  it('keeps network over other', () => {
    expect(core.worseReason('network', 'other')).toBe('network');
    expect(core.worseReason('other', 'network')).toBe('network');
  });

  it('takes whichever side is a known reason when the other is null', () => {
    expect(core.worseReason(null, 'network')).toBe('network');
    expect(core.worseReason('network', null)).toBe('network');
  });

  it('returns null when neither side is a known reason', () => {
    expect(core.worseReason(null, null)).toBeNull();
    expect(core.worseReason(undefined, 'nonsense')).toBeNull();
  });

  it('accumulates a run down to its most actionable failure', () => {
    // One quota failure among a thousand network ones is still the thing
    // worth telling the user about.
    const run = ['network', 'other', 'network', 'quota', 'network'];
    expect(run.reduce((acc, r) => core.worseReason(acc, r), null)).toBe('quota');
  });
});

/*
 * SNOW-586 — responseBytes, the byte-size accounting _warmCache sums as it
 * writes a pinned download so the page can record the run's size against
 * the new standing byte budget.
 */

describe('responseBytes', () => {
  it('reads Content-Length without touching the body', async () => {
    const response = new Response('ignored body', {
      headers: { 'Content-Length': '12345' },
    });
    await expect(core.responseBytes(response)).resolves.toBe(12345);
  });

  it('falls back to the blob size when Content-Length is absent', async () => {
    const body = 'x'.repeat(2000);
    const response = new Response(body);
    await expect(core.responseBytes(response)).resolves.toBe(body.length);
  });

  it('ignores a non-numeric Content-Length and falls back to the blob', async () => {
    const body = 'abcdef';
    const response = new Response(body, { headers: { 'Content-Length': 'not-a-number' } });
    await expect(core.responseBytes(response)).resolves.toBe(body.length);
  });

  it('returns 0 for a falsy response', async () => {
    await expect(core.responseBytes(null)).resolves.toBe(0);
    await expect(core.responseBytes(undefined)).resolves.toBe(0);
  });

  it('returns 0 rather than throwing when the blob read itself fails', async () => {
    const unusable = {
      headers: null,
      clone: () => {
        throw new Error('already consumed');
      },
    };
    await expect(core.responseBytes(unusable)).resolves.toBe(0);
  });
});
