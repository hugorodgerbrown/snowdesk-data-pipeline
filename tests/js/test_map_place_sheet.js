/*
 * tests/js/test_map_place_sheet.js — the phone place sheet
 * (static/js/map_place_sheet.js, SNOW-1100).
 *
 * map.js calls `pwaPlaceSheet.open()` for a resort or a saved pin; on a
 * phone the sheet opens at peek and fetches the body, on a desktop it
 * declines so map.js keeps the popup. A newer open drops an older answer, a
 * failed fetch falls back to what the map already holds, the date follows
 * the scrubber, and the saved pin's Share and Delete act from inside the
 * sheet.
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
  <template id="place-sheet-strings-template">
    <span data-string="loading">Loading place details…</span>
    <span data-string="failed">Place details are unavailable offline.</span>
  </template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
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
  window.pwaPlaceSheet.close();
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: null } }));
  fetchMock = vi.fn(() => answer(FAVOURITE_BODY));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('opening', () => {
  it('opens at peek on a phone and fetches the resort body', async () => {
    fetchMock.mockImplementation(() => answer(RESORT_BODY));
    expect(window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier', name: 'Verbier' })).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('peek');
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/resort/verbier/');
    expect(fetchMock.mock.calls[0][1].headers).toEqual({ 'HX-Request': 'true' });
    await settle();
    expect(sheet.textContent).toContain('Verbier');
  });

  it('fetches a saved pin by its uuid', () => {
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc-123' });
    expect(fetchMock.mock.calls[0][0]).toBe('/partials/place-sheet/favourite/abc-123/');
  });

  it('declines on a desktop, so map.js keeps the popup', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' })).toBe(false);
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

describe('offline', () => {
  it('falls back to a saved pin\'s own name', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    window.pwaPlaceSheet.open({ kind: 'favourite', id: 'abc', name: 'Lac des Vaux' });
    await settle();
    expect(sheet.textContent).toContain('Lac des Vaux');
  });

  it('says a resort is unavailable', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    window.pwaPlaceSheet.open({ kind: 'resort', id: 'verbier' });
    await settle();
    expect(sheet.textContent).toContain('Place details are unavailable offline.');
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
