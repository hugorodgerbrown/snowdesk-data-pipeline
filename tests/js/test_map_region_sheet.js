/*
 * tests/js/test_map_region_sheet.js — the phone region sheet
 * (static/js/map_region_sheet.js, SNOW-1098).
 *
 * A tap opens it at peek and fetches its body; a selection that was not a
 * tap does not open it; while it is open it follows the selection and the
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
  </template>
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
 */
function select(regionId, via) {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: regionId, via },
  }));
}

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
  document.getElementById('map-download-control').removeAttribute('aria-disabled');
  fetchMock = vi.fn(() =>
    Promise.resolve({ ok: true, text: () => Promise.resolve(BODY) }),
  );
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
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

  it('does not open for a selection that was not a tap', async () => {
    const sheet = await boot();
    select('CH-4115');
    expect(sheet.hasAttribute('hidden')).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('does not open on a desktop', async () => {
    const sheet = await boot();
    vi.stubGlobal('matchMedia', () => ({ matches: true }));
    select('CH-4115', 'tap');
    expect(sheet.hasAttribute('hidden')).toBe(true);
  });

  it('says so when the region cannot be fetched', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')));
    const sheet = await boot();
    select('CH-4115', 'tap');
    await settle();
    expect(sheet.textContent).toContain('Region details are unavailable offline.');
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
});
