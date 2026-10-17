/*
 * tests/js/test_terrain_filter_sheet.js — the terrain filter's sheet, legend and
 * menu-row second line (SNOW-978, SNOW-1109).
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

/** The sheet, its two templates, the menu row and the legend. */
function buildFixture() {
  document.documentElement.lang = 'en';
  const compass = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
  const ramp = ['30–35', '35–40', '40–45', '45–50', '50+'];
  document.body.innerHTML = `
    <div id="map"></div>
    <li class="basemap-menu-row">
      <button data-overlay-key="terrain" aria-checked="false"><span data-row-label>Terrain filter</span>
        <span data-terrain-filter-summary hidden></span></button>
      <button data-terrain-filter-open aria-label="Adjust terrain filter"></button>
    </li>
    <section data-terrain-filter-legend hidden>
      <div data-terrain-filter-key="match" data-legend-part>Terrain filter</div>
      <div data-terrain-filter-key="slope" data-legend-part hidden>Slope angle</div>
      <p data-terrain-filter-legend-summary></p>
    </section>
    <div id="map-terrain-filter-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="map-terrain-filter-template">
      <h2>Terrain filter</h2>
      <input type="radio" name="terrain-filter-mode" value="match" checked>
      <input type="radio" name="terrain-filter-mode" value="slope">
      <span data-terrain-filter-slope-line></span>
      <div data-terrain-filter-ramp data-mode="match">
        ${ramp.map((label, i) => `<button type="button" data-slope-class="${i}" aria-pressed="false">${label}</button>`).join('')}
      </div>
      <p data-terrain-filter-aspect-line></p>
      <svg>
        ${compass.map((c, i) => `<path data-octant="${i}" role="button" tabindex="0" aria-pressed="false" aria-label="${c}"></path>`).join('')}
        <g data-terrain-filter-all-aspects role="button" tabindex="0" aria-pressed="false"></g>
      </svg>
      <p data-terrain-filter-elevation-line></p>
      <svg data-terrain-filter-mountain role="img">
        <rect data-terrain-filter-band x="8" y="8" width="108" height="104"></rect>
        <line data-terrain-filter-bound="max" hidden></line>
        <line data-terrain-filter-bound="min" hidden></line>
      </svg>
      <select data-terrain-filter-min-elevation><option value="">Any</option></select>
      <select data-terrain-filter-max-elevation><option value="">Any</option></select>
    </template>
    <template id="map-terrain-filter-strings-template">
      <span data-string="mode-slope">Neigungsklassen</span>
      <span data-string="availability-zoom-in">Näher heranzoomen.</span>
      <span data-string="availability-out-of-coverage">Keine Daten hier.</span>
      <span data-string="aspects-all">Alle Expositionen</span>
      <span data-string="aspects-count">%(count)s Expositionen</span>
      <span data-string="slope-min">ab %(min)s°</span>
      <span data-string="elevation-above">über %(min)s m</span>
      <span data-string="elevation-line-above">Über %(min)s m</span>
      <span data-string="elevation-all">Alle Höhen</span>
      <span data-string="elevation-any">Egal</span>
      <span data-string="compass-0">N</span>
      <span data-string="compass-7">NW</span>
    </template>`;
}

/** @type {ReturnType<typeof stubBridge>} */
let bridge;
const sheetEl = () => document.getElementById('map-terrain-filter-sheet');
const q = (selector) => sheetEl().querySelector(selector);
const changeEvent = () => new Event('change', { bubbles: true });
const press = (el) => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
const lastFilter = () => bridge.setFilter.mock.calls.at(-1)[0];
const pressedTiles = () => [...sheetEl().querySelectorAll('[data-slope-class][aria-pressed="true"]')]
  .map((b) => Number(b.dataset.slopeClass));
const optionValues = (select) => [...select.options].map((o) => o.value);

