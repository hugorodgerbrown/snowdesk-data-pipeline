/*
 * tests/js/test_map_region_sheet.js — the phone region sheet
 * (static/js/map_region_sheet.js, SNOW-1098).
 *
 * A tap opens it at peek, painted from the page at once, and fetches its
 * body, which is then kept and reused for the same region and day; a
 * selection that was not a tap opens it too; while it is open it follows the selection and the
 * date, and a deselect closes it; a failed fetch says so; and the pin and
 * download proxies press, and mirror, the roundels they stand for.
 *
 * The module wires itself at import against the elements present then, and
 * its listeners live on `document` for the rest of the file, so the page is
 * built and the modules imported ONCE; each test starts from a deselected,
 * closed sheet and fresh roundels instead.
 * jsdom's `matchMedia` stub (tests/js/setup.js) answers `matches: false`,
 * which both this module and map_sheet.js read as a phone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const BODY = `
  <div data-sheet-peek>
    <h2>Martigny-Verbier</h2>
    <button type="button" data-region-sheet-proxy="map-region-pin-control" aria-label="Pin this region"></button>
    <button type="button" data-region-sheet-proxy="map-download-control" aria-label="Download this region"></button>
  </div>
  <a href="/CH-4115/">Open bulletin</a>
`;

document.body.innerHTML = `
  <div id="map" data-region-sheet-url="/partials/region-sheet/XX-0000/"></div>
  <button id="map-region-pin-control" aria-pressed="false" aria-label="Pin this region"></button>
  <button id="map-download-control" aria-label="Download this region"></button>
  <div id="region-sheet" data-overlay hidden tabindex="-1"
       data-detents="peek,half,full" data-detent-initial="peek">
    <button type="button" data-sheet-handle aria-label="Resize panel"></button>
  </div>
  <template id="region-sheet-strings-template">
    <span data-string="loading">Loading region details…</span>
    <span data-string="failed">Region details are unavailable offline.</span>
    <span data-string="level-considerable">Considerable</span>
  </template>
  <template id="region-sheet-peek-template"><div data-sheet-peek>
    <span class="danger-tile" data-region-sheet-slot="rating" hidden></span>
    <h2 data-region-sheet-slot="name"></h2>
    <svg data-region-sheet-slot="flag" class="search-result-flag" hidden><use href=""></use></svg>
    <span data-region-sheet-slot="code"></span>
    <span data-region-sheet-slot="meta"></span>
    <button type="button" data-region-sheet-proxy="map-region-pin-control" aria-label="Pin this region"></button>
    <button type="button" data-region-sheet-proxy="map-download-control" aria-label="Download this region"></button>
  </div></template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_detents_core.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/map_region_sheet.js');

const sheet = /** @type {HTMLElement} */ (document.getElementById('region-sheet'));

/** The sheet, closed and deselected, as each test expects to find it. */
async function boot() {
  return sheet;
}

/**
 * Announce a selection the way map.js does.
 *
 * @param {?string} regionId
 * @param {string} [via]
 * @param {object} [extra] More of the event's detail (region_name, …).
 */
function select(regionId, via, extra = {}) {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: regionId, via, ...extra },
  }));
}

// The module keeps fetched bodies for ten minutes. Each test starts an hour
// after the last, so none inherits another's kept bodies.
let now = Date.UTC(2026, 9, 19);

/** Let the fetch chain settle. */
async function settle() {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

let fetchMock;

beforeEach(() => {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: null },
  }));
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
    detail: { date: null },
  }));
  const pin = document.getElementById('map-region-pin-control');
  pin.setAttribute('aria-pressed', 'false');
  pin.setAttribute('aria-label', 'Pin this region');
  const download = document.getElementById('map-download-control');
  download.removeAttribute('aria-disabled');
  download.removeAttribute('data-download-state');
  download.removeAttribute('style');
  download.className = '';
  download.innerHTML = '';
  fetchMock = vi.fn(() =>
    Promise.resolve({ ok: true, text: () => Promise.resolve(BODY) }),
  );
  vi.stubGlobal('fetch', fetchMock);
  now += 60 * 60 * 1000;
  vi.spyOn(Date, 'now').mockImplementation(() => now);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete window.pwaRegionLevel;
});

describe('the peek painted from the page', () => {
  it('shows the region, its level and its area before the body arrives', async () => {
    window.pwaRegionLevel = { forRegion: () => 'considerable' };
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier', subregion_name: 'Lower Valais' });
    const slot = (name) => sheet.querySelector(`[data-region-sheet-slot="${name}"]`);
    expect(slot('name').textContent).toBe('Martigny-Verbier');
    expect(slot('code').textContent).toBe('CH-4115');
    expect(slot('meta').textContent).toBe('Considerable · Lower Valais');
    expect(slot('rating').hidden).toBe(false);
    expect(slot('rating').textContent).toBe('3');
    expect(slot('rating').getAttribute('data-level')).toBe('considerable');
    expect(slot('flag').hasAttribute('hidden')).toBe(false);
    expect(slot('flag').querySelector('use').getAttribute('href')).toBe('#flag-ch');
  });

  it('leaves the tile hidden when the map has no level for the day', async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    expect(sheet.querySelector('[data-region-sheet-slot="rating"]').hidden).toBe(true);
  });
});

