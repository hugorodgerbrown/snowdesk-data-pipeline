/*
 * tests/js/test_map_downloaded_overlay_colour.js — Vitest DOM test for
 * SNOW-645 (review item — "every basemap at once"): the "downloaded areas"
 * map overlay (`cached-tiles-fill` / `cached-tiles-line`) paints EACH tile
 * in the identity colour of the basemap it was downloaded UNDER, not the
 * one active on screen right now.
 *
 * This supersedes an earlier version of this file, which covered a
 * single-colour-tied-to-the-active-basemap mechanism — Hugo's own
 * follow-up call, after the overlay itself was rebuilt to show downloads
 * across every basemap at once rather than emptying out on a basemap
 * switch (see refreshDownloadedOverlay's own "EVERY BASEMAP AT ONCE"
 * comment in map.js). The mechanism is a MapLibre `match` PAINT EXPRESSION
 * keyed on each tile feature's own `basemapKey` property, not a flat colour
 * re-resolved on a basemap-changed event — this file tests that shape.
 *
 * There are TWO such expressions now that the squares are a hatch rather
 * than a tint (the mark that lets them share a polygon with the danger
 * choropleth): `downloadedTilesColourExpression` still resolves each
 * area's OUTLINE colour, and `downloadedTilesPatternExpression` resolves
 * its FILL to one hatch image per basemap — the identity colour having
 * moved into that image's pixels. Both are asserted, along with the
 * registration of every image named, since a `fill-pattern` pointing at an
 * image the style is not holding paints nothing at all and says nothing.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale. This file's stub additionally
 * TRACKS added layers and setPaintProperty calls (most other harnesses'
 * stubs are pure no-ops for these, since nothing before this ticket needed
 * to assert on paint state), which is the whole point here.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TEMPLATE_LIBERTY = 'https://tiles-liberty.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_SWISSTOPO = 'https://tiles-swisstopo.example.invalid/{z}/{x}/{y}.pbf';

const LIBERTY_COLOUR = 'rgb(1, 2, 3)';
const SWISSTOPO_COLOUR = 'rgb(9, 8, 7)';
const SYNC_OK_COLOUR = 'rgb(4, 5, 6)';

const CACHED_TILES_ZOOM = 14;

/** Empty regions collection — this suite only cares about the overlay's colour. */
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub, mirroring test_map_download_bytes.js's, but with
 * `addLayer` / `getLayer` / `setPaintProperty` backed by a real `layers`
 * Map (id -> paint object) and a `sources` Map (id -> data) so a test can
 * read back both the paint expression AND the feature data
 * refreshDownloadedOverlay builds from them.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const images = new Map();
  let cachedTilesData = null;
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: (ev, cb) => {
      (handlers[ev] ||= []).push(cb);
    },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => {
      const layout = layouts.get(id);
      return layout ? layout[prop] : undefined;
    },
    getPaintProperty: (id, prop) => {
      const paint = layers.get(id);
      return paint ? paint[prop] : undefined;
    },
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) =>
      id === 'cached-tiles'
        ? { setData: (data) => { cachedTilesData = data; } }
        : id === 'basemap'
          ? { tiles: [TEMPLATE_LIBERTY] }
          : null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
      layouts.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id);
      if (paint) paint[prop] = value;
    },
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: { basemap: { type: 'vector' } } }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => Array.from(images.keys()),
    // Tracked for real, not stubbed true: a `fill-pattern` naming an image
    // the style is not holding paints NOTHING — no error, no warning, just
    // an overlay that silently fails to appear. `hasImage` lying would hide
    // exactly that.
    hasImage: (id) => images.has(id),
    addImage: (id, image, options) => { images.set(id, { image, options }); },
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5,
      getSouth: () => 45,
      getEast: () => 10,
      getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    layers,
    images,
    getCachedTilesData: () => cachedTilesData,
  };
  globalThis.maplibregl = {
    Map: function () {
      return map;
    },
    Popup: function () {
      return {
        setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }),
        remove: () => {},
      };
    },
    GeolocateControl: function () {
      return { on: () => {} };
    },
    AttributionControl: function () {
      return {};
    },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/**
 * Cache Storage stub with one pinned bucket per `{name, urls}` entry —
 * mirrors SNOW-586's one-bucket-per-downloaded-area shape closely enough
 * for `pinnedBasemapCacheURLs` (static/js/map_basemap_downloads.js) to
 * union across them, which is all this suite needs from it.
 */
