/*
 * tests/js/test_map_place_sheet.js — the phone place sheet
 * (static/js/map_place_sheet.js, SNOW-1100).
 *
 * map.js calls `pwaPlaceSheet.open()` for a resort or a saved pin; on a
 * phone the sheet opens at peek and fetches the body. On a desktop it opens
 * for a resort (SNOW-1161, which retired the resort popup) and declines a
 * saved pin, so map.js keeps that pin's popup. A newer open drops an older answer, a
 * failed fetch falls back to what the map already holds, the date follows
 * the scrubber, and the saved pin's Share and Delete act from inside the
 * sheet. While the timeline plays (SNOW-1239) the sheet stays open under a
 * "Playback in progress" band, fetches nothing, and loads once on stop.
 * SNOW-1313: a pin's sheet asks with the map's day (the offline key), says
 * when that day is not downloaded, and its "Full forecast" link opens the
 * map's weather sheet for the map's day instead of navigating.
 *
 * The module wires itself at import, so the page is built and the modules
 * imported ONCE; each test starts from a closed sheet instead. jsdom's
 * `matchMedia` stub (tests/js/setup.js) answers `matches: false`, a phone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const FAVOURITE_BODY = `
  <div data-testid="place-sheet-body" data-place-kind="favourite">
    <div data-sheet-peek id="place-sheet-row" data-row-renameable>
      <h2 data-row-label>Lac des Vaux</h2>
      <button type="button" data-row-rename data-favourite-rename="abc">Rename</button>
      <button type="button" data-favourite-share data-favourite-share-text="Lac des Vaux - https://w3w.co/a.b.c">Share</button>
      <a href="/weather/Ab3dE/?date=2026-10-30" data-testid="place-sheet-forecast-link" data-forecast-short-id="Ab3dE">Full forecast</a>
      <form data-row-remove hx-post="/favourites/abc/delete/" hx-confirm="Delete this pin?">
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
        <button type="submit">Delete</button>
      </form>
    </div>
  </div>
`;

const RESORT_BODY = `
  <div data-testid="place-sheet-body" data-place-kind="resort">
    <div data-sheet-peek>
      <h2>Verbier</h2>
      <button type="button" data-resort-star data-favourited="false" aria-pressed="false"><svg fill="none"></svg></button>
    </div>
  </div>
`;

document.body.innerHTML = `
  <div id="map"
       data-resort-sheet-url="/partials/place-sheet/resort/__SLUG__/"
       data-favourite-sheet-url="/partials/place-sheet/favourite/00000000-0000-0000-0000-000000000000/"></div>
  <div id="place-sheet" data-overlay hidden tabindex="-1"
       data-detents="peek,half,full" data-detent-initial="peek">
    <button type="button" data-sheet-handle aria-label="Resize panel"></button>
  </div>
  <template id="sheet-playback-band-template"><div role="status"><p>Playback in progress</p></div></template>
  <template id="place-sheet-strings-template">
    <span data-string="loading">Loading place details…</span>
    <span data-string="failed">Place details are unavailable offline.</span>
    <span data-string="notDownloaded">This day isn't downloaded for offline use.</span>
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

/** @param {string} html */
const answer = (html) => Promise.resolve({ ok: true, text: () => Promise.resolve(html) });

let fetchMock;

beforeEach(() => {
  window.snowdeskMapState = { isPlaying: false };
  window.pwaPlaceSheet.close();
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: null } }));
  fetchMock = vi.fn(() => answer(FAVOURITE_BODY));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('opening', () => {
  it('opens a resort at half on a phone and fetches its body', async () => {
    fetchMock.mockImplementation(() => answer(RESORT_BODY));
    expect(window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier', name: 'Verbier' })).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('half');
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/resort/verbier/');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'HX-Request': 'true' });
    await settle();
    expect(sheet.textContent).toContain('Verbier');
  });

  it('fetches a saved pin by its uuid', () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc-123' });
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/favourite/abc-123/');
  });

  it('opens a saved pin at peek, after a resort opened at half', () => {
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    window.pwaPlaceSheet.close();
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc-123' });
    expect(sheet.getAttribute('data-detent')).toBe('peek');
  });

  it('opens a resort on a desktop too, as the inset panel (SNOW-1161)', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' })).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/resort/verbier/');
  });

  it('declines a saved pin on a desktop, so map.js keeps its popup', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc-123' })).toBe(false);
    expect(sheet.hasAttribute('hidden')).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('carries the scrubbed day', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-04-27' } }));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/resort/verbier/?d=2026-04-27');
  });

  it('drops an older answer that lands after a newer tap', async () => {
    let releaseFirst;
    fetchMock
      .mockImplementationOnce(() => new Promise((resolve) => { releaseFirst = resolve; }))
      .mockImplementationOnce(() => answer(RESORT_BODY));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    releaseFirst({ ok: true, text: () => Promise.resolve(FAVOURITE_BODY) });
    await settle();
    expect(sheet.textContent).toContain('Verbier');
    expect(sheet.textContent).not.toContain('Lac des Vaux');
  });
});

