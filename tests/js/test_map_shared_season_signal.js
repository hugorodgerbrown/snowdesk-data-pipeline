/**
 * tests/js/test_map_shared_season_signal.js — the boot country's season
 * announces itself (SNOW-957).
 *
 * `getSeasonRatings` (map_shared.js) dispatches
 * 'snowdesk:country-ratings-loaded' with `{ code: 'ch' }` once Switzerland's
 * season lands. The between-seasons note and the tour offer wait for the first
 * such event as their "the colours are on the map" signal, and since boot no
 * longer fetches the season of a country only the basemap outlines, nothing
 * else raises one on a Swiss-only visit.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

const SOURCE = readFileSync(
  path.join(__dirname, '../../static/js/map_shared.js'),
  'utf8',
);

/**
 * Evaluate map_shared.js in its own scope and hand back its season getter.
 *
 * @returns {{getSeasonRatings: () => Promise<Object>, ready: () => boolean}}
 */
function loadGetter() {
  // eslint-disable-next-line no-new-func
  return new Function(
    `${SOURCE}\n;RATINGS_URL = '/api/ratings.json';
    return { getSeasonRatings, ready: () => SEASON_RATINGS_READY };`,
  )();
}

describe('the boot season signal (SNOW-957)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("announces Switzerland's season once it lands, and only once", async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ '2026-01-05': { 'CH-1111': 2 } }),
    })));
    const codes = [];
    const listener = (e) => codes.push(e.detail.code);
    document.addEventListener('snowdesk:country-ratings-loaded', listener);
    try {
      const shared = loadGetter();
      expect(shared.ready()).toBe(false);
      await shared.getSeasonRatings();
      await shared.getSeasonRatings();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(shared.ready()).toBe(true);
    } finally {
      document.removeEventListener('snowdesk:country-ratings-loaded', listener);
    }
    expect(codes).toEqual(['ch']);
  });

  it('stays quiet when the season fails to load', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false })));
    const codes = [];
    const listener = (e) => codes.push(e.detail.code);
    document.addEventListener('snowdesk:country-ratings-loaded', listener);
    const shared = loadGetter();
    try {
      await shared.getSeasonRatings().catch(() => {});
      await new Promise((resolve) => setTimeout(resolve, 0));
    } finally {
      document.removeEventListener('snowdesk:country-ratings-loaded', listener);
    }
    expect(codes).toEqual([]);
    expect(shared.ready()).toBe(false);
  });
});
