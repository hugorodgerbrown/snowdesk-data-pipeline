/*
 * tests/js/test_basemap_glyph_promotion.js — SNOW-742's glyph promotion.
 *
 * The bug is subtler than "glyphs are never downloaded", and the distinction
 * is what these tests pin. Glyph PBFs ARE fetched, by ordinary browsing, via
 * ``_basemapStaleWhileRevalidate`` — but they land in ``BASEMAP_CACHE``, which
 * is FIFO-trimmed to 600 entries, while pinned download buckets are never
 * trimmed. Within a couple of browsing sessions (150-300 distinct URLs each)
 * the glyphs an area needs are evicted while its tiles sit safe in the pinned
 * bucket, and the area decays into geometry with no labels.
 *
 * So the fix is promotion, not enumeration: at the end of a pinned run, copy
 * the matching entries out of the trimmable cache into the untrimmable one.
 * Ranges never browsed stay uncovered, exactly as before — that part of
 * SNOW-492's reasoning still stands and is not under test here.
 *
 * Loads sw.js the same way tests/js/test_sw.js does — evaluated in a
 * ``new Function`` sandbox with the worker globals passed in — so this
 * exercises the shipped code rather than a re-implementation.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_cache_core.js';

const SW_SOURCE = readFileSync(join(process.cwd(), 'static', 'js', 'sw.js'), 'utf8');
const ORIGIN = 'https://snowdesk.example';
const GLYPH_PREFIX = 'https://tiles.example/fonts/';
const AREA_ID = 'region-ch-4115';

/** Evaluate sw.js and return the helpers these tests drive. */
function loadSw(cachesStub, fetchStub) {
  const selfStub = {
    location: { origin: ORIGIN },
    addEventListener: () => {},
    clients: { get: () => Promise.resolve(null), matchAll: () => Promise.resolve([]) },
    registration: { showNotification: () => Promise.resolve() },
    pwaBasemapCacheCore: self.pwaBasemapCacheCore,
  };
  const factory = new Function(
    'self',
    'caches',
    'fetch',
    `${SW_SOURCE}\nreturn { _warmCache, _promoteGlyphs, BASEMAP_CACHE, BASEMAP_PINNED_CACHE_PREFIX };`,
  );
  return factory(selfStub, cachesStub, fetchStub);
}

/**
 * An in-memory CacheStorage exposing the surface promotion needs — notably
 * ``keys()`` on a bucket, which the pinned-bucket stub in test_sw.js does not
 * provide because nothing there enumerates a bucket's contents.
 */
function makeCaches() {
  const buckets = new Map();
  const entriesFor = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Map());
    return buckets.get(name);
  };
  return {
    open: async (name) => {
      const entries = entriesFor(name);
      return {
        async match(request) {
          const url = typeof request === 'string' ? request : request.url;
          const hit = entries.get(url);
          return hit ? hit.clone() : undefined;
        },
        async put(request, response) {
          const url = typeof request === 'string' ? request : request.url;
          entries.set(url, response);
        },
        async keys() {
          return [...entries.keys()].map((url) => new Request(url));
        },
      };
    },
    async keys() {
      return [...buckets.keys()];
    },
    async delete(name) {
      return buckets.delete(name);
    },
    seed(name, url, response) {
      entriesFor(name).set(url, response);
    },
    urls(name) {
      return [...entriesFor(name).keys()];
    },
  };
}

/** A cacheable cross-origin response stand-in. */
function corsResponse(body) {
  const build = () => {
    const real = new Response(body, { status: 200 });
    return {
      ok: true,
      status: 200,
      headers: real.headers,
      type: 'cors',
      get body() {
        return real.body;
      },
      text: () => real.text(),
      arrayBuffer: () => real.arrayBuffer(),
      blob: () => real.blob(),
      clone: () => build(),
    };
  };
  return build();
}

let cachesStub;
let sw;

beforeEach(() => {
  cachesStub = makeCaches();
  sw = loadSw(cachesStub, vi.fn(async () => corsResponse('tile')));
});

const pinnedName = () => `${sw.BASEMAP_PINNED_CACHE_PREFIX}${AREA_ID}`;

