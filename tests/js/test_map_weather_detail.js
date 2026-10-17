/*
 * tests/js/test_map_weather_detail.js — the weather sheet a symbol tap opens
 * (static/js/map_weather_detail.js, SNOW-761; detents SNOW-1101).
 *
 * The sheet opens at once on a loading line and fills when the card
 * arrives. On a phone it measures its PEEK as it opens, so the loading line
 * must already be in it, marked `data-sheet-peek` — otherwise the sheet
 * opens at the fallback height and then contracts to the line.
 *
 * The module wires itself at import, so the page is built and the modules
 * imported ONCE; each test starts from a closed sheet instead.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

document.body.innerHTML = `
  <div id="map" data-weather-detail-url="/api/weather/__SHORTID__/detail/"></div>
  <div id="weather-sheet" data-overlay hidden tabindex="-1"
       data-detents="peek,half,full" data-detent-initial="peek">
    <button type="button" data-sheet-handle aria-label="Resize panel"></button>
  </div>
  <template id="weather-detail-strings-template">
    <span data-string="loading">Loading weather…</span>
    <span data-string="failed">That weather couldn't be loaded.</span>
  </template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/map_weather_detail.js');

const sheet = /** @type {HTMLElement} */ (document.getElementById('weather-sheet'));

/** Let the fetch chain settle. */
async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
}

let fetchMock;

beforeEach(() => {
  window.pwaWeatherDetail.close();
  fetchMock = vi.fn(() => new Promise(() => {}));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('opening', () => {
  it('paints the loading line as the peek before the sheet opens', () => {
    // What the sheet held at the moment it was unhidden — the moment it
    // measures its peek.
    let atOpen = null;
    const remove = sheet.removeAttribute.bind(sheet);
    const spy = vi.spyOn(sheet, 'removeAttribute').mockImplementation((name) => {
      if (name === 'hidden') atOpen = sheet.querySelector('[data-sheet-peek]')?.textContent || '';
      remove(name);
    });
    window.pwaWeatherDetail.open('abc', '2026-04-27');
    spy.mockRestore();
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(atOpen).toContain('Loading weather…');
    expect(fetchMock.mock.calls[0][0]).toBe('/api/weather/abc/detail/?date=2026-04-27');
  });

  it('marks the failure line as the peek too', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    window.pwaWeatherDetail.open('abc');
    await settle();
    const peek = sheet.querySelector('[data-sheet-peek]');
    expect(peek?.textContent).toContain("That weather couldn't be loaded.");
  });
});
