/*
 * tests/js/test_map_offline_boot_control.js — negative control for SNOW-638
 * (see test_map_offline_boot.js's module docstring for the full root-cause
 * writeup, including the SECOND CI failure and the ``buildFetchImpl`` /
 * ``tick`` / ``captureUnhandledRejections`` fix in
 * ``map_offline_boot_helpers.js``).
 *
 * Same assertions as the regression case — the ``regions`` source and the
 * ``regions-fill`` layer both install — but here every one of the three
 * boot fetches (regions/ratings/resorts) resolves cleanly. Without this
 * case, an assertion that happened to always pass regardless of the fix
 * (e.g. a typo'd selector, or a stub that never actually exercised the
 * install path) would go undetected; this proves the same assertions DO
 * hold in the ordinary, everything-succeeds case.
 *
 * Deliberately a SEPARATE file from test_map_offline_boot.js rather than a
 * second ``it()`` there: each file must import map.js exactly once (see
 * that file's module docstring for why). Shared fixtures live in
 * ``map_offline_boot_helpers.js``, a plain module (not itself a test file),
 * so importing them here does not pull in a second copy of map.js.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import {
  REGIONS_GEOJSON,
  buildFetchImpl,
  buildFixture,
  captureUnhandledRejections,
  regionsInstalled,
  stubMapLibre,
  tick,
} from './map_offline_boot_helpers.js';

let mapStub;
let rejectionCapture;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  rejectionCapture = captureUnhandledRejections();
  vi.stubGlobal(
    'fetch',
    vi.fn(
      buildFetchImpl([
        [
          (href) => href.includes('regions.geojson'),
          () => Promise.resolve({ ok: true, json: () => Promise.resolve(REGIONS_GEOJSON) }),
        ],
        // resorts.json, ratings.json and every other endpoint: buildFetchImpl's
        // default (well-formed `{}` for non-.geojson URLs) is exactly right
        // here — nothing needs to fail in the negative control.
      ]),
    ),
  );

  vi.resetModules();
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  // ONE import of map.js for this whole file — see
  // test_map_offline_boot.js's module docstring for why.
  await import('../../static/js/map.js');
  for (const handler of mapStub.handlers.load || []) await handler();
  // Let boot's un-awaited fire-and-forget chains settle before this
  // beforeAll returns — see test_map_offline_boot.js's module docstring and
  // map_offline_boot_helpers.js's tick() docstring for why.
  await tick();
});

afterAll(() => {
  rejectionCapture.stop();
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('map.js boot handler — negative control (SNOW-638)', () => {
  it('installs the regions choropleth when every boot fetch resolves', () => {
    expect(regionsInstalled(mapStub)).toBe(true);
  });

  it('produces no unhandled promise rejections during boot', () => {
    expect(rejectionCapture.rejections).toEqual([]);
  });
});