describe('glyph promotion into a pinned bucket (SNOW-742)', () => {
  it('copies the style’s cached glyphs into the pinned bucket', async () => {
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('glyph bytes'));

    await sw._warmCache([], { pinned: true, areaId: AREA_ID, glyphPrefix: GLYPH_PREFIX });

    expect(cachesStub.urls(pinnedName())).toContain(glyph);
    // The passive copy is left alone — it is still the fast path for ordinary
    // browsing, and it is the trim, not this function, that removes it.
    expect(cachesStub.urls(sw.BASEMAP_CACHE)).toContain(glyph);
  });

  it('leaves entries from other origins where they are', async () => {
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    const tile = 'https://tiles.example/tiles/12/34/56.pbf';
    const otherStyle = 'https://other-basemap.example/fonts/Arial/0-255.pbf';
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('glyph bytes'));
    cachesStub.seed(sw.BASEMAP_CACHE, tile, corsResponse('tile bytes'));
    cachesStub.seed(sw.BASEMAP_CACHE, otherStyle, corsResponse('other glyph'));

    await sw._warmCache([], { pinned: true, areaId: AREA_ID, glyphPrefix: GLYPH_PREFIX });

    const pinned = cachesStub.urls(pinnedName());
    expect(pinned).toEqual([glyph]);
    // A tile under the same host but a different path must not match, or the
    // prefix would be promoting half the passive cache.
    expect(pinned).not.toContain(tile);
    expect(pinned).not.toContain(otherStyle);
  });

  it('is idempotent, so re-downloading an area does not duplicate entries', async () => {
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('glyph bytes'));
    const options = { pinned: true, areaId: AREA_ID, glyphPrefix: GLYPH_PREFIX };

    await sw._warmCache([], options);
    const afterFirst = cachesStub.urls(pinnedName()).length;
    await sw._warmCache([], options);

    // The re-download path is where this family of bugs has shipped before —
    // a run that starts from a populated store, not an empty one.
    expect(cachesStub.urls(pinnedName()).length).toBe(afterFirst);
  });

  it('counts promoted bytes into the run total, so the budget sees real disk', async () => {
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('0123456789'));

    const withGlyphs = await sw._warmCache([], {
      pinned: true,
      areaId: AREA_ID,
      glyphPrefix: GLYPH_PREFIX,
    });
    const without = await sw._warmCache([], { pinned: true, areaId: 'other-area' });

    expect(withGlyphs.bytes).toBeGreaterThan(without.bytes);
  });

  it('does nothing without a prefix, and nothing for a non-pinned run', async () => {
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('glyph bytes'));

    // No prefix: an empty string must not be treated as "matches everything",
    // which would promote the entire passive cache into the bucket.
    await sw._warmCache([], { pinned: true, areaId: AREA_ID, glyphPrefix: '' });
    expect(cachesStub.urls(pinnedName())).toEqual([]);

    // Ordinary browsing warms nothing into any pinned bucket.
    await sw._warmCache([], { glyphPrefix: GLYPH_PREFIX });
    expect(cachesStub.urls(pinnedName())).toEqual([]);
  });
});

/*
 * SNOW-844: the render-dependency probe and glyph promotion must not meet.
 *
 * Promotion copies whatever ranges ORDINARY BROWSING happened to cache, so
 * the set in a pinned bucket is legitimately partial and unpredictable —
 * ranges never browsed were never covered, which SNOW-742 states outright
 * and did not change. A completeness check over that set would report a
 * permanent fault with no repair able to clear it, which is worse than the
 * silence it replaced. Pinning the ranges an area actually needs is
 * SNOW-847's job.
 *
 * Asserted rather than left as a comment, so a later ticket cannot fold
 * glyphs into the dependency list by reflex.
 */
describe('glyphs are outside the render-dependency check (SNOW-844)', () => {
  it('promotes a partial glyph set, which no completeness check could pass', async () => {
    // Two ranges of one fontstack exist; browsing cached one. Promotion
    // copies that one and cannot know the other was ever wanted.
    const browsed = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    const neverBrowsed = `${GLYPH_PREFIX}Frutiger%20Regular/256-511.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, browsed, corsResponse('glyph bytes'));

    await sw._warmCache([], { pinned: true, areaId: AREA_ID, glyphPrefix: GLYPH_PREFIX });

    const pinned = cachesStub.urls(pinnedName());
    expect(pinned).toContain(browsed);
    expect(pinned).not.toContain(neverBrowsed);
  });

  it('carries the prefix as a promotion instruction, not as a URL list', async () => {
    // The shape of the exclusion, in the wiring rather than in prose: a
    // download hands the worker `glyphPrefix` — a string it SELECTS
    // already-cached entries with — while the dependency list the record
    // stores is built from real URLs the run fetched
    // (`activeBasemapRenderDependencyURLs`). There is no point at which a
    // glyph URL could join that list, because nothing ever enumerates one.
    const glyph = `${GLYPH_PREFIX}Frutiger%20Regular/0-255.pbf`;
    cachesStub.seed(sw.BASEMAP_CACHE, glyph, corsResponse('glyph bytes'));

    const result = await sw._warmCache([], {
      pinned: true,
      areaId: AREA_ID,
      glyphPrefix: GLYPH_PREFIX,
    });

    // Nothing was FETCHED — `ok` counts the URL list, which was empty. The
    // glyph arrived by copy, which is why it can never be a dependency a
    // repair could re-fetch.
    expect(result.ok).toBe(0);
    expect(cachesStub.urls(pinnedName())).toEqual([glyph]);
  });
});
