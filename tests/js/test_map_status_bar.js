/*
 * tests/js/test_map_status_bar.js — the map status bar's DOM half (SNOW-1392).
 *
 * map_status_bar.js reads the shared MapLibre `MAP`, so it is booted here
 * against a stub map, as the region chip's test does. What is pinned: it
 * draws the segments after a move, re-reads on the slopes and filter
 * events, reads the checked basemap row, hides when it has nothing to say,
 * and the region chip drops its second line while the bar is on the page.
 */

import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_status_bar_core.js';
import '../../static/js/map_region_chip_core.js';

const STATIC_JS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../static/js');

const handlers = {};
const camera = { zoom: 12, feature: null, edge: 'visible' };
const state = { slopes: true, filter: false };

const stubMap = {
  on: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
  getLayer: (id) => (['regions-fill', 'regions-edge'].includes(id) ? { id } : null),
  getLayoutProperty: (id, prop) => (id === 'regions-edge' && prop === 'visibility' ? camera.edge : undefined),
  getZoom: () => camera.zoom,
  getCenter: () => ({ lng: 7.4, lat: 46.1 }),
  project: () => ({ x: 100, y: 200 }),
  queryRenderedFeatures: () => (camera.feature ? [camera.feature] : []),
  getFeatureState: () => ({ terrain: true }),
};

const bar = () => document.getElementById('map-status-bar');
const kinds = () => [...bar().querySelectorAll('.map-status-segment')].map((el) => el.dataset.kind);
const moveEnd = () => { for (const cb of handlers.moveend || []) cb(); };
const boot = (file) => {
  const source = readFileSync(path.join(STATIC_JS, file), 'utf8');
  new Function(`const MAP = globalThis.__barMap;\n${source}`)();
};

beforeAll(() => {
  document.body.innerHTML = `
    <div id="map">
      <div id="basemap-menu">
        <button class="basemap-menu-item" data-basemap-key="swisstopo_winter" aria-checked="true">
          <span class="sync-dot"></span> Swisstopo (CH)
        </button>
        <button class="basemap-menu-item" data-basemap-key="osm" aria-checked="false">OpenFreeMap</button>
      </div>
      <div class="map-status-row">
        <button type="button" id="map-region-chip" hidden>
          <span data-region-chip-level data-level="no_rating"></span>
          <span data-region-chip-name></span>
          <span data-region-chip-detail hidden></span>
        </button>
        <div id="map-status-bar" hidden></div>
      </div>
      <template id="map-status-bar-strings-template">
        <span data-string="region">Region danger</span>
        <span data-string="region-edge">at the edges</span>
        <span data-string="slopes">Problem slopes</span>
        <span data-string="slopes-detail">from the bulletin</span>
        <span data-string="zoom-slopes">Zoom in for the bulletin's slopes</span>
        <span data-string="label">The map shows</span>
      </template>
    </div>`;
  window.pwaRegionLevel = { forRegion: (id) => (id === 'CH-4122' ? 'moderate' : null) };
  window.pwaBulletinTerrain = { isEnabled: () => state.slopes && !state.filter };
  window.pwaTerrainFilter = { isEnabled: () => state.filter };
  camera.feature = { id: 3, properties: { id: 'CH-4122', name: "Val d'Hérens", country: 'CH' } };
  globalThis.__barMap = stubMap;
  boot('map_status_bar.js');
  boot('map_region_chip.js');
});

afterAll(() => {
  delete globalThis.__barMap;
  delete window.pwaRegionLevel;
  delete window.pwaBulletinTerrain;
  delete window.pwaTerrainFilter;
});

describe('the map status bar', () => {
  it('names the edge band, the slopes and the basemap at z12', () => {
    moveEnd();
    expect(bar().hidden).toBe(false);
    expect(kinds()).toEqual(['region-edge', 'slopes', 'basemap']);
    expect(bar().textContent).toContain('Problem slopes from the bulletin');
    expect(bar().textContent).toContain('Swisstopo (CH)');
    expect(bar().querySelector('.map-status-swatch').dataset.level).toBe('moderate');
    expect(bar().getAttribute('aria-label')).toBe('The map shows');
  });

  it('points at zooming in below z12', () => {
    camera.zoom = 11;
    moveEnd();
    expect(kinds()).toEqual(['region-edge', 'basemap']);
    expect(bar().querySelector('.map-status-hint').textContent).toBe("Zoom in for the bulletin's slopes");
    camera.zoom = 12;
    moveEnd();
  });

  it('re-reads when the slopes are switched off', () => {
    state.slopes = false;
    document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed'));
    expect(kinds()).toEqual(['region-edge', 'slopes-off', 'basemap']);
    state.slopes = true;
    document.dispatchEvent(new CustomEvent('snowdesk:problem-terrain-changed'));
  });

  it('drops the region with the bulletin layer off', () => {
    camera.edge = 'none';
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    expect(kinds()).toEqual(['slopes', 'basemap']);
    camera.edge = 'visible';
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
  });

  it('leaves the region chip to its region and level', () => {
    moveEnd();
    const chip = document.getElementById('map-region-chip');
    expect(chip.hidden).toBe(false);
    expect(chip.querySelector('[data-region-chip-detail]').hidden).toBe(true);
  });
});