function installCachesStub(buckets) {
  const stub = {
    keys: vi.fn(async () => buckets.map((b) => b.name)),
    open: vi.fn(async (name) => {
      const bucket = buckets.find((b) => b.name === name);
      const urls = bucket ? bucket.urls : [];
      return {
        keys: async () => urls.map((url) => ({ url })),
        put: async () => {},
        match: async () => undefined,
      };
    }),
    delete: vi.fn(async () => {}),
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/**
 * `window.pwaDb` stub seeded with the raw `basemap.regions` /
 * `basemap.customAreas` records `basemapDownloadedTemplates()` reads
 * directly (see that function's own docstring — it is a sibling of
 * `basemapDownloadedAreas()`, not a wrapper, so this suite has to write
 * the SAME record shape map_region_download.js's `_recordRegionDownload`
 * does, not the normalised `areas()` shape).
 */
function installDbStub(regions, customAreas) {
  const rows = new Map([
    ['basemap.regions', regions || []],
    ['basemap.customAreas', customAreas || []],
  ]);
  window.pwaDb = {
    get: vi.fn(async (_store, key) =>
      rows.has(key) ? { key, value: rows.get(key) } : undefined,
    ),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
    }),
    delete: vi.fn(async () => {}),
  };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="true"
        >Standard</button>
      </li>
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="swisstopo_winter"
          data-basemap-url="https://tiles.example.invalid/swisstopo.json"
          aria-checked="false"
        >Swisstopo (CH)</button>
      </li>
    </ul>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/**
 * jsdom ships no 2D canvas, and `map.js` resolves an identity colour to
 * three channels by filling a 1×1 one — the only way to parse every CSS
 * colour syntax a token might hold. This is the narrowest double that keeps
 * that path honest: it parses the `rgb(r, g, b)` values THIS file sets on
 * the tokens, so an image built for the wrong basemap's colour still fails.
 *
 * @returns {() => void} Restores the original `getContext`.
 */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    let channels = [0, 0, 0, 255];
    return {
      set fillStyle(value) {
        const m = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(String(value));
        channels = m ? [Number(m[1]), Number(m[2]), Number(m[3]), 255] : [0, 0, 0, 255];
      },
      get fillStyle() {
        return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`;
      },
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from(channels) }),
    };
  };
  return () => { HTMLCanvasElement.prototype.getContext = original; };
}

/** The RGB a registered hatch image was built in, read back off its pixels. */
function hatchColour(entry) {
  return Array.from(entry.image.data.slice(0, 3));
}

/** Read a `['match', ['get', 'basemapKey'], key, value, …, fallback]` expression as a plain object. */
function matchArms(expr) {
  if (!Array.isArray(expr)) return null;
  const arms = {};
  for (let i = 2; i + 1 < expr.length; i += 2) arms[expr[i]] = expr[i + 1];
  return { arms, fallback: expr[expr.length - 1] };
}

let mapStub;
let restoreCanvas;

beforeAll(async () => {
  buildFixture();
  restoreCanvas = stubCanvas2D();
  document.documentElement.style.setProperty(
    '--color-basemap-openfreemap-liberty',
    LIBERTY_COLOUR,
  );
  document.documentElement.style.setProperty(
    '--color-basemap-swisstopo-winter',
    SWISSTOPO_COLOUR,
  );
  document.documentElement.style.setProperty('--color-sync-ok', SYNC_OK_COLOUR);

  installDbStub(
    [
      // A region downloaded under Standard (openfreemap_liberty).
      {
        region_id: 'CH-2101',
        name: 'Aletsch',
        template: TEMPLATE_LIBERTY,
        basemapKey: 'openfreemap_liberty',
        bytes: 1000,
        savedAt: '2026-08-01T10:00:00.000Z',
      },
    ],
    [
      // A custom area downloaded under Swisstopo Winter.
      {
        id: 'custom-a1',
        ordinal: 1,
        bbox: [7.9, 46.4, 8.1, 46.6],
        template: TEMPLATE_SWISSTOPO,
        basemapKey: 'swisstopo_winter',
        bytes: 2000,
        savedAt: '2026-08-01T10:00:00.000Z',
      },
    ],
  );

  installCachesStub([
    {
      name: 'snowdesk-basemap-pinned-CH-2101',
      urls: [`https://tiles-liberty.example.invalid/${CACHED_TILES_ZOOM}/100/200.pbf`],
    },
    {
      name: 'snowdesk-basemap-pinned-custom-a1',
      urls: [`https://tiles-swisstopo.example.invalid/${CACHED_TILES_ZOOM}/300/400.pbf`],
    },
  ]);

  mapStub = stubMapLibre();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      let body = {};
      if (href.includes('regions.geojson')) body = REGIONS_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; installRegionsLayers (and so the
  // two cached-tiles-* layers) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
  // downloadedOverlayVisible is session-scoped, off by default — only
  // window.pwaDownloadedOverlay.show()/hide() (called from the sheet's
  // toggle) ever flip it, so a test must call show() itself rather than
  // relying on any persisted flag.
  await window.pwaDownloadedOverlay.show();
});

