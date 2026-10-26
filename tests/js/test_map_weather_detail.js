/*
 * tests/js/test_map_weather_detail.js — the weather sheet a symbol tap opens
 * (static/js/map_weather_detail.js, SNOW-761; detents SNOW-1101).
 *
 * The sheet opens at once on a loading line and fills when the card
 * arrives. On a phone it measures its PEEK as it opens, so the loading line
 * must already be in it, marked `data-sheet-peek` — otherwise the sheet
 * opens at the fallback height and then contracts to the line.
 *
 * While the timeline plays (SNOW-1239) the sheet stays open under a
 * "Playback in progress" band, asks for nothing, and loads once when
 * playback stops; a newer ask and every close abort the one in flight.
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
  <template id="sheet-playback-band-template"><div role="status"><p>Playback in progress</p></div></template>
  <template id="weather-detail-strings-template">
    <span data-string="loading">Loading weather…</span>
    <span data-string="failed">That weather couldn't be loaded.</span>
  </template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/sheet_playback_core.js');
await import('../../static/js/map_weather_detail.js');

const sheet = /** @type {HTMLElement} */ (document.getElementById('weather-sheet'));

/** Let the fetch chain settle. */
async function settle() {
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
}

let fetchMock;

beforeEach(() => {
  window.snowdeskMapState = { isPlaying: false };
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

// SNOW-1195: the calendar spares this sheet now, so a new day reloads it.
describe('a change of day', () => {
  /** Announce a committed day, as map_scrubber.js does. */
  const commit = (date) =>
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date } }));

  it('reloads the open sheet for the same location on the new day', async () => {
    fetchMock.mockImplementation((url) =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ html: `<p>${url}</p>` }) }),
    );
    window.pwaWeatherDetail.open('abc', '2026-04-27');
    await settle();
    commit('2026-04-28');
    await settle();
    expect(fetchMock.mock.calls.at(-1)[0]).toBe('/api/weather/abc/detail/?date=2026-04-28');
    expect(sheet.textContent).toContain('?date=2026-04-28');
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });

  it('fetches nothing while the sheet is closed', () => {
    commit('2026-04-28');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// SNOW-1239: the timeline plays five frames a second; the sheet must not
// fetch for each.
describe('during playback', () => {
  /** @param {boolean} playing */
  const announceState = (playing) => {
    window.snowdeskMapState.isPlaying = playing;
    document.dispatchEvent(new CustomEvent('snowdesk:timelapse-state', { detail: { playing } }));
  };
  const frame = (date) =>
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date, source: 'timelapse' },
    }));
  const scrub = (date) =>
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date, source: 'scrubber' },
    }));
  const band = () => sheet.querySelector('[data-sheet-playback-band]');
  const respond = () =>
    fetchMock.mockImplementation((url) =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ html: `<p data-sheet-peek>${url}</p>` }) }),
    );
  /** A fetch that never answers, and rejects as an abort when aborted. */
  const hang = () =>
    fetchMock.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));

  it('fetches nothing for a hundred frames, then once for the last day', async () => {
    respond();
    window.pwaWeatherDetail.open('abc', '2026-01-01');
    await settle();
    fetchMock.mockClear();
    announceState(true);
    for (let i = 1; i <= 100; i += 1) frame(`2026-02-${String((i % 28) + 1).padStart(2, '0')}`);
    frame('2026-03-15');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(band()?.textContent).toContain('Playback in progress');
    expect(sheet.hasAttribute('data-sheet-paused')).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/weather/abc/detail/?date=2026-03-15');
    expect(band()).toBeNull();
    expect(sheet.hasAttribute('data-sheet-paused')).toBe(false);
  });

  it('reloads for a day picked by hand, and the stop that follows adds none', async () => {
    respond();
    window.pwaWeatherDetail.open('abc', '2026-01-01');
    await settle();
    announceState(true);
    frame('2026-01-02');
    fetchMock.mockClear();
    // The scrub arrives first; the timelapse then stops itself.
    scrub('2026-01-20');
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/weather/abc/detail/?date=2026-01-20');
  });

  it('opens on the band alone during playback, and loads when it stops', async () => {
    respond();
    announceState(true);
    window.pwaWeatherDetail.open('abc', '2026-01-05');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(band()?.hasAttribute('data-sheet-peek')).toBe(true);
    frame('2026-01-06');
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/weather/abc/detail/?date=2026-01-06');
    await settle();
    expect(sheet.textContent).toContain('?date=2026-01-06');
  });

  it('aborts the request a second tap supersedes', () => {
    hang();
    window.pwaWeatherDetail.open('abc');
    window.pwaWeatherDetail.open('def');
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(false);
  });

  it('aborts on close, and a late answer does not paint', async () => {
    let resolveLate;
    fetchMock.mockImplementation(() => new Promise((resolve) => { resolveLate = resolve; }));
    window.pwaWeatherDetail.open('abc');
    window.pwaWeatherDetail.close();
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    resolveLate({ ok: true, json: () => Promise.resolve({ html: '<p>late</p>' }) });
    await settle();
    expect(sheet.textContent).not.toContain('late');
  });

  it.each([
    ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['overlay:dismissed', () => {
      sheet.setAttribute('hidden', '');
      document.dispatchEvent(new CustomEvent('overlay:dismissed', { detail: { overlay: sheet } }));
    }],
  ])('aborts on %s', (_name, dismiss) => {
    hang();
    window.pwaWeatherDetail.open('abc');
    dismiss();
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  });

  it('shows no failure line for an aborted request', async () => {
    hang();
    window.pwaWeatherDetail.open('abc');
    announceState(true);
    await settle();
    expect(sheet.textContent).not.toContain("couldn't be loaded");
    expect(band()).not.toBeNull();
  });
});
