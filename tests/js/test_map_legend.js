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
 *     providers only while their key is in view;
 *   - each map mode keys its own layers and not the other's (SNOW-1421),
 *     even where the other's section was left showing by its module.
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
        <section data-legend-key="terrain_filter" data-terrain-filter-legend hidden>Steep</section>
        <section data-legend-key="ski_tours">Tours</section>
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
        <span data-legend-on="problem_terrain" hidden>On</span>
        <span data-legend-on="terrain_filter" hidden>On</span>
        <p id="map-attribution-text"><a href="#">© swisstopo</a> · swisstopo — swissALTI3D</p>
      </div>
    </div>
    <button id="map-legend-toggle" type="button">i</button>
  </div>`;

/** What queryRenderedFeatures answers, per layer id. */
let features;
/** Layers the stub style holds, visible unless in `hiddenLayers`. */
let layers;
/** Layers the stub style holds at `visibility: none`. */
let hiddenLayers;
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
  hiddenLayers = new Set();
  bulletinsOn = true;
  globalThis.readStorage = () => null;
  globalThis.writeStorage = () => {};
  globalThis.MAP = {
    on: vi.fn(),
    getLayer: (id) => (layers.has(id) ? { id } : undefined),
    getLayoutProperty: (id) => (hiddenLayers.has(id) ? 'none' : 'visible'),
    // A hidden layer renders nothing, as MapLibre's does.
    queryRenderedFeatures: ({ layers: ids }) => ids
      .filter((id) => !hiddenLayers.has(id))
      .flatMap((id) => features[id] || []),
  };
  window.pwaBulletinsLayer = { isVisible: () => bulletinsOn };
  window.pwaTerrainFilter = { availability: () => 'ok' };
  await load();
});

afterEach(() => {
  delete window.pwaBulletinsLayer;
  delete window.pwaTerrainFilter;
  delete window.pwaMapModeState;
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

describe('each map mode (SNOW-1421)', () => {
  const CONDITIONS_ONLY = ['bulletins', 'l3', 'problem_terrain', 'weather'];
  const TERRAIN_ONLY = ['terrain_filter', 'ski_tours', 'ski_summits', 'rest_zones'];
  const on = (name) => !document.querySelector(`[data-legend-on="${name}"]`).hidden;

  /**
   * The page as one mode leaves it: the reader had both terrain layers and
   * the tours on, so both terrain sections were shown by their modules,
   * and the mode holds the other's own (map.js's pwaMapModeState).
   *
   * @param {string[]} held The keys the mode holds.
   */
  function inMode(held) {
    window.pwaMapModeState = { isSuppressed: (k) => held.includes(k) };
    key('problem_terrain').hidden = false;
    key('terrain_filter').hidden = false;
    layers.add('ski-tours-line');
    features['ski-tours-line'] = [{ properties: { climbing: true } }];
    if (held.includes('ski_tours')) hiddenLayers.add('ski-tours-line');
  }

  it('keys Conditions\' own and none of Terrain\'s in Conditions', () => {
    inMode(TERRAIN_ONLY);
    open();
    expect(outOfView(key('problem_terrain'))).toBe(false);
    expect(outOfView(key('terrain_filter'))).toBe(true);
    expect(outOfView(key('ski_tours'))).toBe(true);
    expect(on('problem_terrain')).toBe(true);
    expect(on('terrain_filter')).toBe(false);
  });

  it('keys Terrain\'s own and none of Conditions\' in Terrain', () => {
    inMode(CONDITIONS_ONLY);
    open();
    expect(outOfView(key('problem_terrain'))).toBe(true);
    expect(outOfView(key('terrain_filter'))).toBe(false);
    expect(outOfView(key('ski_tours'))).toBe(false);
    expect(on('problem_terrain')).toBe(false);
    expect(on('terrain_filter')).toBe(true);
  });

  it('keys both terrain layers off the flag, where nothing is held', () => {
    key('problem_terrain').hidden = false;
    key('terrain_filter').hidden = false;
    open();
    expect(outOfView(key('problem_terrain'))).toBe(false);
    expect(outOfView(key('terrain_filter'))).toBe(false);
  });
});

describe("open(), for the phone app's Help menu", () => {
  it('opens the card on its first page and works the view out', () => {
    features['regions-fill'] = [{ properties: {} }];
    document.querySelector('[data-legend-go="all"]').click();

    window.pwaMapLegend.open();

    expect(document.getElementById('map-legend').dataset.state).toBe('expanded');
    expect(card().dataset.legendPage).toBe('now');
    expect(document.querySelector('[data-legend-sources]').textContent).toContain('SLF');
  });
});