beforeAll(async () => {
  buildFixture();
  bridge = stubBridge();
  vi.resetModules();
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/slope_classes_core.js');
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
  bridge.state.enabled = true;
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

  it('turns the filter on when it opens with the filter off', () => {
    bridge.state.enabled = false;
    window.pwaTerrainFilterSheet.open();
    expect(bridge.show).toHaveBeenCalledTimes(1);
    expect(bridge.isEnabled()).toBe(true);
  });

  it('leaves an enabled filter alone', () => {
    window.pwaTerrainFilterSheet.open();
    expect(bridge.show).not.toHaveBeenCalled();
  });

  it('seeds every control from the bridge', () => {
    bridge.state.filter = { aspects: [0, 7], minSlope: 35, maxSlope: 45, minElevation: 2400 };
    window.pwaTerrainFilterSheet.open();
    const pressed = [...sheetEl().querySelectorAll('[data-octant][aria-pressed="true"]')]
      .map((b) => b.dataset.octant);
    expect(pressed).toEqual(['0', '7']);
    expect(q('[data-terrain-filter-all-aspects]').getAttribute('aria-pressed')).toBe('false');
    expect(pressedTiles()).toEqual([1, 2]);
    expect(q('[data-terrain-filter-min-elevation]').value).toBe('2400');
    expect(q('[data-terrain-filter-max-elevation]').value).toBe('');
    expect(q('[data-terrain-filter-aspect-line]').textContent).toBe('N, NW');
    expect(q('[data-terrain-filter-slope-line]').textContent).toBe('35–45°');
    expect(q('[data-terrain-filter-elevation-line]').textContent).toBe('Über 2,400 m');
  });
});

