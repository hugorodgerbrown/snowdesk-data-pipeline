/*
 * tests/js/test_route_draw_signed_out.js — a draft stamped with an account
 * survives a sign-out or an expired session (SNOW-1262).
 *
 * route_draw_core.js's draftState calls it 'hidden': someone's draft while
 * no one is signed in. It is neither destroyed nor shown nor overwritten —
 * only a DIFFERENT signed-in account discards it (test_route_draw_offline.js).
 * This file loads the real queue with no <meta name="pwa-user-id">, so the
 * principal is the anonymous null; even an acknowledgement for the draft's
 * key leaves it alone until its owner is back.
 */

import { describe, expect, it, vi } from 'vitest';

const CREATE_URL = '/routes/drawn/';
const DRAFT_KEY = 'snowdesk.route-draft';
const POINTS = [[7, 46], [7, 46.001]];

document.body.innerHTML = `
  <div id="route-draw-sheet" hidden></div>
  <form id="route-draw-config" hidden data-preview-url="" data-create-url="${CREATE_URL}"
        data-signin-url="/signin/" data-save-eligible="true">
    <input name="csrfmiddlewaretoken" value="csrf-token">
  </form>
  <div id="route-draw-crosshair" hidden></div>
  <div id="map"></div>
  <template id="route-draw-template">
    <p data-route-draw-hint></p>
    <p data-route-draw-stats></p>
    <p data-route-draw-notice hidden></p>
    <button type="button" data-route-draw-undo>Undo</button>
    <button type="button" data-route-draw-add>Add point</button>
    <form data-route-draw-save>
      <input id="route-draw-name">
      <button type="submit" data-route-draw-submit>Save</button>
    </form>
    <button type="button" data-route-draw-clear>Start over</button>
  </template>`;

const STAMPED = JSON.stringify({
  v: 1, points: POINTS, name: 'Mont Fort', taps: [0, 1], key: 'k-1', sent: 'abc', principal: 'me',
});
localStorage.setItem(DRAFT_KEY, STAMPED);
await import('../../static/js/i18n_strings.js');
await import('../../static/js/db.js');
await window.pwaDb.put('meta:app', {
  key: 'mutations.ack:k-1',
  value: { kind: 'route-create', uuid: 'u', at: Date.now() },
});
await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/mutation_queue.js');
await import('../../static/js/route_draw_core.js');
const toast = vi.fn();
const sheet = document.getElementById('route-draw-sheet');
let drawController = null;
window.MapSheet = {
  toast,
  attach: (el, options) => {
    let open = false;
    drawController = {
      open: () => {
        open = true;
        el.hidden = false;
      },
      close: () => {
        if (!open) return;
        open = false;
        el.hidden = true;
        options.onTeardown();
      },
      isOpen: () => open,
    };
    return drawController;
  },
};
const sources = new Map();
window.snowdeskMapState = {
  map: {
    getSource: (id) => sources.get(id) || null,
    addSource: (id) => sources.set(id, { setData: () => {} }),
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: (id) => sources.delete(id),
    getStyle: () => ({ layers: [], sources: {} }),
    getContainer: () => document.getElementById('map'),
    on: () => {},
    off: () => {},
    getZoom: () => 10,
    getCenter: () => ({ lng: 7, lat: 46 }),
    unproject: () => ({ lng: 7, lat: 46 }),
  },
};
await import('../../static/js/route_draw.js');

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

describe('a stamped draft with no one signed in', () => {
  it('is kept exactly as stored, through load and queue changes', async () => {
    await settle();
    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));
    await settle();

    expect(localStorage.getItem(DRAFT_KEY)).toBe(STAMPED);
  });

  it('is not shown in draw mode, and drawing does not overwrite it', async () => {
    window.pwaRouteDraw.start();
    await settle();

    expect(sheet.querySelector('[data-route-draw-stats]').textContent).toBe('No points yet.');
    sheet.querySelector('#route-draw-name').value = 'Another';
    sheet.querySelector('#route-draw-name').dispatchEvent(new Event('input'));
    expect(localStorage.getItem(DRAFT_KEY)).toBe(STAMPED);
    drawController.close();
  });
});
