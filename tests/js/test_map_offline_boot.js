/*
 * tests/js/test_map_offline_boot.js — Vitest DOM test for SNOW-638: the
 * micro-region (L4) choropleth failed to paint offline even though its own
 * sync dot showed green.
 *
 * Root cause: static/js/map.js's ``map.on('load', ...)`` boot handler
 * fetched regions.geojson, ratings.json and resorts.json in a single
 * ``Promise.all`` with only the ratings leg guarded by a ``.catch()``. The
 * resorts leg (``/api/resorts.json``) is a plain-JSON, non-cacheable
 * sibling deliberately absent from sw.js's ``STATIC_PATHS`` (only the
 * ``.geojson`` variant is listed there) — offline, that fetch rejects on
 * every single load. With no ``.catch()`` on that leg the whole
 * ``Promise.all`` rejected, so ``installRegionsLayers``, the choropleth
 * paint, and ``applyCountryFilters`` never ran, even though the regions
 * feed the sync dot itself probes (``/api/regions.geojson``) was fine.
 *
 * The fix guards each of the three legs independently, still inside ONE
 * ``Promise.all`` (SNOW-235 trimmed this exact critical path — serialising
 * the legs would reintroduce that cost): resorts/ratings degrade to an
 * empty payload on failure, and the resolved ``geojson`` is null-checked
 * after the ``await``, returning early rather than throwing. This file
 * covers the regression itself — it fails against the pre-fix map.js
 * (regions/layers never installed) and passes after the fix.
 *
 * The negative control (same assertions, every fetch resolves — proving a
 * test that always passed regardless of the fix would be caught) lives in
 * ``test_map_offline_boot_control.js``, a SEPARATE file, NOT a second
 * ``it()`` here. Why: this suite originally had both cases in one file,
 * each calling ``vi.resetModules()`` + ``await import('.../map.js')``
 * inside a shared ``bootMap()`` helper invoked once per ``it()`` — so the
 * file imported map.js twice. That intermittently threw
 * ``ReferenceError: BULLETIN_GROUPINGS_URL_MODULE is not defined`` from
 * deep inside map.js's own module-evaluation on CI (never reproduced
 * locally, including under ``--maxWorkers=1``/``=2`` or the full suite):
 * map.js is one script of thousands of lines of top-level IIFEs,
 * module-scope ``let``s, timers and listeners, and re-entering that
 * evaluation while the first instance's side effects are still live is
 * exactly the kind of thing that goes wrong under different timing. Every
 * OTHER map.js test in this repo imports it exactly once, in ``beforeAll``
 * (``test_map_download_bytes.js``, ``test_map_download_eviction.js``,
 * ``test_map_groupings_guard.js``) — this file (and its control sibling)
 * now follow that same rule: ONE ``import('.../map.js')`` per file, in
 * ``beforeAll``. Do not add a second case here that needs a second import
 * — put it in its own file, as the control case now is.
 *
 * Shared fixtures (the MapLibre stub, the DOM fixture, the
 * ``regionsInstalled`` assertion helper) live in
 * ``map_offline_boot_helpers.js`` — a plain module, not itself a test file
 * (vitest.config.mjs's ``include`` glob only matches ``test_*.js``), so
 * importing it here does not pull in a second copy of map.js.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import {
  REGIONS_GEOJSON,
  buildFixture,
  regionsInstalled,
  stubMapLibre,
} from './map_offline_boot_helpers.js';

let mapStub;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      if (href.includes('regions.geojson')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(REGIONS_GEOJSON) });
      }
      if (href.includes('resorts.json')) {
        // Exactly what an offline browser does for a fetch sw.js never
        // intercepts: the promise itself rejects, not a non-ok response.
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      // ratings.json and anything else — resolve emptyish, matching the
      // ratings leg's own existing guard.
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }),
  );

  vi.resetModules();
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  // ONE import of map.js for this whole file — see the module docstring
  // above for why a second import (even from a second test in this file)
  // is unsafe.
  await import('../../static/js/map.js');
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('map.js boot handler — offline resorts feed (SNOW-638)', () => {
  it('still installs the regions choropleth when /api/resorts.json rejects (the offline repro)', () => {
    expect(regionsInstalled(mapStub)).toBe(true);
  });
});