describe('from one place to the next (Hugo, 27 Oct 2026)', () => {
  const ZERMATT = `
    <div data-testid="place-sheet-body" data-place-kind="resort">
      <div data-sheet-peek><h2 data-row-label>Zermatt</h2></div>
      <section data-testid="place-sheet-weather">Zermatt weather</section>
    </div>
  `;

  /** @param {Function} [hold] Receives the second answer's resolver. */
  async function openZermattThenTap(hold) {
    fetchMock
      .mockImplementationOnce(() => answer(ZERMATT))
      .mockImplementationOnce(() => new Promise((resolve) => { hold(resolve); }));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'zermatt', name: 'Zermatt' });
    await settle();
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'saas-fee', name: 'Saas-Fee' });
  }

  it('keeps the open body up, dimmed, under the new name until the answer lands', async () => {
    let release;
    await openZermattThenTap((r) => { release = r; });
    // Not emptied to a loading line: that is what made the sheet bob.
    expect(sheet.textContent).toContain('Zermatt weather');
    expect(sheet.textContent).not.toContain('Loading place details');
    expect(sheet.querySelector('h2[data-row-label]').textContent).toBe('Saas-Fee');
    expect(sheet.getAttribute('aria-busy')).toBe('true');
    expect(sheet.querySelector('[data-testid="place-sheet-weather"]').hasAttribute('data-sheet-stale')).toBe(true);
    expect(sheet.querySelector('h2[data-row-label]').hasAttribute('data-sheet-stale')).toBe(false);
    expect(sheet.querySelector('[data-sheet-handle]').hasAttribute('data-sheet-stale')).toBe(false);

    release({ ok: true, text: () => Promise.resolve(RESORT_BODY) });
    await settle();
    expect(sheet.textContent).toContain('Verbier');
    expect(sheet.hasAttribute('aria-busy')).toBe(false);
    expect(sheet.hasAttribute('data-sheet-pending')).toBe(false);
    expect(sheet.querySelector('[data-sheet-stale]')).toBeNull();
  });

  it("does not save the last resort from its star while the next one loads", async () => {
    const ZERMATT_STAR = `
      <div data-testid="place-sheet-body" data-place-kind="resort">
        <div data-sheet-peek><h2 data-row-label>Zermatt</h2>
          <button type="button" data-resort-star data-resort-slug="zermatt" data-favourited="false" aria-pressed="false"><svg fill="none"></svg></button>
        </div>
      </div>
    `;
    fetchMock
      .mockImplementationOnce(() => answer(ZERMATT_STAR))
      .mockImplementationOnce(() => new Promise(() => {}));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'zermatt', name: 'Zermatt' });
    await settle();
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'saas-fee', name: 'Saas-Fee' });
    const star = sheet.querySelector('[data-resort-star]');
    star.click();
    expect(star.getAttribute('aria-pressed')).toBe('false');
  });

  it('stays at the detent it rests at', async () => {
    await openZermattThenTap(() => {});
    expect(sheet.getAttribute('data-detent')).toBe('half');
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });

  it('takes the stale body down when the next place cannot be fetched', async () => {
    let release;
    await openZermattThenTap((r) => { release = r; });
    release({ ok: false });
    await settle();
    expect(sheet.textContent).not.toContain('Zermatt weather');
    expect(sheet.textContent).toContain('unavailable offline');
    expect(sheet.hasAttribute('data-sheet-pending')).toBe(false);
  });
});

