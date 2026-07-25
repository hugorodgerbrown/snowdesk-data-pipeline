/*
 * tests/js/test_basemap_download_core.js — Vitest unit tests for
 * static/js/basemap_download_core.js (SNOW-521).
 *
 * Covers the zoom-band rule, tile enumeration (incl. the maxTiles cap),
 * the byte-estimate helpers, and over-ceiling detection moved out of
 * map.js's `computeBasemapTileURLs` — the pure geometry/arithmetic behind
 * the "Download basemap" docked bar's live "up to N MB" readout.
 *
 * `basemap_download_core.js` is a plain IIFE that assigns a frozen
 * `self.pwaBasemapDownloadCore` — jsdom's global is `window`, which is
 * also `self` in a window context, so importing it for side effects is
 * enough (no service-worker environment or MapLibre instance needed).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const core = self.pwaBasemapDownloadCore;

describe('zoomBand', () => {
  it('bands one level shallower and down to the detail floor for a shallow view', () => {
    expect(core.zoomBand(6, 14)).toEqual({ minZ: 5, maxZ: 14 });
  });

  it('bands a one-level-either-side window when already at the detail floor', () => {
    expect(core.zoomBand(14, 14)).toEqual({ minZ: 13, maxZ: 14 });
  });

  it('reaches to the current zoom, not the floor, when already deeper than the floor', () => {
    expect(core.zoomBand(16, 14)).toEqual({ minZ: 15, maxZ: 16 });
  });

  it('rounds a fractional centre zoom before banding', () => {
    expect(core.zoomBand(6.6, 14)).toEqual({ minZ: 6, maxZ: 14 });
    expect(core.zoomBand(6.4, 14)).toEqual({ minZ: 5, maxZ: 14 });
  });

  it('clamps minZ at 0 for a very shallow view', () => {
    expect(core.zoomBand(0, 14)).toEqual({ minZ: 0, maxZ: 14 });
  });

  it('clamps maxZ at 20 for a very deep view', () => {
    expect(core.zoomBand(20, 14)).toEqual({ minZ: 19, maxZ: 20 });
  });
});

describe('enumerateTileURLs', () => {
  const TEMPLATE = 'https://tiles.example.com/{z}/{x}/{y}.pbf';
  // A degenerate point bounds at (0, 0) — the equator/prime-meridian
  // intersection. Chosen because both slippy-map formulas resolve exactly
  // at lon/lat 0 (Math.tan(0) === 0, Math.cos(0) === 1, Math.log(1) === 0
  // are all IEEE-754-exact), so the expected tile at any zoom is
  // deterministic without hand-tracing floating-point rounding.
  const POINT_BOUNDS = { west: 0, east: 0, north: 0, south: 0 };

  it('enumerates the single tile covering a point bounds at a given zoom', () => {
    const urls = core.enumerateTileURLs(TEMPLATE, POINT_BOUNDS, 1, 1, 100);
    expect(urls).toEqual(['https://tiles.example.com/1/1/1.pbf']);
  });

  it('covers every zoom level within [minZ, maxZ]', () => {
    const urls = core.enumerateTileURLs(TEMPLATE, POINT_BOUNDS, 0, 1, 100);
    expect(urls).toEqual([
      'https://tiles.example.com/0/0/0.pbf',
      'https://tiles.example.com/1/1/1.pbf',
    ]);
  });

  it('covers every tile column for a bounds spanning multiple tiles', () => {
    // Longitude is a pure linear formula (no trig) — a bounds from -180°
    // to 0° at z=2 spans tile columns 0 through 2 inclusive (3 columns),
    // hand-verifiable: lonToTileX(-180, 2) = 0, lonToTileX(0, 2) = 2.
    const bounds = { west: -180, east: 0, north: 0, south: 0 };
    const urls = core.enumerateTileURLs(TEMPLATE, bounds, 2, 2, 100);
    expect(urls).toEqual([
      'https://tiles.example.com/2/0/2.pbf',
      'https://tiles.example.com/2/1/2.pbf',
      'https://tiles.example.com/2/2/2.pbf',
    ]);
  });

  it('caps the returned array at maxTiles', () => {
    // The whole planet at a wide zoom band — would be hundreds of tiles
    // uncapped.
    const bounds = { west: -180, east: 180, north: 85, south: -85 };
    const urls = core.enumerateTileURLs(TEMPLATE, bounds, 0, 6, 10);
    expect(urls.length).toBe(10);
  });

  it('returns [] for a falsy template', () => {
    const bounds = { west: -1, east: 1, north: 1, south: -1 };
    expect(core.enumerateTileURLs('', bounds, 0, 1, 100)).toEqual([]);
    expect(core.enumerateTileURLs(null, bounds, 0, 1, 100)).toEqual([]);
  });

  it('returns [] when maxTiles is 0 or negative', () => {
    const bounds = { west: -1, east: 1, north: 1, south: -1 };
    expect(core.enumerateTileURLs(TEMPLATE, bounds, 0, 1, 0)).toEqual([]);
    expect(core.enumerateTileURLs(TEMPLATE, bounds, 0, 1, -5)).toEqual([]);
  });
});

describe('estimateBytes', () => {
  it('multiplies tile count by bytes-per-tile', () => {
    expect(core.estimateBytes(10, 1024)).toBe(10240);
  });

  it('is 0 for a zero tile count', () => {
    expect(core.estimateBytes(0, core.BASEMAP_WORST_CASE_BYTES_PER_TILE)).toBe(0);
  });
});

describe('formatUpToMB', () => {
  it('rounds up to the nearest whole MB', () => {
    expect(core.formatUpToMB(1)).toBe('1 MB');
    expect(core.formatUpToMB(1024 * 1024)).toBe('1 MB');
    expect(core.formatUpToMB(1024 * 1024 + 1)).toBe('2 MB');
  });

  it('formats a realistic multi-MB estimate', () => {
    const bytes = core.estimateBytes(120, core.BASEMAP_WORST_CASE_BYTES_PER_TILE);
    expect(core.formatUpToMB(bytes)).toBe('12 MB');
  });

  it('never reports a negative MB figure', () => {
    expect(core.formatUpToMB(0)).toBe('0 MB');
  });
});

describe('over-ceiling detection', () => {
  it('an estimate at exactly the tile ceiling stays within the MB ceiling', () => {
    const bytes = core.estimateBytes(
      core.BASEMAP_DOWNLOAD_CEILING_TILES,
      core.BASEMAP_WORST_CASE_BYTES_PER_TILE,
    );
    expect(bytes).toBeLessThanOrEqual(core.BASEMAP_DOWNLOAD_CEILING_MB * 1024 * 1024);
  });

  it('an estimate one tile past the ceiling exceeds the MB ceiling', () => {
    const bytes = core.estimateBytes(
      core.BASEMAP_DOWNLOAD_CEILING_TILES + 1,
      core.BASEMAP_WORST_CASE_BYTES_PER_TILE,
    );
    expect(bytes).toBeGreaterThan(core.BASEMAP_DOWNLOAD_CEILING_MB * 1024 * 1024);
  });

  it('enumerateTileURLs capped one tile past the ceiling detects the excess', () => {
    // A viewport whose true tile count exceeds the ceiling is detectable
    // by probing with maxTiles = ceiling + 1: the returned length hits
    // that cap rather than stopping short of it.
    const bounds = { west: -180, east: 180, north: 85, south: -85 };
    const urls = core.enumerateTileURLs(
      'https://tiles.example.com/{z}/{x}/{y}.pbf',
      bounds,
      0,
      10,
      core.BASEMAP_DOWNLOAD_CEILING_TILES + 1,
    );
    expect(urls.length).toBe(core.BASEMAP_DOWNLOAD_CEILING_TILES + 1);
  });
});