afterAll(() => {
  restoreCanvas();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute('style');
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('downloaded-areas overlay colour (SNOW-645 review — every basemap at once)', () => {
  it('outlines each area in a match expression with one arm per basemap', async () => {
    await waitFor(() => {
      const expr = mapStub.layers.get('cached-tiles-line')?.['line-color'];
      return Array.isArray(expr) && expr[0] === 'match';
    });

    const lineExpr = mapStub.layers.get('cached-tiles-line')['line-color'];
    expect(lineExpr[0]).toBe('match');
    expect(lineExpr[1]).toEqual(['get', 'basemapKey']);

    const { arms, fallback } = matchArms(lineExpr);
    expect(arms.openfreemap_liberty).toBe(LIBERTY_COLOUR);
    expect(arms.swisstopo_winter).toBe(SWISSTOPO_COLOUR);
    expect(fallback).toBe(SYNC_OK_COLOUR);
  });

  it('fills each area with a hatch image in its own basemap identity colour', async () => {
    // The fill carries a PATTERN, not a colour: the squares share their
    // polygons with the danger choropleth, and a flat tint over it both
    // obscured the danger colour and shifted it. The per-basemap identity
    // survives the change — it moved from the arms of a colour expression
    // into the pixels of one image per basemap.
    await waitFor(() => {
      const expr = mapStub.layers.get('cached-tiles-fill')?.['fill-pattern'];
      return Array.isArray(expr) && expr[0] === 'match';
    });

    const fillExpr = mapStub.layers.get('cached-tiles-fill')['fill-pattern'];
    expect(fillExpr[1]).toEqual(['get', 'basemapKey']);

    const { arms, fallback } = matchArms(fillExpr);
    expect(arms.openfreemap_liberty).toBe('cached-tiles-hatch-openfreemap_liberty');
    expect(arms.swisstopo_winter).toBe('cached-tiles-hatch-swisstopo_winter');
    expect(fallback).toBe('cached-tiles-hatch-default');

    expect(hatchColour(mapStub.images.get(arms.openfreemap_liberty))).toEqual([1, 2, 3]);
    expect(hatchColour(mapStub.images.get(arms.swisstopo_winter))).toEqual([9, 8, 7]);
    expect(hatchColour(mapStub.images.get(fallback))).toEqual([4, 5, 6]);
  });

  it('registers every image the pattern names — an absent one paints nothing', async () => {
    await waitFor(() => {
      const expr = mapStub.layers.get('cached-tiles-fill')?.['fill-pattern'];
      return Array.isArray(expr) && expr[0] === 'match';
    });

    const fillExpr = mapStub.layers.get('cached-tiles-fill')['fill-pattern'];
    const { arms, fallback } = matchArms(fillExpr);
    for (const id of [...Object.values(arms), fallback]) {
      expect(mapStub.images.has(id)).toBe(true);
    }
  });

  it('adds the hatch at pixelRatio 2, so the period is in device pixels', async () => {
    await waitFor(() => mapStub.images.has('cached-tiles-hatch-default'));

    const entry = mapStub.images.get('cached-tiles-hatch-default');
    expect(entry.options).toEqual({ pixelRatio: 2 });
    expect(entry.image.width).toBe(globalThis.pwaHatchCore.SIZE);
  });

  it('tags each feature with the basemapKey it was downloaded under', async () => {
    const data = mapStub.getCachedTilesData();
    const keys = new Set(data.features.map((f) => f.properties.basemapKey));
    expect(keys).toEqual(new Set(['openfreemap_liberty', 'swisstopo_winter']));
  });
});

describe('downloaded-areas overlay colour — nothing downloaded', () => {
  it('falls back to a bare id and a flat colour with no keys to match on', async () => {
    installDbStub([], []);
    installCachesStub([]);
    window.pwaDownloadedOverlay.hide();
    await window.pwaDownloadedOverlay.show();

    // Not a one-armed `match` — with nothing to key off, both properties
    // take their fallback directly.
    expect(mapStub.layers.get('cached-tiles-fill')['fill-pattern'])
      .toBe('cached-tiles-hatch-default');
    expect(mapStub.layers.get('cached-tiles-line')['line-color']).toBe(SYNC_OK_COLOUR);
  });
});

describe('window.pwaDownloadedOverlay show()/hide()/isVisible() (SNOW-645 review)', () => {
  it('isVisible() reflects the real session-scoped flag, not a DOM read', () => {
    window.pwaDownloadedOverlay.hide();
    expect(window.pwaDownloadedOverlay.isVisible()).toBe(false);
  });

  it('show() flips isVisible() to true', async () => {
    window.pwaDownloadedOverlay.hide();
    await window.pwaDownloadedOverlay.show();
    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);
  });

  it('hide() flips isVisible() back to false and hides the layers', async () => {
    await window.pwaDownloadedOverlay.show();
    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);

    window.pwaDownloadedOverlay.hide();

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(false);
    for (const id of ['cached-tiles-fill', 'cached-tiles-line']) {
      expect(mapStub.getLayoutProperty(id, 'visibility')).toBe('none');
    }
  });

  it('show() sets both layers visible', async () => {
    window.pwaDownloadedOverlay.hide();
    await window.pwaDownloadedOverlay.show();

    for (const id of ['cached-tiles-fill', 'cached-tiles-line']) {
      expect(mapStub.getLayoutProperty(id, 'visibility')).toBe('visible');
    }
  });
});