describe('offline', () => {
  it('falls back to a saved pin\'s own name', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc', name: 'Lac des Vaux' });
    await settle();
    expect(sheet.textContent).toContain('Lac des Vaux');
  });

  it('asks for a saved pin on the map\'s current day, the key it was kept under (SNOW-1313)', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-10-30' } }));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc-123' });
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/favourite/abc-123/?d=2026-10-30');
  });

  it('says a day the device does not hold is not downloaded, under the pin\'s name (SNOW-1313)', async () => {
    fetchMock.mockImplementation(() => Promise.resolve({
      ok: false,
      status: 504,
      headers: new Headers({ 'X-SW-Offline-Miss': '1' }),
      text: () => Promise.resolve(''),
    }));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc', name: 'Lac des Vaux' });
    await settle();
    expect(sheet.textContent).toContain('Lac des Vaux');
    expect(sheet.querySelector('[data-testid="place-sheet-not-downloaded"]').textContent)
      .toBe("This day isn't downloaded for offline use.");
  });

  it('says a resort is unavailable', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    expect(sheet.textContent).toContain('Place details are unavailable offline.');
  });
});

describe('the forecast link (SNOW-1313)', () => {
  afterEach(() => {
    delete window.pwaWeatherDetail;
  });

  it('opens the weather sheet on the map\'s current day and does not navigate', async () => {
    const open = vi.fn(() => Promise.resolve(true));
    window.pwaWeatherDetail = { open };
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-10-30' } }));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const link = /** @type {HTMLAnchorElement} */ (sheet.querySelector('[data-forecast-short-id]'));
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });

    link.dispatchEvent(click);

    expect(click.defaultPrevented).toBe(true);
    expect(open).toHaveBeenCalledWith('Ab3dE', '2026-10-30');
  });

  it('leaves the link to navigate without the weather sheet, or on a modified click', async () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const link = /** @type {HTMLAnchorElement} */ (sheet.querySelector('[data-forecast-short-id]'));
    // Whether the sheet took the click, read once it has run; then the
    // navigation jsdom cannot make is stopped.
    const taken = [];
    const record = (ev) => {
      taken.push(ev.defaultPrevented);
      ev.preventDefault();
    };
    document.addEventListener('click', record);
    link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

    const open = vi.fn();
    window.pwaWeatherDetail = { open };
    link.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, metaKey: true }));
    document.removeEventListener('click', record);

    expect(taken).toEqual([false, false]);
    expect(open).not.toHaveBeenCalled();
  });
});

