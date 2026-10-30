/*
 * tests/js/test_route_draw_network.js — which ski-touring network draw mode
 * loads and shows, by zoom and by the Follow switch (SNOW-1322).
 *
 * Hugo, 30 Oct 2026: "the Swisstopo routes should appear at the same zoom
 * as the SAC. And if the user toggles the snap option they should
 * appear". This loads route_draw.js against a map whose zoom the test
 * sets, with a network endpoint whose answers the test holds back, and
 * checks that the thinned overview is asked for once at z9-z11, that the
 * per-view detail is asked for from z12 and is what the snap graph is
 * built from, and that switching Follow off hides both layers and stops
 * a load already under way.
 */

import { afterAll, describe, expect, it, vi } from 'vitest';

const NETWORK_URL = '/api/ski-network.geojson';

document.body.innerHTML = `
  <div id="route-draw-sheet" hidden></div>
  <form id="route-draw-config" hidden data-preview-url="/routes/drawn/preview/"
        data-create-url="/routes/drawn/" data-signin-url="/signin/" data-save-eligible="true"
        data-network-url="${NETWORK_URL}">
    <input name="csrfmiddlewaretoken" value="csrf-token">
  </form>
  <div id="route-draw-crosshair" hidden></div>
  <div id="map"></div>
  <template id="route-draw-template">
    <p data-route-draw-hint></p>
    <p data-route-draw-stats></p>
    <p data-route-draw-notice hidden></p>
    <button type="button" role="switch" aria-checked="true" data-route-draw-follow hidden><span data-route-draw-follow-label>Snap to known ski routes</span></button>
    <button type="button" data-route-draw-undo>Undo</button>
    <button type="button" data-route-draw-add><span data-route-draw-add-label>Set start location</span></button>
    <form data-route-draw-save><input id="route-draw-name"><button type="submit">Save</button></form>
    <button type="button" data-route-draw-clear>Start over</button>
  </template>`;

window.matchMedia = () => ({
  matches: false,
  addEventListener: () => {},
  removeEventListener: () => {},
});

// A map whose zoom the test sets; a move ends when the test says so.
let zoom = 10;
let east = 0;
const handlers = new Map();
const sources = new Map();
const layers = new Map();
window.snowdeskMapState = {
  map: {
    getSource: (id) => sources.get(id) || null,
    addSource: (id, spec) =>
      sources.set(id, {
        data: spec.data,
        setData(d) {
          this.data = d;
        },
      }),
    addLayer: (spec) => layers.set(spec.id, spec),
    getLayer: (id) => layers.get(id) || null,
    removeLayer: (id) => layers.delete(id),
    removeSource: (id) => sources.delete(id),
    setLayoutProperty: (id, key, value) => {
      layers.get(id).layout = { ...layers.get(id).layout, [key]: value };
    },
    getStyle: () => ({ layers: [], sources: {} }),
    getContainer: () => document.getElementById('map'),
    on: (name, fn) => handlers.set(name, fn),
    off: () => {},
    getZoom: () => zoom,
    getCenter: () => ({ lng: 7.3 + east, lat: 46.1 }),
    getBounds: () => ({
      getWest: () => 7.25 + east,
      getEast: () => 7.35 + east,
      getSouth: () => 46.05,
      getNorth: () => 46.15,
    }),
  },
};
window.MapSheet = {
  toast: () => {},
  attach: (el) => ({
    open: () => {
      el.hidden = false;
    },
    close: () => {
      el.hidden = true;
    },
    isOpen: () => !el.hidden,
  }),
};

const LINE = {
  type: 'MultiLineString',
  coordinates: [
    [
      [7.3, 46.1],
      [7.31, 46.11],
    ],
  ],
};
// Each request waits until the test answers it.
const pending = [];
const fetchSpy = vi.fn(
  (url, init) =>
    new Promise((resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      pending.push(() =>
        resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              type: 'FeatureCollection',
              truncated: false,
              features: [
                {
                  type: 'Feature',
                  id: String(url).includes('bbox') ? 'seg-1' : undefined,
                  geometry: LINE,
                  properties: {},
                },
              ],
            }),
        }),
      );
    }),
);
vi.stubGlobal('fetch', fetchSpy);