describe('the aspect rose', () => {
  it('hands a wedge toggle back as a whole filter', () => {
    window.pwaTerrainFilterSheet.open();
    press(q('[data-octant="4"]'));
    expect(bridge.setFilter).toHaveBeenCalledTimes(1);
    expect(lastFilter()).toMatchObject({ aspects: [0, 1, 2, 3, 5, 6, 7], minSlope: 30, maxSlope: null });
    expect(q('[data-octant="4"]').getAttribute('aria-pressed')).toBe('false');
  });

  it('answers Enter and Space on a wedge', () => {
    window.pwaTerrainFilterSheet.open();
    q('[data-octant="2"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(lastFilter().aspects).not.toContain(2);
    q('[data-octant="2"]').dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(lastFilter().aspects).toContain(2);
  });

  it('turns every aspect off from "All" when all are on, and every one on otherwise', () => {
    window.pwaTerrainFilterSheet.open();
    expect(q('[data-terrain-filter-all-aspects]').getAttribute('aria-pressed')).toBe('true');
    press(q('[data-terrain-filter-all-aspects]'));
    expect(lastFilter().aspects).toEqual([]);
    expect(q('[data-terrain-filter-aspect-line]').textContent).toBe('No aspects');
    press(q('[data-terrain-filter-all-aspects]'));
    expect(lastFilter().aspects).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(q('[data-terrain-filter-aspect-line]').textContent).toBe('Alle Expositionen');
  });
});

describe('the slope ramp', () => {
  it('collapses to a tapped interior class, then extends and trims', () => {
    window.pwaTerrainFilterSheet.open();
    expect(pressedTiles()).toEqual([0, 1, 2, 3, 4]);
    press(q('[data-slope-class="2"]'));
    expect(lastFilter()).toMatchObject({ minSlope: 40, maxSlope: 45 });
    expect(pressedTiles()).toEqual([2]);
    press(q('[data-slope-class="4"]'));
    expect(lastFilter()).toMatchObject({ minSlope: 40, maxSlope: null });
    expect(pressedTiles()).toEqual([2, 3, 4]);
    press(q('[data-slope-class="2"]'));
    expect(lastFilter()).toMatchObject({ minSlope: 45, maxSlope: null });
    expect(q('[data-terrain-filter-slope-line]').textContent).toBe('ab 45°');
  });

  it('carries the mode for the CSS to colour by', () => {
    bridge.state.filter = { mode: 'slope', minSlope: 30 };
    window.pwaTerrainFilterSheet.open();
    expect(q('[data-terrain-filter-ramp]').dataset.mode).toBe('slope');
  });
});

describe('the elevation lists and mountain', () => {
  it('lists Any then the scale, each cut at the other bound', () => {
    bridge.state.filter = { minSlope: 30, minElevation: 4200 };
    window.pwaTerrainFilterSheet.open();
    const below = q('[data-terrain-filter-max-elevation]');
    expect(optionValues(below)).toEqual(['', '4500', '4400', '4300']);
    expect(below.options[0].textContent).toBe('Egal');
    expect(below.options[1].textContent).toBe('4,500 m');
  });

  it('commits a pick, and null for Any', () => {
    window.pwaTerrainFilterSheet.open();
    const above = q('[data-terrain-filter-min-elevation]');
    above.value = '2500';
    above.dispatchEvent(changeEvent());
    expect(lastFilter()).toMatchObject({ minElevation: 2500, maxElevation: null });
    const again = q('[data-terrain-filter-min-elevation]');
    again.value = '';
    again.dispatchEvent(changeEvent());
    expect(lastFilter()).toMatchObject({ minElevation: null });
  });

  it('draws the band and a dashed line only for a set bound', () => {
    bridge.state.filter = { minSlope: 30, minElevation: 1700 };
    window.pwaTerrainFilterSheet.open();
    const band = q('[data-terrain-filter-band]');
    expect(Number(band.getAttribute('y'))).toBeCloseTo(8, 2);
    expect(Number(band.getAttribute('height'))).toBeCloseTo(0.8 * 104, 2);
    expect(q('[data-terrain-filter-bound="max"]').hasAttribute('hidden')).toBe(true);
    expect(q('[data-terrain-filter-bound="min"]').hasAttribute('hidden')).toBe(false);
    expect(q('[data-terrain-filter-mountain]').getAttribute('aria-label')).toBe('Über 1,700 m');
  });

  it('names the open band "All elevations"', () => {
    window.pwaTerrainFilterSheet.open();
    expect(q('[data-terrain-filter-elevation-line]').textContent).toBe('Alle Höhen');
  });
});

describe('the mode (SNOW-1073)', () => {
  const radio = (value) => q(`input[name="terrain-filter-mode"][value="${value}"]`);
  const shown = (root) => [...root.querySelectorAll('[data-terrain-filter-key]')]
    .filter((el) => !el.hidden)
    .map((el) => el.dataset.terrainFilterKey);

  it('seeds the mode tiles from the bridge', () => {
    bridge.state.filter = { mode: 'slope', minSlope: 30 };
    window.pwaTerrainFilterSheet.open();
    expect(radio('slope').checked).toBe(true);
    expect(radio('match').checked).toBe(false);
  });

  it('hands the chosen mode back with the rest of the filter', () => {
    bridge.state.filter = { minSlope: 35, maxSlope: 45 };
    window.pwaTerrainFilterSheet.open();
    radio('slope').checked = true;
    radio('slope').dispatchEvent(changeEvent());
    expect(lastFilter()).toMatchObject({ mode: 'slope', minSlope: 35, maxSlope: 45 });
    radio('match').checked = true;
    radio('match').dispatchEvent(changeEvent());
    expect(lastFilter()).toMatchObject({ mode: 'match' });
  });

  it('names the mode in the legend and shows its key there', () => {
    bridge.state.filter = { mode: 'slope', minSlope: 30 };
    bridge.show();
    const legendSummary = document.querySelector('[data-terrain-filter-legend-summary]');
    expect(legendSummary.textContent).toBe('Neigungsklassen · Alle Expositionen · ab 30°');
    expect(shown(document.querySelector('[data-terrain-filter-legend]'))).toEqual(['slope']);
  });
});

describe('the menu row and legend', () => {
  const summary = () => document.querySelector('[data-terrain-filter-summary]');

  it('shows the short summary while the filter draws', () => {
    bridge.state.filter = { aspects: [0, 7], minSlope: 35, minElevation: 2400 };
    bridge.show();
    expect(summary().hidden).toBe(false);
    expect(summary().textContent).toBe('2 Expositionen · ab 35°');
    const legendSummary = document.querySelector('[data-terrain-filter-legend-summary]');
    expect(legendSummary.textContent).toBe('N, NW · ab 35° · über 2,400 m');
    expect(document.querySelector('[data-terrain-filter-legend]').hidden).toBe(false);
  });

  it('says why nothing is drawn instead', () => {
    bridge.state.availability = 'zoom-in';
    bridge.show();
    expect(summary().textContent).toBe('Näher heranzoomen.');
    bridge.state.availability = 'out-of-coverage';
    bridge.show();
    expect(summary().textContent).toBe('Keine Daten hier.');
  });

  it('hides the line and the legend while the filter is off', () => {
    bridge.hide();
    expect(summary().hidden).toBe(true);
    expect(summary().textContent).toBe('');
    expect(document.querySelector('[data-terrain-filter-legend-summary]').textContent).toBe('');
    expect(document.querySelector('[data-terrain-filter-legend]').hidden).toBe(true);
  });
});
