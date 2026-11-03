/*
 * tests/js/test_map_region_chip.js — the region chip's DOM half (SNOW-1376).
 *
 * map_region_chip.js reads the shared MapLibre `MAP` as the other surface
 * modules do, so it is booted here against a stub map: the region under the
 * centre comes from `queryRenderedFeatures` on `regions-fill`, the level
 * from `window.pwaRegionLevel`, and the painted-slopes state from feature
 * state. What is pinned: it follows the camera, it re-reads on the slopes
 * and filter events, and a tap opens the region without reframing the map.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_region_chip_core.js';

const STATIC_JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js');

const handlers = {};
const camera = { zoom: 12, feature: null, terrain: false };
const queried = [];
const opened = [];

const stubMap = {
  on: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
  getLayer: (id) => (id === 'regions-fill' ? { id } : null),
  getZoom: () => camera.zoom,
  getCenter: () => ({ lng: 7.2, lat: 46.1 }),
  project: () => ({ x: 100, y: 200 }),
  queryRenderedFeatures: (point, options) => {
    queried.push({ point, options });
    return camera.feature ? [camera.feature] : [];
  },
  getFeatureState: () => ({ terrain: camera.terrain }),
};

const chip = () => document.getElementById('map-region-chip');
const moveEnd = () => { for (const cb of handlers.moveend || []) cb(); };

beforeAll(() => {
  document.body.innerHTML = `
    <div id="map">
      <button type="button" id="map-region-chip" hidden>
        <span data-region-chip-level data-level="no_rating"></span>
        <span data-region-chip-name></span>
        <span data-region-chip-detail hidden></span>
      </button>
      <template id="map-region-chip-strings-template">
        <span data-string="slopes">slopes</span>
        <span data-string="ch-only">Slopes are drawn in Switzerland only</span>
        <span data-string="filter">Your filter</span>
        <span data-string="open">Open the region</span>
        <span data-string="level">danger level {level}</span>
      </template>
      <span data-terrain-filter-summary>35–40° NE E SE</span>
    </div>`;
  window.pwaRegionLevel = { forRegion: (id) => (id === 'CH-4115' ? 'considerable' : null) };
  window.pwaBulletinTerrain = { isEnabled: () => true };
  let filterOn = false;
  window.pwaTerrainFilter = { isEnabled: () => filterOn, set: (on) => { filterOn = on; } };
  document.addEventListener('snowdesk:region-open', (e) => opened.push(e.detail));
  globalThis.__chipMap = stubMap;
  const source = readFileSync(path.join(STATIC_JS, 'map_region_chip.js'), 'utf8');
  new Function(`const MAP = globalThis.__chipMap;\n${source}`)();
});

afterAll(() => {
  delete globalThis.__chipMap;
  delete window.pwaRegionLevel;
  delete window.pwaBulletinTerrain;
  delete window.pwaTerrainFilter;
});

describe('the region chip', () => {
  it('shows the region under the centre after a move, querying regions-fill', () => {
    camera.feature = { id: 7, properties: { id: 'CH-4115', name: 'Martigny-Verbier', country: 'CH' } };
    camera.terrain = true;
    moveEnd();
    expect(queried.at(-1).options).toEqual({ layers: ['regions-fill'] });
    expect(chip().hidden).toBe(false);
    expect(chip().querySelector('[data-region-chip-name]').textContent).toBe('Martigny-Verbier');
    const level = chip().querySelector('[data-region-chip-level]');
    expect(level.dataset.level).toBe('considerable');
    expect(level.textContent).toBe('3');
    expect(chip().querySelector('[data-region-chip-detail]').textContent).toBe('slopes');
  });

  it('names the level and the slopes line to a screen reader', () => {
    // The square is aria-hidden and the label replaces the text, so the
    // label alone has to carry what the chip shows.
    expect(chip().getAttribute('aria-label'))
      .toBe('Open the region: Martigny-Verbier, danger level 3, slopes');
  });

  it('hides at z9', () => {
    camera.zoom = 9;
    moveEnd();
    expect(chip().hidden).toBe(true);
    camera.zoom = 12;
    moveEnd();
  });

  it('re-reads when the terrain filter changes', () => {
    window.pwaTerrainFilter.set(true);
    document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));
    expect(chip().querySelector('[data-region-chip-detail]').textContent)
      .toBe('Your filter · 35–40° NE E SE');
    window.pwaTerrainFilter.set(false);
    document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed'));
    expect(chip().querySelector('[data-region-chip-detail]').textContent).toBe('slopes');
  });

  it('opens the shown region on a tap, without reframing the map', () => {
    chip().click();
    expect(opened.at(-1)).toEqual({ region_id: 'CH-4115', frame: false });
  });

  it('hides in Terrain mode and comes back in Dated (SNOW-1394)', () => {
    // Terrain holds the regions with the bulletins, so the chip has no
    // region and level to name; it hears the mode change, not just a move.
    let terrain = true;
    window.pwaMapMode = { isTerrain: () => terrain };
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'terrain' } }));
    expect(chip().hidden).toBe(true);
    chip().click();
    expect(opened).toHaveLength(1);

    terrain = false;
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'dated' } }));
    expect(chip().hidden).toBe(false);
    delete window.pwaMapMode;
  });
});
