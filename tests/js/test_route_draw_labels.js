/*
 * tests/js/test_route_draw_labels.js — what the draw panel says before its
 * first point and while a profile is built (SNOW-1321).
 *
 * Hugo, 30 Oct 2026: the first point "should say 'Set start location', not
 * 'Add point'", and while the profile is worked out the panel "should say
 * 'Building profile' in the blank space". This loads route_draw.js on a
 * phone (a coarse pointer, so the cross and the button are live) with a
 * preview endpoint whose answer the test holds back.
 */

import { afterAll, describe, expect, it, vi } from 'vitest';

const PREVIEW_URL = '/routes/draw/preview/';

document.body.innerHTML = `
  <div id="route-draw-sheet" hidden></div>
  <form id="route-draw-config" hidden data-preview-url="${PREVIEW_URL}" data-create-url="/routes/drawn/"
        data-signin-url="/signin/" data-save-eligible="true">
    <input name="csrfmiddlewaretoken" value="csrf-token">
  </form>
  <div id="route-draw-crosshair" hidden></div>
  <div id="map"></div>
  <template id="route-draw-template">
    <p data-route-draw-hint></p>
    <p data-route-draw-stats></p>
    <div data-route-draw-building hidden><p>Building profile…</p></div>
    <p data-route-draw-notice hidden></p>
    <button type="button" data-route-draw-undo>Undo</button>
    <button type="button" data-route-draw-add><span data-route-draw-add-label>Set start location</span></button>
    <form data-route-draw-save>
      <input id="route-draw-name">
      <button type="submit" data-route-draw-submit>Save</button>
    </form>
    <button type="button" data-route-draw-clear>Start over</button>
  </template>`;

window.matchMedia = (query) => ({
  matches: query === '(pointer: coarse)',
  addEventListener: () => {},
  removeEventListener: () => {},
});

// Each tap lands a little further north, so no two are the same point.
let taps = 0;
const sources = new Map();
window.snowdeskMapState = {
  map: {
    getSource: (id) => sources.get(id) || null,
    addSource: (id) => sources.set(id, { setData: () => {} }),
    addLayer: () => {},
    getLayer: () => null,
    removeLayer: () => {},
    removeSource: (id) => sources.delete(id),
    getStyle: () => ({ layers: [], sources: {} }),
    getContainer: () => document.getElementById('map'),
    on: () => {},
    off: () => {},
    getZoom: () => 10,
    getCenter: () => ({ lng: 7, lat: 46 }),
    unproject: () => {
      taps += 1;
      return { lng: 7, lat: 46 + taps * 0.001 };
    },
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

// The preview's answer waits until the test releases it, as a drawn
// preview (ok) or a server error.
let release = (_ok) => {};
const fetchSpy = vi.fn(
  () =>
    new Promise((resolve) => {
      release = (ok = true) =>
        resolve({
          ok,
          status: ok ? 200 : 500,
          json: () =>
            Promise.resolve({
              feature: {
                type: 'Feature',
                geometry: { type: 'LineString', coordinates: [] },
                properties: { distance_m: 111 },
              },
            }),
        });
    }),
);
vi.stubGlobal('fetch', fetchSpy);

await import('../../static/js/i18n_strings.js');
await import('../../static/js/route_draw_core.js');
await import('../../static/js/route_draw.js');

const sheet = document.getElementById('route-draw-sheet');
const label = () => sheet.querySelector('[data-route-draw-add-label]').textContent;
const building = () => sheet.querySelector('[data-route-draw-building]');
const tap = () => sheet.querySelector('[data-route-draw-add]').click();
const settle = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

afterAll(() => {
  vi.unstubAllGlobals();
});

describe('the draw panel before and after its first point', () => {
  it('asks for the start location first, then for points', () => {
    window.pwaRouteDraw.start();

    expect(label()).toBe('Set start location');
    tap();
    expect(label()).toBe('Add point');
    expect(building().hidden).toBe(true);
  });

  it('says the profile is being built until the preview is drawn', async () => {
    tap();
    expect(building().hidden).toBe(false);

    await settle(400);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(building().hidden).toBe(false);

    release();
    await settle();
    expect(building().hidden).toBe(true);
  });

  it('stops saying so when the preview fails, and shows the failure', async () => {
    tap();
    await settle(400);
    expect(building().hidden).toBe(false);

    release(false);
    await settle();
    expect(building().hidden).toBe(true);
    expect(sheet.querySelector('[data-route-draw-notice]').hidden).toBe(false);
  });

  it('never says so offline, where no preview is asked for', async () => {
    const calls = fetchSpy.mock.calls.length;
    window.pwaConnectivity = { isOnline: () => false };
    tap();
    expect(building().hidden).toBe(true);

    await settle(400);
    expect(building().hidden).toBe(true);
    expect(fetchSpy.mock.calls.length).toBe(calls);
    delete window.pwaConnectivity;
  });

  it('stops saying so when Undo leaves fewer than two points', async () => {
    tap();
    expect(building().hidden).toBe(false);
    const undo = sheet.querySelector('[data-route-draw-undo]');
    while (!undo.disabled) undo.click();

    expect(building().hidden).toBe(true);
    expect(label()).toBe('Set start location');
  });
});
