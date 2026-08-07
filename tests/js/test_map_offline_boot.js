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
 * locally, including under ``--maxWorkers=1``/``=2`` or the full suite).
 * Every OTHER map.js test in this repo imports it exactly once, in
 * ``beforeAll`` (``test_map_download_bytes.js``,
 * ``test_map_download_eviction.js``, ``test_map_groupings_guard.js``) —
 * this file (and its control sibling) follow that same rule: ONE
 * ``import('.../map.js')`` per file, in ``beforeAll``. Do not add a second
 * case here that needs a second import — put it in its own file, as the
 * control case now is.
 *
 * SECOND CI failure after that split: BOTH files still threw the same
 * ``ReferenceError`` on their first and only import — so the "imported
 * twice" theory wasn't the whole story. This suite is the only map.js test
 * whose regions fetch returns a FeatureCollection with REAL features
 * (every sibling uses an empty one), so the boot handler runs all the way
 * through ``installRegionsLayers`` and then fires a lot of work it
 * deliberately does NOT await — ``ensureCountryLoaded``, the l1/l2/resorts
 * ``restoreOverlay`` calls, and the l3 bulletin-groupings load riding along
 * with L4 (on by default). The original mock answered every non-regions
 * URL with a bare ``{}``, the wrong shape for a ``.geojson`` endpoint — code
 * doing ``payload.features.forEach(...)`` inside one of those floating
 * (never-awaited) promises would throw, and Node/Vitest can misattribute
 * that unhandled rejection's settling to whatever frame is executing at the
 * time, including a completely unrelated import elsewhere in the same
 * worker. ``buildFetchImpl`` (map_offline_boot_helpers.js) now answers
 * every ``.geojson``-shaped URL with a real, empty FeatureCollection by
 * default; ``tick()`` after firing the load handler(s) lets any such
 * fire-and-forget chain settle inside THIS ``beforeAll`` rather than
 * leaking past it; and ``captureUnhandledRejections`` proves none occurred
 * (rather than silently hoping) — see that module's docstrings for the
 * full reasoning.
 *
 * Shared fixtures live in ``map_offline_boot_helpers.js`` — a plain module,
 * not itself a test file (vitest.config.mjs's ``include`` glob only matches
 * ``test_*.js``), so importing it here does not pull in a second copy of
 * map.js.
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
        [
          (href) => href.includes('resorts.json'),
          // Exactly what an offline browser does for a fetch sw.js never
          // intercepts: the promise itself rejects, not a non-ok response.
          // This is the thing under test — every other endpoint gets a
          // well-formed payload via buildFetchImpl's default.
          () => Promise.reject(new TypeError('Failed to fetch')),
        ],
      ]),
    ),
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
  // Let boot's un-awaited fire-and-forget chains settle before this
  // beforeAll returns — see the module docstring above and
  // map_offline_boot_helpers.js's tick() docstring for why.
  await tick();
});

afterAll(() => {
  rejectionCapture.stop();
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('map.js boot handler — offline resorts feed (SNOW-638)', () => {
  it('still installs the regions choropleth when /api/resorts.json rejects (the offline repro)', () => {
    expect(regionsInstalled(mapStub)).toBe(true);
  });

  it('produces no unhandled promise rejections during boot', () => {
    expect(rejectionCapture.rejections).toEqual([]);
  });
});
