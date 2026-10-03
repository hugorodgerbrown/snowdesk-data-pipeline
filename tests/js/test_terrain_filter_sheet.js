/*
 * tests/js/test_terrain_filter_sheet.js — the terrain filter's sheet, chip and
 * menu-row summary (SNOW-978).
 *
 * terrain_filter_sheet.js talks to one thing, window.pwaTerrainFilter, so the
 * bridge is stubbed here with a small in-memory state that announces its
 * changes exactly as map.js's does. What is under test is the sheet's half:
 * a fresh body cloned on every open (MapSheet empties it on close), controls
 * seeded from the bridge, edits handed back to it, and every word read from
 * the strings template rather than the English fallback.
 *
 * The fixture mirrors the hooks of
 * apps/public/templates/public/partials/_map_terrain_filter_sheet.html.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** In-memory bridge with map.js's announcement. */
function stubBridge() {
  const state = {
    enabled: false,
    filter: { aspects: [0, 1, 2, 3, 4, 5, 6, 7], minSlope: 30, maxSlope: null, minElevation: null, maxElevation: null },
    availability: 'ok',
  };
  const announce = () => document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));
  const bridge = {
    state,
    isEnabled: () => state.enabled,
    show: vi.fn(() => { state.enabled = true; announce(); }),
    hide: vi.fn(() => { state.enabled = false; announce(); }),
    getFilter: () => self.pwaTerrainFilterCore.normaliseFilter(state.filter),
    setFilter: vi.fn((next) => {
      state.filter = self.pwaTerrainFilterCore.normaliseFilter(next);
      announce();
    }),
    availability: () => state.availability,
  };
  window.pwaTerrainFilter = bridge;
  return bridge;
}

/** The sheet, its two templates, the chip, the menu row and the legend. */
function buildFixture() {
  document.documentElement.lang = 'en';
  const compass = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  document.body.innerHTML = `
    <div id="map">
      <button id="map-terrain-filter-chip" data-terrain-filter-open hidden>
        <span data-terrain-filter-chip-text></span>
      </button>
    </div>
    <button data-terrain-filter-open><span data-row-label>Terrain filter…</span>
      <span data-terrain-filter-summary hidden></span></button>
    <section data-terrain-filter-legend hidden></section>
    <div id="map-terrain-filter-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="map-terrain-filter-template">
      <h2>Terrain filter</h2>
      <input id="map-terrain-filter-enabled" type="checkbox" role="switch">
      <p data-terrain-filter-availability></p>
      ${compass.map((c, i) => `<button type="button" data-octant="${i}" aria-pressed="false">${c}</button>`).join('')}
      <select data-terrain-filter-min-slope>
        ${[30, 35, 40, 45, 50].map((d) => `<option value="${d}">${d}°</option>`).join('')}
      </select>
      <select data-terrain-filter-max-slope>
        <option value="">No limit</option>
        ${[35, 40, 45, 50].map((d) => `<option value="${d}">${d}°</option>`).join('')}
      </select>
      <input type="number" step="100" data-terrain-filter-min-elevation>
      <input type="number" step="100" data-terrain-filter-max-elevation>
    </template>
    <template id="map-terrain-filter-strings-template">
      <span data-string="availability-ok">Hervorgehoben.</span>
      <span data-string="availability-off">Aus.</span>
      <span data-string="availability-zoom-in">Näher heranzoomen.</span>
      <span data-string="aspects-all">Alle Expositionen</span>
      <span data-string="aspects-count">%(count)s Expositionen</span>
      <span data-string="slope-min">ab %(min)s°</span>
      <span data-string="elevation-above">über %(min)s m</span>
      <span data-string="compass-0">N</span>
      <span data-string="compass-7">NW</span>
    </template>`;
}

/** @type {ReturnType<typeof stubBridge>} */
let bridge;
const sheetEl = () => document.getElementById('map-terrain-filter-sheet');
const q = (selector) => sheetEl().querySelector(selector);
const changeEvent = () => new Event('change', { bubbles: true });

beforeAll(async () => {
  buildFixture();
  bridge = stubBridge();
  vi.resetModules();
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/terrain_filter_core.js');
  await import('../../static/js/terrain_filter_sheet.js');
});

afterAll(() => {
  delete window.pwaTerrainFilter;
  delete window.pwaTerrainFilterSheet;
  delete window.MapSheet;
});

beforeEach(() => {
  if (window.pwaTerrainFilterSheet.isOpen()) document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  bridge.state.enabled = false;
  bridge.state.filter = { aspects: [0, 1, 2, 3, 4, 5, 6, 7], minSlope: 30 };
  bridge.state.availability = 'ok';
  bridge.setFilter.mockClear();
  bridge.show.mockClear();
});

