/*
 * tests/js/test_map_place_sheet_location.js — the "Your location" kind of
 * the place sheet (static/js/map_place_sheet.js, SNOW-1395).
 *
 * map_geolocate.js opens `{kind: 'location', lat, lon, altitude}` when the
 * reader taps the locate-me dot. The sheet opens at HALF on every width,
 * asks for the fix on the map's day, degrades to the device's altitude and
 * a "needs a connection" line when the fetch fails or stalls, and its star
 * turns at once (favourites.js queues the save — see
 * test_favourites_location_star.js). Its own file because the
 * module wires itself once at import (see test_map_place_sheet.js).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const LOCATION_BODY = `
  <div data-testid="place-sheet-body" data-place-kind="location">
    <div data-sheet-peek>
      <h2 data-testid="location-title">2,410 m · NE · 30–35°</h2>
      <button type="button" data-location-star data-lat="46.090500" data-lon="7.273700" aria-pressed="false"><svg fill="none"></svg></button>
    </div>
  </div>
`;

document.body.innerHTML = `
  <div id="map"
       data-resort-sheet-url="/partials/place-sheet/resort/__SLUG__/"
       data-favourite-sheet-url="/partials/place-sheet/favourite/00000000-0000-0000-0000-000000000000/"
       data-location-sheet-url="/partials/place-sheet/location/"></div>
  <button id="favourite-add-btn" data-favourite-create-url="/favourites/create/"></button>
  <div id="place-sheet" data-overlay hidden tabindex="-1"
       data-detents="peek,half,full" data-detent-initial="peek">
    <button type="button" data-sheet-handle aria-label="Resize panel"></button>
  </div>
  <template id="sheet-playback-band-template"><div role="status"><p>Playback in progress</p></div></template>
  <template id="place-sheet-strings-template">
    <span data-string="location-title">Your location</span>
    <span data-string="location-offline">The bulletin and weather need a connection.</span>
    <span data-string="location-saved">Saved as a pin.</span>
  </template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/sheet_playback_core.js');
await import('../../static/js/map_place_sheet.js');

const sheet = /** @type {HTMLElement} */ (document.getElementById('place-sheet'));

/** Let the fetch chain settle. */
async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
}

const FIX = { kind: 'location', id: 'here', lat: 46.0905, lon: 7.2737, altitude: 2385.4, lngLat: [7.2737, 46.0905] };

let fetchMock;

beforeEach(() => {
  window.snowdeskMapState = { isPlaying: false };
  window.pwaPlaceSheet.close();
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: null } }));
  fetchMock = vi.fn(() => Promise.resolve({ ok: true, text: () => Promise.resolve(LOCATION_BODY) }));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaMutationQueue;
});

describe('the Your location sheet', () => {
  it('opens at half and asks for the fix', () => {
    expect(window.pwaPlaceSheet.open(FIX)).toBe(true);
    expect(sheet.getAttribute('data-detent')).toBe('half');
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/location/?lat=46.0905&lon=7.2737&alt=2385');
  });

  it('opens on a desktop too', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(window.pwaPlaceSheet.open(FIX)).toBe(true);
  });

  it('carries the map day and leaves out a missing altitude', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-04-28' } }));
    window.pwaPlaceSheet.open({ ...FIX, altitude: null });
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/location/?lat=46.0905&lon=7.2737&d=2026-04-28');
  });

  it("offline, shows the device's altitude and says the rest needs a connection", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')));
    window.pwaPlaceSheet.open(FIX);
    await settle();
    expect(sheet.textContent).toContain('2,385 m');
    expect(sheet.textContent).toContain('The bulletin and weather need a connection.');
  });

  it('offline with no altitude, names the sheet instead', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError('Failed to fetch')));
    window.pwaPlaceSheet.open({ ...FIX, altitude: null });
    await settle();
    expect(sheet.textContent).toContain('Your location');
  });

  it('the star turns at once and leaves the write to favourites.js', async () => {
    const enqueue = vi.fn(() => Promise.resolve());
    window.pwaMutationQueue = { enqueue };
    window.MapSheet.canQueueMutations = () => true;
    window.pwaPlaceSheet.open(FIX);
    await settle();
    const star = /** @type {HTMLButtonElement} */ (sheet.querySelector('[data-location-star]'));
    star.click();
    expect(star.getAttribute('aria-pressed')).toBe('true');
    expect(enqueue).not.toHaveBeenCalled();
    await new Promise((r) => setTimeout(r, 0));
    expect(star.disabled).toBe(true);
  });

  it('a device that cannot queue leaves the star off', async () => {
    window.pwaMutationQueue = { enqueue: vi.fn() };
    window.MapSheet.canQueueMutations = () => false;
    window.pwaPlaceSheet.open(FIX);
    await settle();
    const star = /** @type {HTMLButtonElement} */ (sheet.querySelector('[data-location-star]'));
    star.click();
    expect(star.getAttribute('aria-pressed')).toBe('false');
    expect(star.disabled).toBe(false);
  });

  it('a stalled read paints the offline fallback when its budget runs out', async () => {
    vi.useFakeTimers();
    try {
      fetchMock.mockImplementation(() => new Promise(() => {}));
      window.pwaPlaceSheet.open(FIX);
      expect(sheet.textContent).not.toContain('The bulletin and weather need a connection.');
      vi.advanceTimersByTime(5000);
      expect(sheet.textContent).toContain('2,385\u00a0m');
      expect(sheet.textContent).toContain('The bulletin and weather need a connection.');
    } finally {
      vi.useRealTimers();
    }
  });

  it('a late answer replaces the fallback', async () => {
    vi.useFakeTimers();
    try {
      /** @type {(v: unknown) => void} */
      let answer = () => {};
      fetchMock.mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
      window.pwaPlaceSheet.open(FIX);
      vi.advanceTimersByTime(5000);
      answer({ ok: true, text: () => Promise.resolve(LOCATION_BODY) });
      await vi.runAllTimersAsync();
      expect(sheet.querySelector('[data-testid="location-title"]')).not.toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it('a favourites change does not re-read the body', async () => {
    window.pwaPlaceSheet.open(FIX);
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