await import('../../static/js/i18n_strings.js');
await import('../../static/js/route_draw_core.js');
await import('../../static/js/ski_snap_core.js');
const realSnap = self.pwaSkiSnapCore;
const buildGraph = vi.fn(realSnap.buildGraph);
self.pwaSkiSnapCore = { ...realSnap, buildGraph };
await import('../../static/js/route_draw.js');

const sheet = document.getElementById('route-draw-sheet');
const settle = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
const networkCalls = () => fetchSpy.mock.calls.map((c) => String(c[0])).filter((u) => u.startsWith(NETWORK_URL));
const visibility = () =>
  ['route-draft-network-halo', 'route-draft-network-line'].map((id) => layers.get(id).layout.visibility);
const drawn = () => sources.get('route-draft-network').data.features;
const moveTo = async (z) => {
  zoom = z;
  handlers.get('moveend')();
  await settle(300);
};
const answer = async () => {
  while (pending.length) pending.shift()();
  await settle();
};

afterAll(() => {
  vi.unstubAllGlobals();
});

describe('the network in draw mode', () => {
  it('draws from z9, the zoom the SAC tours start at', () => {
    window.pwaRouteDraw.start();
    expect(layers.get('route-draft-network-line').minzoom).toBe(9);
    expect(layers.get('route-draft-network-halo').minzoom).toBe(9);
    expect(visibility()).toEqual(['visible', 'visible']);
  });

  it('asks for the thinned overview once at z10, never again on a move, and never snaps to it', async () => {
    await settle(300);
    expect(networkCalls()).toEqual([NETWORK_URL + '?overview=1']);
    await answer();
    expect(drawn()).toHaveLength(1);
    await moveTo(10.5);
    await moveTo(11);
    expect(networkCalls()).toEqual([NETWORK_URL + '?overview=1']);
    expect(buildGraph).not.toHaveBeenCalled();
  });

  it('loads the view from z12 and builds the snap graph from that alone', async () => {
    await moveTo(12.5);
    const calls = networkCalls();
    expect(calls).toHaveLength(2);
    expect(calls[1]).toMatch(/\?bbox=/);
    await answer();
    expect(buildGraph).toHaveBeenCalledTimes(1);
    expect(buildGraph.mock.calls[0][0].map((f) => f.id)).toEqual(['seg-1']);
    expect(drawn().map((f) => f.id)).toEqual(['seg-1']);
  });

  it('draws the overview again, not the detail, back below z12', async () => {
    await moveTo(10);
    expect(drawn()).toHaveLength(1);
    expect(drawn()[0].id).toBeUndefined();
  });

  it('hides both layers when Follow is switched off and stops a load under way', async () => {
    // Somewhere not yet loaded, so a request goes out.
    east = 0.5;
    zoom = 13;
    handlers.get('moveend')();
    await settle(300);
    expect(pending).toHaveLength(1);
    const toggle = sheet.querySelector('[data-route-draw-follow]');
    toggle.click();
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(visibility()).toEqual(['none', 'none']);
    expect(drawn()).toEqual([]);
    await answer();
    expect(buildGraph).toHaveBeenCalledTimes(1);
  });

  it('shows them again when Follow is switched back on', async () => {
    const calls = networkCalls().length;
    sheet.querySelector('[data-route-draw-follow]').click();
    expect(visibility()).toEqual(['visible', 'visible']);
    await settle(300);
    expect(networkCalls().length).toBe(calls + 1);
  });

  it('disables the switch below z12 and says why in its label, keeping its value', async () => {
    const toggle = sheet.querySelector('[data-route-draw-follow]');
    const label = () => sheet.querySelector('[data-route-draw-follow-label]').textContent;
    expect(toggle.disabled).toBe(false);
    expect(label()).toBe('Snap to known ski routes');
    await moveTo(10);
    expect(toggle.disabled).toBe(true);
    expect(label()).toBe('Zoom in to enable snap-to-route');
    toggle.click();
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    expect(localStorage.getItem('snowdesk.route-draw-follow')).toBe('on');
    expect(visibility()).toEqual(['visible', 'visible']);
  });

  it('enables it again from z12, where a click toggles it', async () => {
    const toggle = sheet.querySelector('[data-route-draw-follow]');
    await moveTo(12.5);
    expect(toggle.disabled).toBe(false);
    expect(sheet.querySelector('[data-route-draw-follow-label]').textContent).toBe('Snap to known ski routes');
    toggle.click();
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });
});
