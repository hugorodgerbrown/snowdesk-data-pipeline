/*
 * tests/js/test_basemap_style_core.js — resolving the reader's basemap, and
 * noticing when it drew nothing (SNOW-829).
 *
 * Three pure functions, all of them decisions the trip page makes before
 * it hands MapLibre a style or after it asks the canvas what it painted.
 * None of it needs a browser: `drewNothing` takes a
 * `queryRenderedFeatures()` result as data, which is the whole reason the
 * predicate is separated from the map at all.
 *
 * The coverage decision this file encodes is the one recorded in the
 * module's own header: coverage is DETECTED, never declared, because every
 * provider's declared extent is unusable for it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/basemap_style_core.js';

const core = self.pwaBasemapStyleCore;

/** The catalogue as apps.trips.views._basemap_context emits it. */
const CATALOGUE = {
  openfreemap_liberty: 'https://tiles.openfreemap.org/styles/liberty',
  swisstopo_winter: 'https://vectortiles.geo.admin.ch/styles/winter/style.json',
  basemap_at: 'https://mapsneu.wien.gv.at/basemapvectorneu/root.json',
};

describe('resolveBasemap', () => {
  it('picks the stored key when it is still in the catalogue', () => {
    expect(core.resolveBasemap('swisstopo_winter', CATALOGUE, 'openfreemap_liberty'))
      .toEqual({
        key: 'swisstopo_winter',
        url: CATALOGUE.swisstopo_winter,
      });
  });

  it('falls back to the default when nothing is stored', () => {
    expect(core.resolveBasemap(null, CATALOGUE, 'openfreemap_liberty')).toEqual({
      key: 'openfreemap_liberty',
      url: CATALOGUE.openfreemap_liberty,
    });
  });

  it('falls back to the default for a key that has left the catalogue', () => {
    // A basemap removed since the reader last chose it. Not an error.
    expect(core.resolveBasemap('swisstopo_light', CATALOGUE, 'openfreemap_liberty'))
      .toEqual({
        key: 'openfreemap_liberty',
        url: CATALOGUE.openfreemap_liberty,
      });
  });

  it('returns null when the page carries no catalogue', () => {
    // The pre-SNOW-829 page shape. The caller then constructs on '' and
    // gets MapLibre's own failure path, not a thrown error here.
    expect(core.resolveBasemap('swisstopo_winter', null, 'openfreemap_liberty'))
      .toBeNull();
  });

  it('returns null when even the default is not in the catalogue', () => {
    expect(core.resolveBasemap(null, CATALOGUE, 'nope')).toBeNull();
  });
});

describe('resolveStyle', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('passes a native style straight through as its URL', async () => {
    expect(core.resolveStyle('swisstopo_winter', CATALOGUE.swisstopo_winter))
      .toBe(CATALOGUE.swisstopo_winter);
  });

  it('rewrites an ESRI style’s relative source path to absolute tiles', async () => {
    // basemap.at's vector source uses a relative `tile/{z}/{y}/{x}.pbf`
    // path MapLibre cannot resolve, so nothing paints without this.
    vi.stubGlobal('fetch', vi.fn(async () => ({
      json: async () => ({
        sources: {
          esri: { type: 'vector', url: 'https://example.test/service' },
        },
      }),
    })));

    const style = await core.resolveStyle('basemap_at', CATALOGUE.basemap_at);

    expect(style.sources.esri.tiles).toEqual([
      'https://example.test/service/tile/{z}/{y}/{x}.pbf',
    ]);
    expect(style.sources.esri.url).toBeUndefined();
  });

  it('leaves a source that already declares tiles alone', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      json: async () => ({
        sources: {
          esri: {
            type: 'vector',
            url: 'https://example.test/service',
            tiles: ['https://example.test/already/{z}/{x}/{y}.pbf'],
          },
        },
      }),
    })));

    const style = await core.resolveStyle('basemap_at', CATALOGUE.basemap_at);

    expect(style.sources.esri.tiles).toEqual([
      'https://example.test/already/{z}/{x}/{y}.pbf',
    ]);
  });
});

describe('drewNothing', () => {
  it('is true when only the page’s own sources drew', () => {
    // The out-of-coverage case: a national basemap outside its country.
    const features = [
      { source: 'trip-route' },
      { source: 'trip-meeting' },
    ];
    expect(core.drewNothing(features, ['trip-route', 'trip-meeting'])).toBe(true);
  });

  it('is FALSE when nothing at all drew, including our own line', () => {
    // A map still loading looks like this, and so does a
    // queryRenderedFeatures that is not answering. Neither is evidence
    // that the basemap is blank, and treating them as such would put the
    // notice over a perfectly good map.
    expect(core.drewNothing([], ['trip-route'])).toBe(false);
  });

  it('is false when the result carries none of our own sources', () => {
    // Same reasoning: without our line in the answer there is no proof the
    // query is live, so nothing can be concluded from what is missing.
    expect(core.drewNothing([{ source: 'other' }], ['trip-route'])).toBe(false);
  });

  it('is false as soon as one basemap feature drew', () => {
    const features = [
      { source: 'trip-route' },
      { source: 'openmaptiles' },
    ];
    expect(core.drewNothing(features, ['trip-route', 'trip-meeting'])).toBe(false);
  });

  it('is false for a non-array, so a failed query never reports blank', () => {
    expect(core.drewNothing(null, ['trip-route'])).toBe(false);
    expect(core.drewNothing(undefined, ['trip-route'])).toBe(false);
  });

  it('treats every feature as the basemap’s when we name no sources', () => {
    expect(core.drewNothing([{ source: 'trip-route' }], [])).toBe(false);
  });

  it('is false for a non-array even when sources are named', () => {
    expect(core.drewNothing('nope', ['trip-route'])).toBe(false);
  });
});
