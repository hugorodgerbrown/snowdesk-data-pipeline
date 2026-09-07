/*
 * tests/js/test_map_downloaded_overlay_colour.js — the "downloaded areas"
 * map overlay draws the ACTIVE basemap's downloads as a hatch in that
 * basemap's own identity colour (`cached-tiles-fill` / `cached-tiles-line`)
 * and every OTHER basemap's as an outline in the colour of the basemap it
 * was downloaded under (`cached-tiles-line-elsewhere`), swapping which is
 * which when the basemap changes under a switched-on overlay.
 *
 * This file has now covered FOUR rules in turn, and the history is worth
 * keeping because the current rule is a synthesis of the middle two rather
 * than a return to either.
 *
 *   1. The original overlay drew the active basemap's downloads in one
 *      flat colour.
 *   2. SNOW-645's review widened it to draw EVERY downloaded basemap at
 *      once, each in the colour of the basemap it was downloaded UNDER,
 *      via a `match` on a per-feature `basemapKey` — because switching
 *      basemap emptied the overlay and that read as data loss.
 *   3. Hugo's call after living with it: two basemaps' squares stacked
 *      over the same ground describe neither basemap's coverage. "It
 *      should filter to the current basemap, so it never overlays
 *      downloads." Back to one flat colour, one basemap.
 *   4. SNOW-857: both are drawn again, but with DIFFERENT MARKS — the
 *      active basemap hatched, the others outlined only. (3)'s reasoning
 *      was about two FILLS and still stands; it never applied to a fill
 *      and an outline, which is the distinction the download roundel has
 *      drawn since SNOW-645 (solid disc / hollow ring). The `match` from
 *      (2) comes back with it, because there is more than one answer on
 *      screen again.
 *
 * So the fixture seeds downloads under TWO basemaps — that is the case
 * that matters — and the assertions are about which mark each one gets,
 * and that the marks swap when the user switches basemap.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale. This file's stub additionally
 * TRACKS added layers, registered images and setPaintProperty calls (most
 * other harnesses' stubs are pure no-ops for these), which is the whole
 * point here, and lets the ACTIVE tile template be swapped mid-suite.
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
  // The live style's own tile template — what `activeBasemapTileTemplate`
  // reads, and so what decides which downloads the overlay draws. Mutable,
  // because a basemap swap is the behaviour under test.
  let activeTemplate = TEMPLATE_LIBERTY;
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
          ? { tiles: [activeTemplate] }
          : null,
    addSource: () => {},
    addLayer: (def) => {
      // SNOW-857: `filter` is recorded alongside the paint, because which
      // features a layer draws is now half of what this suite asserts —
      // the hatch and the two outline layers share one source and are
      // told apart by nothing else.
      layers.set(def.id, { ...(def.paint || {}), filter: def.filter });
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
    setFilter: (id, filter) => {
      const layer = layers.get(id);
      if (layer) layer.filter = filter;
    },
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
    // Swap the rendered basemap the way the picker does: the style's tile
    // template and the picker row's `aria-checked` both move, since
    // `activeBasemapTileTemplate` reads the former and `activeBasemapKey`
    // the latter.
    setActiveBasemap: (template, key) => {
      activeTemplate = template;
      for (const row of document.querySelectorAll('.basemap-menu-item[data-basemap-key]')) {
        row.setAttribute('aria-checked', String(row.dataset.basemapKey === key));
      }
    },
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
        >OpenFreeMap</button>
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
      // A region downloaded under OpenFreeMap (openfreemap_liberty).
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
  // The overlay's state IS persisted now, but this suite seeds no
  // localStorage, so it boots off — only window.pwaDownloadedOverlay.show()
  // / hide() (called from the panel's switch) flip it. Restoring a stored
  // preference at boot is tests/js/test_map_downloaded_overlay_boot.js's
  // subject, not this file's.
  await window.pwaDownloadedOverlay.show();
});

afterAll(() => {
  restoreCanvas();
  vi.unstubAllGlobals();
  document.documentElement.removeAttribute('style');
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('downloaded-areas overlay — this basemap filled, the others outlined', () => {
  it('draws BOTH basemaps\' squares, tagged with which is which', async () => {
    await waitFor(() => (mapStub.getCachedTilesData()?.features || []).length > 0);

    // SNOW-857 reverses SNOW-645's filter — see the module header. Two
    // areas are downloaded, one under OpenFreeMap (active) and one under
    // Swisstopo, and BOTH are now on the map. What SNOW-645 was protecting
    // against was two FILLS over the same ground; the other basemap's
    // square gets an outline instead, so `here` is what has to be right.
    const features = mapStub.getCachedTilesData().features;
    expect(features).toHaveLength(2);

    const here = features.filter((f) => f.properties.here);
    const elsewhere = features.filter((f) => !f.properties.here);
    expect(here).toHaveLength(1);
    expect(elsewhere).toHaveLength(1);
    expect(here[0].properties.basemapKey).toBe('openfreemap_liberty');
    expect(elsewhere[0].properties.basemapKey).toBe('swisstopo_winter');
  });

  it('hatches only the active basemap, in its own identity colour', async () => {
    await waitFor(() => mapStub.layers.get('cached-tiles-fill')?.filter !== undefined);

    // The hatch is the active basemap's mark alone — the filter is what
    // stops another basemap's square being hatched in this one's colour,
    // which would claim ground the reader does not have on this map.
    expect(mapStub.layers.get('cached-tiles-fill').filter)
      .toEqual(['==', ['get', 'here'], true]);
    expect(mapStub.layers.get('cached-tiles-fill')['fill-pattern'])
      .toBe('cached-tiles-hatch-openfreemap_liberty');
  });

  it('colours each outline by the basemap it was downloaded UNDER', async () => {
    await waitFor(
      () => Array.isArray(mapStub.layers.get('cached-tiles-line')?.['line-color']),
    );

    // The `match` SNOW-645 deleted, restored: with more than one answer on
    // screen a flat colour would paint the other basemap's outline in the
    // ACTIVE basemap's colour, saying it belongs to the map being looked
    // at — the one thing it does not. Mirrors the download roundel's
    // 'other-basemap' ring, which is likewise keyed on the other basemap.
    const colour = mapStub.layers.get('cached-tiles-line')['line-color'];
    expect(colour[0]).toBe('match');
    expect(colour[1]).toEqual(['get', 'basemapKey']);
    expect(colour).toContain('openfreemap_liberty');
    expect(colour).toContain(LIBERTY_COLOUR);
    expect(colour).toContain('swisstopo_winter');
    expect(colour).toContain(SWISSTOPO_COLOUR);
    // Both line layers share one expression, so they cannot disagree.
    expect(mapStub.layers.get('cached-tiles-line-elsewhere')['line-color']).toEqual(colour);
  });

  it('gives the elsewhere outlines a constant opacity, not the fade', async () => {
    await waitFor(() => mapStub.layers.has('cached-tiles-line-elsewhere'));

    // The active layer's outline fades out below about z12 because it is an
    // accent ON a hatch that carries the meaning without it. An elsewhere
    // outline is the ONLY mark its tile gets, so the same fade would make
    // every other-basemap download invisible below z10 — which is exactly
    // the zoomed-out view SNOW-856 made worth reading.
    expect(mapStub.layers.get('cached-tiles-line-elsewhere')['line-opacity'])
      .toBe(0.4);
    expect(Array.isArray(mapStub.layers.get('cached-tiles-line')['line-opacity']))
      .toBe(true);
    expect(mapStub.layers.get('cached-tiles-line-elsewhere').filter)
      .toEqual(['==', ['get', 'here'], false]);
  });

  it('registers the image the pattern names — an absent one paints nothing', async () => {
    await waitFor(() => mapStub.images.has('cached-tiles-hatch-openfreemap_liberty'));

    // The identity colour lives in the hatch's PIXELS (the mark that lets
    // the squares share a polygon with the danger choropleth), so this is
    // where a wrong-basemap colour would actually show up.
    const entry = mapStub.images.get('cached-tiles-hatch-openfreemap_liberty');
    expect(hatchColour(entry)).toEqual([1, 2, 3]);
  });

  it('adds the hatch at pixelRatio 2, so the period is in device pixels', async () => {
    await waitFor(() => mapStub.images.has('cached-tiles-hatch-openfreemap_liberty'));

    const entry = mapStub.images.get('cached-tiles-hatch-openfreemap_liberty');
    expect(entry.options).toEqual({ pixelRatio: 2 });
    expect(entry.image.width).toBe(globalThis.pwaHatchCore.SIZE);
  });

  it('swaps which basemap is filled when the basemap changes', async () => {
    // Hugo's SNOW-645 rule, in the form that survives SNOW-857: "if you
    // then switch maps it honours the toggle and shows the new map
    // downloads." Both are still drawn; which one is FILLED changes. The
    // overlay is already on — nothing here touches show()/hide().
    mapStub.setActiveBasemap(TEMPLATE_SWISSTOPO, 'swisstopo_winter');
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));

    await waitFor(
      () => mapStub.layers.get('cached-tiles-fill')?.['fill-pattern']
        === 'cached-tiles-hatch-swisstopo_winter',
    );

    for (const id of ['cached-tiles-fill', 'cached-tiles-line', 'cached-tiles-line-elsewhere']) {
      expect(mapStub.getLayoutProperty(id, 'visibility')).toBe('visible');
    }
    // The `here` flag has flipped between the two features rather than
    // either of them leaving the map.
    const features = mapStub.getCachedTilesData().features;
    expect(features).toHaveLength(2);
    expect(features.find((f) => f.properties.here).properties.basemapKey)
      .toBe('swisstopo_winter');
    expect(features.find((f) => !f.properties.here).properties.basemapKey)
      .toBe('openfreemap_liberty');
    expect(hatchColour(mapStub.images.get('cached-tiles-hatch-swisstopo_winter')))
      .toEqual([9, 8, 7]);

    mapStub.setActiveBasemap(TEMPLATE_LIBERTY, 'openfreemap_liberty');
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    await waitFor(
      () => mapStub.layers.get('cached-tiles-fill')?.['fill-pattern']
        === 'cached-tiles-hatch-openfreemap_liberty',
    );
  });
});

describe('downloaded-areas overlay — nothing downloaded under the active basemap', () => {
  it('empties the source and keeps the active basemap\'s colour', async () => {
    installDbStub([], []);
    installCachesStub([]);
    window.pwaDownloadedOverlay.hide();
    await window.pwaDownloadedOverlay.show();

    await waitFor(() => (mapStub.getCachedTilesData()?.features || []).length === 0);

    expect(mapStub.getCachedTilesData().features).toEqual([]);
    // The layers stay switched on and keep the active basemap's colour —
    // "on, with nothing to show" is a state the user asked for, and the
    // switch says so; see the isVisible/isEnabled note in map.js.
    //
    // A flat colour, not a `match`: with no downloaded basemaps there are
    // no arms to build one from, and MapLibre rejects an empty `match`
    // outright — so the expression is only assembled when something is
    // actually on disk to discriminate between.
    expect(mapStub.layers.get('cached-tiles-line')['line-color']).toBe(LIBERTY_COLOUR);
  });
});

describe('window.pwaDownloadedOverlay show()/hide()/isVisible() (SNOW-645 review)', () => {
  it('isVisible() reflects the real overlay flag, not a DOM read', () => {
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