describe('a body fetched once', () => {
  it('is reused for the same region and day, with no second request', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    select(null);
    select('CH-4115', 'tap');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sheet.querySelector('a[href="/CH-4115/"]')).not.toBeNull();
  });

  it('is asked for again once ten minutes have passed', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    select(null);
    now += 11 * 60 * 1000;
    select('CH-4115', 'tap');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('is kept per day', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-02-03' },
    }));
    await settle();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: null },
    }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('opening', () => {
  it('opens at peek on a tap and fetches the region', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('peek');
    expect(fetchMock).toHaveBeenCalledWith(
      '/partials/region-sheet/ch-4115/',
      { headers: { 'HX-Request': 'true' } },
    );
    await settle();
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });

  it('opens for a selection that was not a tap (search, pinned row, deep link)', async () => {
    const sheet = await boot();
    select('CH-4115');
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(fetchMock).toHaveBeenCalled();
  });

  it('does not open on a desktop', async () => {
    const sheet = await boot();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    select('CH-4115', 'tap');
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('says so when the region cannot be fetched, under the peek it keeps', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    const sheet = await boot();
    select('CH-4115', 'tap', { region_name: 'Martigny-Verbier' });
    await settle();
    expect(sheet.textContent).toContain('Region details are unavailable offline.');
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });
});

describe('reopening (2026-10-19)', () => {
  it('reopens for the region still selected once its sheet was closed', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    // The × closes it the way overlays.js's shared handler does: by
    // hiding it. The region stays selected.
    sheet.setAttribute('hidden', '');
    expect(window.pwaRegionSheet.reopen()).toBe(true);
    expect(sheet.hasAttribute('hidden')).toBe(false);
    expect(sheet.getAttribute('data-detent')).toBe('peek');
    expect(sheet.textContent).toContain('Martigny-Verbier');
  });

  it('does nothing while the sheet is open, with no selection, or on a desktop', async () => {
    const sheet = await boot();
    expect(window.pwaRegionSheet.reopen()).toBe(false);
    select('CH-4115', 'tap');
    expect(window.pwaRegionSheet.reopen()).toBe(false);
    sheet.setAttribute('hidden', '');
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    expect(window.pwaRegionSheet.reopen()).toBe(false);
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });
});

describe('while open', () => {
  it('follows another selection, tap or not, at the detent it rests at', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    sheet.querySelector('[data-sheet-handle]').click();
    expect(sheet.getAttribute('data-detent')).toBe('half');
    select('CH-4116');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/partials/region-sheet/ch-4116/',
      expect.anything(),
    );
    expect(sheet.getAttribute('data-detent')).toBe('half');
  });

  it('refetches for the day the map moves to', async () => {
    await boot();
    select('CH-4115', 'tap');
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: '2026-02-03' },
    }));
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/partials/region-sheet/ch-4115/?d=2026-02-03',
      expect.anything(),
    );
  });

  it('closes when the region is deselected', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    select(null);
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('marks the root while open, so the ribbon header steps out', async () => {
    await boot();
    select('CH-4115', 'tap');
    await settle();
    expect(document.documentElement.hasAttribute('data-region-sheet')).toBe(true);
    select(null);
    await settle();
    expect(document.documentElement.hasAttribute('data-region-sheet')).toBe(false);
  });

  it('drops an answer for a region the reader has moved on from', async () => {
    let resolveFirst;
    fetchMock
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() =>
        Promise.resolve({ ok: true, text: () => Promise.resolve('<p>second</p>') }));
    const sheet = await boot();
    select('CH-4115', 'tap');
    select('CH-4116');
    await settle();
    resolveFirst({ ok: true, text: () => Promise.resolve('<p>first</p>') });
    await settle();
    expect(sheet.textContent).toContain('second');
    expect(sheet.textContent).not.toContain('first');
  });
});

describe('the proxies', () => {
  it('mirror the roundel they stand for', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-region-pin-control');
    roundel.setAttribute('aria-pressed', 'true');
    roundel.setAttribute('aria-label', 'Unpin this region');
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-region-pin-control"]');
    expect(proxy.getAttribute('aria-pressed')).toBe('true');
    expect(proxy.getAttribute('aria-label')).toBe('Unpin this region');
  });

  it('take the roundel\'s look: its classes, state, fill and glyphs', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    roundel.className = 'map-glass-roundel map-download-control';
    roundel.setAttribute('data-download-state', 'busy');
    roundel.style.setProperty('--download-progress', '40%');
    roundel.innerHTML = '<svg class="map-download-control-icon--busy"></svg>';
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    expect(proxy.className).toBe('map-glass-roundel map-download-control');
    expect(proxy.getAttribute('data-download-state')).toBe('busy');
    expect(proxy.style.getPropertyValue('--download-progress')).toBe('40%');
    expect(proxy.querySelector('.map-download-control-icon--busy')).not.toBeNull();
    expect(proxy.getAttribute('data-region-sheet-proxy')).toBe('map-download-control');
  });

  it('press the roundel, and not when it is disabled', async () => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    const pressed = vi.fn();
    roundel.addEventListener('click', pressed);
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);

    roundel.setAttribute('aria-disabled', 'true');
    await settle();
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);
  });

  // SNOW-1130: on a phone the proxy is the only download button, and a tap
  // on a downloaded ('done') or half-refreshed ('partial') region refreshes
  // its bulletins and weather. The roundel announces both as actionable, so
  // the proxy must pass the press through.
  it.each(['done', 'partial'])('presses the roundel in the %s state', async (state) => {
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    const roundel = document.getElementById('map-download-control');
    const pressed = vi.fn();
    roundel.addEventListener('click', pressed);
    roundel.setAttribute('data-download-state', state);
    roundel.setAttribute('aria-disabled', 'false');
    await settle();
    const proxy = sheet.querySelector('[data-region-sheet-proxy="map-download-control"]');
    expect(proxy.getAttribute('aria-disabled')).toBe('false');
    proxy.click();
    expect(pressed).toHaveBeenCalledTimes(1);
  });
});
