/*
 * tests/js/test_map_legend.js — the legend card's two tiers, wired
 * (static/js/map_legend.js against a reduced copy of the card, with the
 * map and map.js's bridges stood in for by stubs).
 *
 * What is pinned, because each is the way this goes wrong:
 *   - opening the card shows only the keys the view draws, and a row of a
 *     key only while its mark is in view;
 *   - a key its own module hid stays hidden: the legend writes
 *     data-legend-out-of-view, never `hidden`, on the four module keys;
 *   - with nothing to key, the card says so;
 *   - the card always opens on its first page, and each page leads to the
 *     other, moving focus;
 *   - the second tier's "On" marks follow what is switched on;
 *   - the one-line credit names the tile providers, and the avalanche
 *     providers only while their key is in view.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <div id="map-legend" data-state="collapsed">
    <div id="map-legend-card" data-legend-page="now">
      <div data-legend-panel="now">
        <section data-legend-key="danger">Danger</section>
        <section data-legend-key="problem_terrain" data-problem-terrain-legend hidden>Problem</section>
        <section data-legend-key="community_reports">
          <div data-legend-item="report-WARNING"></div>
          <div data-legend-item="report-SURFACE"></div>
          <div data-legend-item="report-live"></div>
          <div data-legend-item="report-recent"></div>
        </section>
        <section data-legend-key="routes" hidden>Routes</section>
        <section data-legend-key="pins">
          <div data-legend-item="pin-resort"></div>
          <div data-legend-item="pin-favourite"></div>
        </section>
        <p data-legend-empty hidden>Nothing</p>
        <p data-legend-sources></p>
        <button type="button" data-legend-go="all">All keys</button>
      </div>
      <div data-legend-panel="all" hidden>
        <button type="button" data-legend-go="now">Back</button>
        <span data-legend-on="danger" hidden>On</span>
        <span data-legend-on="routes" hidden>On</span>
        <p id="map-attribution-text"><a href="#">© swisstopo</a> · swisstopo — swissALTI3D</p>
      </div>
    </div>
    <button id="map-legend-toggle" type="button">i</button>
  </div>`;

/** What queryRenderedFeatures answers, per layer id. */
let features;
/** Layers the stub style holds, all visible. */
let layers;
let bulletinsOn;

const card = () => document.getElementById('map-legend-card');
const key = (name) => document.querySelector(`[data-legend-key="${name}"]`);
const item = (name) => document.querySelector(`[data-legend-item="${name}"]`);
const outOfView = (el) => el.hasAttribute('data-legend-out-of-view');
const open = () => document.getElementById('map-legend-toggle').click();

async function load() {
  vi.resetModules();
  await import('../../static/js/map_legend_core.js');
  await import('../../static/js/map_legend.js');
}

beforeEach(async () => {
  document.body.innerHTML = MARKUP;
  features = {};
  layers = new Set(['regions-fill', 'community-reports-point', 'resorts-pin', 'favourites-pin']);
  bulletinsOn = true;
  globalThis.readStorage = () => null;
  globalThis.writeStorage = () => {};
  globalThis.MAP = {
    on: vi.fn(),
    getLayer: (id) => (layers.has(id) ? { id } : undefined),
    getLayoutProperty: () => 'visible',
    queryRenderedFeatures: ({ layers: ids }) => ids.flatMap((id) => features[id] || []),
  };
  window.pwaBulletinsLayer = { isVisible: () => bulletinsOn };
  window.pwaTerrainFilter = { availability: () => 'ok' };
  await load();
});

afterEach(() => {
  delete window.pwaBulletinsLayer;
  delete window.pwaTerrainFilter;
  globalThis.MAP = null;
});

describe('the first tier', () => {
  it('keys only what the view draws', () => {
    features['regions-fill'] = [{ properties: {} }];
    features['community-reports-point'] = [{ properties: { family: 'SURFACE', _state: 'live' } }];
    open();
    expect(outOfView(key('danger'))).toBe(false);
    expect(outOfView(key('community_reports'))).toBe(false);
    expect(outOfView(item('report-SURFACE'))).toBe(false);
    expect(outOfView(item('report-WARNING'))).toBe(true);
    expect(outOfView(item('report-live'))).toBe(false);
    expect(outOfView(item('report-recent'))).toBe(true);
    expect(outOfView(key('pins'))).toBe(true);
    expect(document.querySelector('[data-legend-empty]').hidden).toBe(true);
  });

  it('drops the danger key while the bulletins are off, though the regions answer', () => {
    features['regions-fill'] = [{ properties: {} }];
    bulletinsOn = false;
    open();
    expect(outOfView(key('danger'))).toBe(true);
  });

  it('never writes `hidden` on a key its own module switches', () => {
    features['routes-leg-climb'] = [{ properties: {} }];
    layers.add('routes-leg-climb');
    open();
    expect(key('routes').hidden).toBe(true);
    expect(key('problem_terrain').hidden).toBe(true);
  });

  it('shows a module key once its module shows it and a leg is in view', async () => {
    features['routes-leg-climb'] = [{ properties: {} }];
    layers.add('routes-leg-climb');
    open();
    key('routes').hidden = false;
    // The observer queues a refresh for the next frame.
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    await new Promise((resolve) => requestAnimationFrame(() => resolve()));
    expect(outOfView(key('routes'))).toBe(false);
    expect(document.querySelector('[data-legend-on="routes"]').hidden).toBe(false);
  });

  it('says so when nothing in view needs a key', () => {
    open();
    expect(document.querySelector('[data-legend-empty]').hidden).toBe(false);
  });

  it('follows the camera once the card is open', () => {
    open();
    expect(globalThis.MAP.on).toHaveBeenCalledWith('idle', expect.any(Function));
  });
});

describe('the credit line', () => {
  it('names the tile providers once, and the avalanche ones with their key', () => {
    open();
    const line = () => document.querySelector('[data-legend-sources]').textContent;
    expect(line()).toBe('© swisstopo');
    document.getElementById('map-legend-toggle').click();
    features['regions-fill'] = [{ properties: {} }];
    open();
    expect(line()).toBe('© swisstopo · SLF · ALBINA · Météo-France');
  });
});

describe('the two pages', () => {
  it('goes to all keys and back, moving focus', () => {
    open();
    document.querySelector('[data-legend-go="all"]').click();
    expect(card().dataset.legendPage).toBe('all');
    expect(document.querySelector('[data-legend-panel="now"]').hidden).toBe(true);
    expect(document.querySelector('[data-legend-panel="all"]').hidden).toBe(false);
    expect(document.activeElement).toBe(document.querySelector('[data-legend-go="now"]'));
    document.querySelector('[data-legend-go="now"]').click();
    expect(card().dataset.legendPage).toBe('now');
    expect(document.activeElement).toBe(document.querySelector('[data-legend-go="all"]'));
  });

  it('always opens on the first page', () => {
    open();
    document.querySelector('[data-legend-go="all"]').click();
    document.getElementById('map-legend-toggle').click();
    open();
    expect(card().dataset.legendPage).toBe('now');
    expect(document.querySelector('[data-legend-panel="all"]').hidden).toBe(true);
  });

  it('marks what is switched on, wherever the view is', () => {
    bulletinsOn = true;
    open();
    expect(document.querySelector('[data-legend-on="danger"]').hidden).toBe(false);
    expect(document.querySelector('[data-legend-on="routes"]').hidden).toBe(true);
  });
});
