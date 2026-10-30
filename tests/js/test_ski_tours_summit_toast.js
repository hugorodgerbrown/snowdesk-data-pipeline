/*
 * tests/js/test_ski_tours_summit_toast.js — a summit picked in search says
 * when its tours need signal (SNOW-1320).
 *
 * Search's summit rows read offline (map.js loadSummits); the tours do not.
 * So a summit picked without signal used to land on a map with no tours
 * and no reason. ski_tours.js's `openSummit` now either toasts at once,
 * when the device knows it is offline (`navigator.onLine === false`), or
 * sets a one-shot mark the next tours fetch consumes, toasting if that
 * fetch rejects or answers non-OK.
 *
 * What must NOT toast is as much the subject as what must: a fetch that
 * lands, a later failure once the mark is spent, and the other ways of
 * turning the layer on (its switch, "Find a route"), which never set it.
 *
 * ski_tours.js is booted alone against stubs: the map (whose `flyTo` ends
 * in a `moveend`, as MapLibre's does), the ski-touring switches
 * (window.pwaSkiRoutes) and MapSheet's toast.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const GEOJSON_URL = '/api/ski-tours.geojson';
const TOAST = 'Tours need signal';
const EMPTY_FC = { type: 'FeatureCollection', features: [] };
const SUMMIT = { key: 'rosablanche', lon: 7.33, lat: 46.06 };

/** A MapLibre stub that keeps its zoom and centre, and moves on `flyTo`. */
function stubMap() {
  const handlers = {};
  const layers = new Set();
  const sources = new Map();
  const view = { zoom: 8, lng: 7.3, lat: 46.0 };
  const fire = (ev) => (handlers[ev] || []).forEach((cb) => cb());
  const map = {
    on: (ev, cb) => {
      (handlers[ev] ||= []).push(cb);
    },
    isStyleLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id) => sources.set(id, { setData: () => {} }),
    getLayer: (id) => (layers.has(id) ? { id } : null),
    addLayer: (def) => layers.add(def.id),
    setLayoutProperty: () => {},
    getContainer: () => document.body,
    getZoom: () => view.zoom,
    getBounds: () => ({
      getWest: () => view.lng - 0.05,
      getSouth: () => view.lat - 0.05,
      getEast: () => view.lng + 0.05,
      getNorth: () => view.lat + 0.05,
    }),
    flyTo: ({ center, zoom }) => {
      [view.lng, view.lat] = center;
      view.zoom = zoom;
      fire('moveend');
    },
    fire,
  };
  return map;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

let toast;
let tourFetches;

/**
 * Boot ski_tours.js with the tours fetch answering `answer`.
 *
 * @param {function(): Promise<any>} answer
 * @returns {Promise<any>} The map stub.
 */
async function boot(answer) {
  document.body.innerHTML = `
    <div id="ski-tours-config" data-geojson-url="${GEOJSON_URL}"></div>
    <div id="ski-tours-hint" hidden><span data-ski-tours-hint-text></span></div>
    <template id="ski-tour-claim-template"><div></div></template>`;
  toast = vi.fn();
  window.MapSheet = { toast };
  const map = stubMap();
  window.snowdeskMapState = { map };
  let on = false;
  window.pwaSkiRoutes = {
    isShown: () => on,
    isPartShown: () => on,
    show: () => {
      on = true;
      document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    },
  };
  tourFetches = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      if (String(url).startsWith(GEOJSON_URL)) {
        tourFetches += 1;
        return answer();
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    }),
  );
  vi.resetModules();
  await import('../../static/js/ski_tours_core.js');
  await import('../../static/js/ski_tours.js');
  return map;
}

const rejects = () => Promise.reject(new TypeError('Failed to fetch'));
const notOk = () => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });
const lands = () => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) });

/** Let every scheduled load fire and its fetch settle. */
async function settle(fetches = 1) {
  await waitFor(() => tourFetches >= fetches);
  await new Promise((resolve) => setTimeout(resolve, 50));
}

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, 'onLine');
  delete window.MapSheet;
  delete window.snowdeskMapState;
  delete window.pwaSkiRoutes;
  delete window.pwaSkiTours;
});

describe('a summit picked in search', () => {
  it('toasts when the next tours fetch rejects', async () => {
    await boot(rejects);
    window.pwaSkiTours.openSummit(SUMMIT);
    await settle();

    expect(toast).toHaveBeenCalledTimes(1);
    expect(toast).toHaveBeenCalledWith(TOAST);
  });

  it('toasts when the next tours fetch answers non-OK', async () => {
    await boot(notOk);
    window.pwaSkiTours.openSummit(SUMMIT);
    await settle();

    expect(toast).toHaveBeenCalledWith(TOAST);
  });

  it('says nothing when the tours land', async () => {
    await boot(lands);
    window.pwaSkiTours.openSummit(SUMMIT);
    await settle();

    expect(toast).not.toHaveBeenCalled();
  });

  it('toasts at once when the device knows it is offline, and only once', async () => {
    await boot(rejects);
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    window.pwaSkiTours.openSummit(SUMMIT);

    expect(toast).toHaveBeenCalledWith(TOAST);
    await settle();
    expect(toast).toHaveBeenCalledTimes(1);
  });

  it('is one-shot: a later failed fetch says nothing', async () => {
    const map = await boot(rejects);
    window.pwaSkiTours.openSummit(SUMMIT);
    await settle();
    expect(toast).toHaveBeenCalledTimes(1);

    map.flyTo({ center: [7.6, 46.2], zoom: 13 });
    await settle(2);
    expect(toast).toHaveBeenCalledTimes(1);
  });
});

describe('the layer turned on any other way', () => {
  it('never toasts from its switch', async () => {
    const map = await boot(rejects);
    window.pwaSkiRoutes.show();
    map.flyTo({ center: [7.3, 46.06], zoom: 13 });
    await settle();

    expect(toast).not.toHaveBeenCalled();
  });

  it('never toasts from "Find a route"', async () => {
    const map = await boot(rejects);
    window.pwaSkiTours.find();
    map.flyTo({ center: [7.3, 46.06], zoom: 13 });
    await settle();

    expect(toast).not.toHaveBeenCalled();
  });
});
