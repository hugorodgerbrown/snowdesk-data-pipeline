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