describe('while open', () => {
  it('re-reads the body when the favourites change', async () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('turns the resort star at once and holds it until the save lands', async () => {
    vi.useFakeTimers();
    const canQueue = vi.spyOn(window.MapSheet, 'canQueueMutations').mockReturnValue(true);
    fetchMock.mockImplementation(() => answer(RESORT_BODY));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    const star = /** @type {HTMLButtonElement} */ (sheet.querySelector('[data-resort-star]'));
    star.click();
    expect(star.getAttribute('aria-pressed')).toBe('true');
    expect(star.querySelector('svg').getAttribute('fill')).toBe('currentColor');
    vi.runAllTimers();
    expect(star.disabled).toBe(true);
    canQueue.mockRestore();
    vi.useRealTimers();
  });

  it('leaves the star alone on a device that cannot queue the save', async () => {
    const canQueue = vi.spyOn(window.MapSheet, 'canQueueMutations').mockReturnValue(false);
    fetchMock.mockImplementation(() => answer(RESORT_BODY));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    const star = /** @type {HTMLButtonElement} */ (sheet.querySelector('[data-resort-star]'));
    star.click();
    expect(star.getAttribute('aria-pressed')).toBe('false');
    expect(star.querySelector('svg').getAttribute('fill')).toBe('none');
    canQueue.mockRestore();
  });

  it('shares the pin\'s composed text', async () => {
    const shareOrCopy = vi.fn(() => Promise.resolve('shared'));
    window.pwaShare = { shareOrCopy };
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    /** @type {HTMLElement} */ (sheet.querySelector('[data-favourite-share]')).click();
    expect(shareOrCopy).toHaveBeenCalledWith('Lac des Vaux - https://w3w.co/a.b.c', undefined, 'text');
    delete window.pwaShare;
  });

  it('re-reads the body once after a rename', async () => {
    window.pwaRowRenameCommit = {
      handleClick: (_ev, opts) => opts.onCommitted(),
    };
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const calls = fetchMock.mock.calls.length;
    /** @type {HTMLElement} */ (sheet.querySelector('[data-row-rename]')).click();
    expect(fetchMock.mock.calls.length).toBe(calls + 1);
    delete window.pwaRowRenameCommit;
  });

  it('deletes after the confirm, then announces and closes', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    const changed = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', changed, { once: true });
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const form = /** @type {HTMLFormElement} */ (sheet.querySelector('form[data-row-remove]'));
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    expect(fetchMock).toHaveBeenLastCalledWith('/favourites/abc/delete/', expect.objectContaining({
      method: 'POST',
      headers: { 'HX-Request': 'true', 'X-CSRFToken': 'tok' },
    }));
    await settle();
    expect(changed).toHaveBeenCalled();
    expect(window.pwaPlaceSheet.isOpen()).toBe(false);
  });

  it('posts with the session\'s cookie token over a kept body\'s stale one (SNOW-1313)', async () => {
    vi.stubGlobal('confirm', vi.fn(() => true));
    document.cookie = 'csrftoken=fresh';
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const form = /** @type {HTMLFormElement} */ (sheet.querySelector('form[data-row-remove]'));
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    expect(fetchMock).toHaveBeenLastCalledWith('/favourites/abc/delete/', expect.objectContaining({
      headers: { 'HX-Request': 'true', 'X-CSRFToken': 'fresh' },
    }));
    document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT';
    await settle();
  });

  it('keeps the pin when the confirm is declined', async () => {
    vi.stubGlobal('confirm', vi.fn(() => false));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    const calls = fetchMock.mock.calls.length;
    const form = /** @type {HTMLFormElement} */ (sheet.querySelector('form[data-row-remove]'));
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    expect(fetchMock.mock.calls.length).toBe(calls);
    expect(window.pwaPlaceSheet.isOpen()).toBe(true);
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
  const band = () => sheet.querySelector('[data-sheet-playback-band]');
  /** A fetch that never answers, and rejects as an abort when aborted. */
  const hang = () =>
    fetchMock.mockImplementation((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    }));

  it('fetches nothing for a hundred frames, then once for the last day', async () => {
    fetchMock.mockImplementation(() => answer(RESORT_BODY));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    fetchMock.mockClear();
    announceState(true);
    for (let i = 0; i < 100; i += 1) frame(`2026-02-${String((i % 28) + 1).padStart(2, '0')}`);
    frame('2026-03-15');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(band()?.textContent).toContain('Playback in progress');
    // The body's own peek row stays the peek.
    expect(band()?.hasAttribute('data-sheet-peek')).toBe(false);
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/resort/verbier/?d=2026-03-15');
    expect(band()).toBeNull();
  });

  it('reloads for a day picked by hand, and the stop that follows adds none', async () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    announceState(true);
    frame('2026-01-02');
    fetchMock.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-01-20', source: 'scrubber' },
    }));
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/favourite/abc/?d=2026-01-20');
  });

  it('opens on the band alone during playback, and loads when it stops', async () => {
    announceState(true);
    expect(window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' })).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(band()?.hasAttribute('data-sheet-peek')).toBe(true);
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await settle();
    expect(sheet.textContent).toContain('Lac des Vaux');
  });

  it('holds a favourites change until playback stops', async () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    await settle();
    announceState(true);
    fetchMock.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));
    expect(fetchMock).not.toHaveBeenCalled();
    announceState(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts the request a newer open supersedes, and on close', () => {
    hang();
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc' });
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    window.pwaPlaceSheet.close();
    expect(fetchMock.mock.calls[1][1].signal.aborted).toBe(true);
  });

  it('aborts on Escape, and a late answer does not paint', async () => {
    let resolveLate;
    fetchMock.mockImplementation(() => new Promise((resolve) => { resolveLate = resolve; }));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
    resolveLate({ ok: true, text: () => Promise.resolve(RESORT_BODY) });
    await settle();
    expect(sheet.textContent).not.toContain('Verbier');
  });

  it('shows no failure line for an aborted request', async () => {
    hang();
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    announceState(true);
    await settle();
    expect(sheet.textContent).not.toContain('unavailable offline');
    expect(band()).not.toBeNull();
  });
});