describe('opening the sheet', () => {
  it('clones a fresh body on every open', () => {
    window.pwaTerrainFilterSheet.open();
    expect(sheetEl().hidden).toBe(false);
    q('[data-octant="3"]').setAttribute('data-touched', '1');
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(sheetEl().children.length).toBe(0);
    window.pwaTerrainFilterSheet.open();
    expect(q('[data-octant="3"]').hasAttribute('data-touched')).toBe(false);
  });

  it('seeds every control from the bridge', () => {
    bridge.state.enabled = true;
    bridge.state.filter = { aspects: [0, 7], minSlope: 35, maxSlope: 45, minElevation: 2400 };
    window.pwaTerrainFilterSheet.open();
    expect(q('#map-terrain-filter-enabled').checked).toBe(true);
    const pressed = [...sheetEl().querySelectorAll('[data-octant][aria-pressed="true"]')]
      .map((b) => b.dataset.octant);
    expect(pressed).toEqual(['0', '7']);
    expect(q('[data-terrain-filter-min-slope]').value).toBe('35');
    expect(q('[data-terrain-filter-max-slope]').value).toBe('45');
    expect(q('[data-terrain-filter-min-elevation]').value).toBe('2400');
    expect(q('[data-terrain-filter-max-elevation]').value).toBe('');
    // A maximum at or under the minimum is greyed out.
    expect(q('[data-terrain-filter-max-slope] option[value="35"]').disabled).toBe(true);
    expect(q('[data-terrain-filter-max-slope] option[value="40"]').disabled).toBe(false);
  });

  it('opens from the chip', () => {
    document.getElementById('map-terrain-filter-chip').click();
    expect(window.pwaTerrainFilterSheet.isOpen()).toBe(true);
  });
});

describe('editing', () => {
  it('turns the filter on and off through the bridge', () => {
    window.pwaTerrainFilterSheet.open();
    const toggle = q('#map-terrain-filter-enabled');
    toggle.checked = true;
    toggle.dispatchEvent(changeEvent());
    expect(bridge.show).toHaveBeenCalledTimes(1);
    toggle.checked = false;
    toggle.dispatchEvent(changeEvent());
    expect(bridge.hide).toHaveBeenCalled();
  });

  it('hands an aspect toggle back as a whole filter', () => {
    window.pwaTerrainFilterSheet.open();
    q('[data-octant="4"]').click();
    expect(bridge.setFilter).toHaveBeenCalledTimes(1);
    expect(bridge.setFilter.mock.calls[0][0]).toMatchObject({
      aspects: [0, 1, 2, 3, 5, 6, 7],
      minSlope: 30,
      maxSlope: null,
    });
    expect(q('[data-octant="4"]').getAttribute('aria-pressed')).toBe('false');
  });

  it('reads the slope and elevation controls, blank meaning none', () => {
    window.pwaTerrainFilterSheet.open();
    q('[data-terrain-filter-min-slope]').value = '40';
    q('[data-terrain-filter-min-elevation]').value = '2500';
    q('[data-terrain-filter-min-elevation]').dispatchEvent(changeEvent());
    expect(bridge.setFilter.mock.calls.at(-1)[0]).toMatchObject({
      minSlope: 40,
      maxSlope: null,
      minElevation: 2500,
      maxElevation: null,
    });
  });
});

describe('the words', () => {
  it('reads the availability line from the strings template', () => {
    bridge.state.enabled = true;
    bridge.state.availability = 'zoom-in';
    window.pwaTerrainFilterSheet.open();
    expect(q('[data-terrain-filter-availability]').textContent).toBe('Näher heranzoomen.');
    bridge.state.enabled = false;
    document.dispatchEvent(new CustomEvent('snowdesk:terrain-filter-changed'));
    expect(q('[data-terrain-filter-availability]').textContent).toBe('Aus.');
  });

  it('shows the chip, the row summary and the legend only while the filter is on', () => {
    bridge.state.filter = { aspects: [0, 7], minSlope: 35, minElevation: 2400 };
    bridge.show();
    const chip = document.getElementById('map-terrain-filter-chip');
    const summary = document.querySelector('[data-terrain-filter-summary]');
    expect(chip.hidden).toBe(false);
    expect(chip.textContent.trim()).toBe('N, NW · ab 35° · über 2,400 m');
    expect(summary.hidden).toBe(false);
    expect(summary.textContent).toBe('2 Expositionen · ab 35°');
    expect(document.querySelector('[data-terrain-filter-legend]').hidden).toBe(false);
    bridge.hide();
    expect(chip.hidden).toBe(true);
    expect(summary.hidden).toBe(true);
    expect(document.querySelector('[data-terrain-filter-legend]').hidden).toBe(true);
  });
});
