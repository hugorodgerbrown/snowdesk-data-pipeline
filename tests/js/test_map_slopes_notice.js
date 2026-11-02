/*
 * tests/js/test_map_slopes_notice.js — the slopes notice's DOM half
 * (SNOW-1378).
 *
 * map_slopes_notice.js reads the shared MapLibre `MAP` as the other surface
 * modules do, so it is booted here against a stub map, once per test. What
 * is pinned: a qualifying move shows the card once, with the region's key
 * fetched as HTMX and the whole-scale key kept when that fetch fails; a
 * move with a sheet open shows nothing; "Got it", Escape and a map tap
 * close it; and it is registered with window.pwaMapOverlays.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_slopes_notice_core.js';

const STATIC_JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js');
const SOURCE = readFileSync(path.join(STATIC_JS, 'map_slopes_notice.js'), 'utf8');

let handlers;
let camera;

/** A stub MapLibre map over `camera`. */
function stubMap() {
  return {
    on: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    off: (ev, cb) => { handlers[ev] = (handlers[ev] || []).filter((h) => h !== cb); },
    getLayer: (id) => (id === 'regions-fill' ? { id } : null),
    getZoom: () => camera.zoom,
    getCenter: () => ({ lng: 7.2, lat: 46.1 }),
    project: () => ({ x: 100, y: 200 }),
    queryRenderedFeatures: () => (camera.feature ? [camera.feature] : []),
  };
}

const notice = () => document.getElementById('map-slopes-notice');
const keyRows = () => notice().querySelector('[data-slopes-notice-key]').innerHTML.trim();
const fire = (ev) => { for (const cb of [...(handlers[ev] || [])]) cb(); };
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

/** Boot the module against a fresh stub map and the given fetch. */
function boot(fetchImpl) {
  globalThis.fetch = vi.fn(fetchImpl);
  globalThis.__noticeMap = stubMap();
  new Function(`const MAP = globalThis.__noticeMap;\n${SOURCE}`)();
}

beforeEach(async () => {
  handlers = {};
  camera = {
    zoom: 12.5,
    feature: { id: 3, properties: { id: 'CH-4115', country: 'CH' } },
  };
  localStorage.clear();
  document.documentElement.removeAttribute('data-map-sheet');
  document.body.innerHTML = `
    <div id="map">
      <div id="season-scrubber" data-date="2026-11-02"></div>
      <div id="map-slopes-notice" hidden data-key-url="/partials/slopes-notice-key/XX-0000/">
        <div data-slopes-notice-key><div class="map-legend-row">whole scale</div></div>
        <button id="map-slopes-notice-ok" type="button">Got it</button>
      </div>
    </div>`;
  delete window.pwaMapOverlays;
  vi.resetModules();
  await import('../../static/js/map_overlay_exclusivity.js');
  window.pwaBulletinTerrain = { isEnabled: () => true };
  window.pwaTerrainFilter = { isEnabled: () => false };
});

afterEach(() => {
  delete globalThis.__noticeMap;
  delete window.pwaBulletinTerrain;
  delete window.pwaTerrainFilter;
});

describe('the slopes notice', () => {
  it('shows once with the region key, fetched as HTMX for the map day', async () => {
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('<div>Considerable problem</div>') }));
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(false);
    expect(keyRows()).toBe('<div>Considerable problem</div>');
    const [url, options] = globalThis.fetch.mock.calls[0];
    expect(url).toBe('/partials/slopes-notice-key/ch-4115/?d=2026-11-02');
    expect(options.headers['HX-Request']).toBe('true');
    expect(localStorage.getItem('snowdesk.map.slopes_notice_seen')).not.toBeNull();
    // Once: the next move asks for nothing.
    notice().hidden = true;
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(true);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps the whole-scale key when the fetch fails or answers empty', async () => {
    boot(() => Promise.reject(new Error('offline')));
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(false);
    expect(keyRows()).toContain('whole scale');
  });

  it('does not show on a device that has seen it', async () => {
    localStorage.setItem('snowdesk.map.slopes_notice_seen', 'seen');
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('') }));
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(true);
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });

  it('waits while an object sheet is open, then shows on a later move', async () => {
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('') }));
    document.documentElement.setAttribute('data-map-sheet', 'peek');
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(true);
    document.documentElement.removeAttribute('data-map-sheet');
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(false);
  });

  it('closes on "Got it", on Escape and on a map tap', async () => {
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('') }));
    const open = () => { notice().hidden = false; };

    open();
    document.getElementById('map-slopes-notice-ok').click();
    expect(notice().hidden).toBe(true);

    open();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(notice().hidden).toBe(true);

    open();
    fire('click');
    expect(notice().hidden).toBe(true);
  });

  it('gives the device its turn back when dismissed while its key is loading', async () => {
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('') }));
    fire('moveend');
    fire('click');
    await flush();
    // It never reached the screen, so it is not recorded as seen…
    expect(notice().hidden).toBe(true);
    expect(localStorage.getItem('snowdesk.map.slopes_notice_seen')).toBeNull();
    // …and the next qualifying move shows it.
    fire('moveend');
    await flush();
    expect(notice().hidden).toBe(false);
    expect(localStorage.getItem('snowdesk.map.slopes_notice_seen')).toBe('seen');
  });

  it('stays hidden, unseen, when another overlay opened while its key loaded', async () => {
    let settle;
    boot(() => new Promise((resolve) => { settle = resolve; }));
    let menuOpen = false;
    window.pwaMapOverlays.register('basemap-menu', {
      isOpen: () => menuOpen,
      close: () => { menuOpen = false; },
    });
    fire('moveend');
    // The reader opens the layers menu while the key is still loading.
    window.pwaMapOverlays.opening('basemap-menu');
    menuOpen = true;
    settle({ ok: true, text: () => Promise.resolve('') });
    await flush();
    await flush();
    expect(notice().hidden).toBe(true);
    expect(menuOpen).toBe(true);
    expect(localStorage.getItem('snowdesk.map.slopes_notice_seen')).toBeNull();
    // With the menu closed again, the next qualifying move shows it.
    menuOpen = false;
    fire('moveend');
    settle({ ok: true, text: () => Promise.resolve('') });
    await flush();
    await flush();
    expect(notice().hidden).toBe(false);
  });

  it('registers as a map overlay under its id', () => {
    boot(() => Promise.resolve({ ok: true, text: () => Promise.resolve('') }));
    expect(window.pwaMapOverlays.names()).toContain('map-slopes-notice');
  });
});
