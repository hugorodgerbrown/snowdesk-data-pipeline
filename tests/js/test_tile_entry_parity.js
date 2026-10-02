/*
 * tests/js/test_tile_entry_parity.js — Vitest parity guard for the two
 * copies of ``isTileEntryURL`` (SNOW-1060).
 *
 * The predicate is defined twice: in static/js/basemap_download_core.js,
 * which the pages load (SNOW-929's re-banding check), and in
 * static/js/basemap_cache_core.js, which only the service worker loads
 * (the passive basemap trim that keeps the style documents). They cannot
 * share one definition without adding a script to the map shell, so this
 * test is the drift guard instead: one truth table, run against both,
 * asserting they agree with each other AND with the expected answer.
 *
 * The urls are the truth table from tests/js/test_basemap_base_layer.js's
 * ``isTileEntryURL`` block, plus the swisstopo shapes SNOW-1060's trim
 * meets in the passive cache.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_cache_core.js';
import '../../static/js/basemap_download_core.js';

const cacheCore = self.pwaBasemapCacheCore;
const downloadCore = self.pwaBasemapDownloadCore;

const TRUTH_TABLE = [
  // Vector tiles, whatever the tileset path carries.
  ['https://tiles.openfreemap.org/planet/20260906_080001_pt/7/66/45.pbf', true],
  ['https://example.invalid/12/2145/1456.mvt', true],
  ['https://vectortiles0.geo.admin.ch/tiles/base.vt/14/8500/5800.pbf', true],
  // Raster tiles.
  ['https://tiles.openfreemap.org/natural_earth/ne2sr/6/33/22.png', true],
  ['https://example.invalid/9/266/180.jpg', true],
  ['https://example.invalid/9/266/180.jpeg', true],
  // A query string and a fragment never change the answer.
  ['https://example.invalid/7/66/45.pbf?key=abc', true],
  ['https://example.invalid/7/66/45.pbf#x', true],
  // Glyph ranges — the closest miss there is.
  ['https://tiles.openfreemap.org/fonts/Noto%20Sans%20Bold/0-255.pbf', false],
  ['https://tiles.openfreemap.org/fonts/Noto%20Sans%20Bold/256-511.pbf', false],
  ['https://vectortiles.geo.admin.ch/fonts/Frutiger%20Neue%20Regular/0-255.pbf', false],
  // Sprites, at 1x and 2x.
  ['https://tiles.openfreemap.org/sprites/ofm_f384/ofm@2x.json', false],
  ['https://tiles.openfreemap.org/sprites/ofm_f384/ofm@2x.png', false],
  ['https://tiles.openfreemap.org/sprites/ofm_f384/ofm.png', false],
  // Style documents and TileJSON.
  ['https://tiles.openfreemap.org/styles/liberty', false],
  ['https://tiles.openfreemap.org/planet', false],
  ['https://mapsneu.wien.gv.at/basemapvectorneu/root.json', false],
  ['https://vectortiles.geo.admin.ch/styles/ch.swisstopo.basemap-winter.vt/style.json', false],
  // What it cannot parse.
  [undefined, false],
  [null, false],
  ['', false],
  [42, false],
  [{ url: 'https://example.invalid/7/66/45.pbf' }, false],
];

describe('isTileEntryURL parity', () => {
  it('is exported by both cores', () => {
    expect(typeof cacheCore.isTileEntryURL).toBe('function');
    expect(typeof downloadCore.isTileEntryURL).toBe('function');
  });

  it.each(TRUTH_TABLE)('answers the same for %s in both cores', (url, expected) => {
    const fromCache = cacheCore.isTileEntryURL(url);
    const fromDownload = downloadCore.isTileEntryURL(url);
    expect(fromCache).toBe(fromDownload);
    expect(fromCache).toBe(expected);
  });
});
