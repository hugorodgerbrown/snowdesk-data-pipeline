/*
 * tests/js/test_map_region_panel.js — the region chip, its pin roundel and
 * the pinned-regions list (static/js/map_region_panel.js; SNOW-814, and
 * SNOW-1173, which retired the chip's dropdown panel).
 *
 * What is left of the module, all of it jsdom's to hold:
 *
 *   the chip opens the region sheet for the region it names, or closes it
 *   when it is open — the chip's own dropdown is gone, and the sheet is the
 *   surface on every width; with nothing selected it is disabled, because
 *   the pinned list it used to open onto is in search now;
 *   its aria-expanded follows the sheet, however the sheet was closed;
 *   the pin roundel in the ribbon header derives its state from the loaded
 *   list, and toggling it writes through favourites:region_toggle;
 *   search reads the same loaded list (window.pwaPinnedRegions), so the
 *   pinned regions it lists are the ones the roundel agrees with.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const PIN_LIST_URL = '/favourites/partials/regions/';
const TOGGLE_URL_TEMPLATE = '/favourites/partials/region/XX-0000/toggle/';

document.body.innerHTML = `
  <div id="season-ribbon">
    <button id="region-readout" aria-expanded="false" aria-controls="region-sheet"></button>
    <div id="region-pin-rows" data-region-pin-rows
         data-region-pin-list-url="${PIN_LIST_URL}"
         data-region-pin-toggle-url="${TOGGLE_URL_TEMPLATE}"
         hidden></div>
    <button id="map-region-pin-control" data-pin-state="no-region"
            aria-disabled="true" aria-pressed="false"></button>
    <template id="region-panel-strings-template">
      <span data-string="pin-region">Pin this region</span>
      <span data-string="unpin-region">Unpin this region</span>
      <span data-string="pin-failed">That region couldn't be pinned. Try again.</span>
    </template>
  </div>
  <div id="region-sheet" hidden></div>
`;

// What favourites:region_list would answer with. Mutable, because the
// module re-reads the list after every write and derives the roundel from
// what comes back — a fixed fixture would have the reload contradicting the
// toggle it was caused by, which is the one thing this surface must not do.
let pinnedIds = [
  ['CH-4115', 'Martigny-Verbier'],
  ['CH-4222', 'Zermatt'],
];

/** Render the rows the server would send for `pinnedIds`. */
function rowsHtml() {
  return '<ul>' + pinnedIds.map(([id, name], i) => (
    `<li id="region-pin-${i}">`
    + `<button data-row-label data-row-focus-region="${id}">${name}</button>`
    + '</li>'
  )).join('') + '</ul>';
}

// htmx.ajax fills whatever target it is handed, the way the real one does,
// so the module's own DOM reads run against real rows.
globalThis.htmx = {
  ajax: vi.fn((verb, url, opts) => {
    opts.target.innerHTML = rowsHtml();
    return Promise.resolve();
  }),
};

globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
document.cookie = 'csrftoken=tok';
window.MapSheet = { toast: vi.fn() };

// map_region_sheet.js's public face, standing in for the module: it opens
// and closes the sheet the way MapSheet does, by its `hidden` attribute.
const sheet = /** @type {HTMLElement} */ (document.getElementById('region-sheet'));
window.pwaRegionSheet = {
  isOpen: vi.fn(() => !sheet.hasAttribute('hidden')),
  reopen: vi.fn(() => {
    sheet.removeAttribute('hidden');
    return true;
  }),
  close: vi.fn(() => sheet.setAttribute('hidden', '')),
};

await import('../../static/js/map_region_panel.js');

const chip = /** @type {HTMLButtonElement} */ (document.getElementById('region-readout'));
const pinControl = /** @type {HTMLElement} */ (document.getElementById('map-region-pin-control'));

/** Let the module's fetch/htmx promise chains run to completion. */
async function settle() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** Announce a region selection the way map.js does. */
function selectRegion(regionId) {
  document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
    detail: { region_id: regionId, region_name: regionId },
  }));
}

beforeEach(() => {
  pinnedIds = [
    ['CH-4115', 'Martigny-Verbier'],
    ['CH-4222', 'Zermatt'],
  ];
  sheet.setAttribute('hidden', '');
  globalThis.htmx.ajax.mockClear();
  window.pwaRegionSheet.reopen.mockClear();
  window.pwaRegionSheet.close.mockClear();
});

describe('the chip (SNOW-1173)', () => {
  it('is disabled with nothing selected — there is nothing for it to open', () => {
    selectRegion(null);
    expect(chip.disabled).toBe(true);
  });

  it('opens the region sheet for the region it names', () => {
    selectRegion('CH-4115');
    expect(chip.disabled).toBe(false);
    chip.click();
    expect(window.pwaRegionSheet.reopen).toHaveBeenCalledTimes(1);
    expect(sheet.hasAttribute('hidden')).toBe(false);
  });

  it('closes the sheet when it is already open', () => {
    selectRegion('CH-4115');
    sheet.removeAttribute('hidden');
    chip.click();
    expect(window.pwaRegionSheet.close).toHaveBeenCalledTimes(1);
    expect(window.pwaRegionSheet.reopen).not.toHaveBeenCalled();
  });

  it('says whether the sheet is open, however it was opened or closed', async () => {
    sheet.removeAttribute('hidden');
    await settle();
    expect(chip.getAttribute('aria-expanded')).toBe('true');
    // The sheet's own ×, Escape or another overlay hides it directly.
    sheet.setAttribute('hidden', '');
    await settle();
    expect(chip.getAttribute('aria-expanded')).toBe('false');
  });
});

describe('the pin roundel', () => {
  /**
   * Select `regionId`, then hand back a fetch spy that has seen nothing yet.
   */
  async function armed(regionId, response) {
    await window.pwaPinnedRegions.ready();
    selectRegion(regionId);
    await settle();
    delete pinControl.dataset.pinBusy;
    const spy = vi.fn(() => response);
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  it('is inert with nothing selected — there is nothing to pin', async () => {
    selectRegion(null);
    await settle();

    expect(pinControl.dataset.pinState).toBe('no-region');
    expect(pinControl.getAttribute('aria-disabled')).toBe('true');
  });

  it('reads its state from the pinned list, not from the server', async () => {
    await window.pwaPinnedRegions.ready();
    selectRegion('CH-4222');
    await settle();
    expect(pinControl.dataset.pinState).toBe('pinned');
    expect(pinControl.getAttribute('aria-pressed')).toBe('true');
    expect(pinControl.getAttribute('aria-label')).toBe('Unpin this region');

    selectRegion('CH-9999');
    await settle();
    expect(pinControl.dataset.pinState).toBe('idle');
    expect(pinControl.getAttribute('aria-pressed')).toBe('false');
    expect(pinControl.getAttribute('aria-label')).toBe('Pin this region');
  });

  it('POSTs the toggle with the region substituted into the URL', async () => {
    const spy = await armed('CH-9999', Promise.resolve({
      ok: true, json: () => Promise.resolve({ pinned: true }),
    }));
    // The server would now hold the pin, so the reload the module triggers
    // sees it too — which is what keeps the roundel and the list agreeing.
    pinnedIds.push(['CH-9999', 'Somewhere']);

    pinControl.click();
    await settle();

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe('/favourites/partials/region/CH-9999/toggle/');
    expect(init.method).toBe('POST');
    expect(init.headers['HX-Request']).toBe('true');
    expect(init.headers['X-CSRFToken']).toBe('tok');
    expect(pinControl.dataset.pinState).toBe('pinned');
    expect(pinControl.getAttribute('aria-label')).toBe('Unpin this region');
    expect(window.pwaPinnedRegions.ids()).toContain('CH-9999');
    vi.unstubAllGlobals();
  });

  it('ignores a second press while the first is in flight', async () => {
    let release;
    const spy = await armed('CH-9999', new Promise((r) => { release = r; }));

    pinControl.click();
    pinControl.click();
    expect(spy).toHaveBeenCalledTimes(1);

    release({ ok: true, json: () => Promise.resolve({ pinned: true }) });
    await settle();
    vi.unstubAllGlobals();
  });

  it('falls back to what the list says when the write fails', async () => {
    window.MapSheet.toast.mockClear();
    await armed('CH-4222', Promise.resolve({ ok: false, status: 500 }));

    pinControl.click();
    await settle();

    // Still what the list says — a failed write must not leave a star
    // claiming a pin that was never made.
    expect(pinControl.dataset.pinState).toBe('pinned');
    expect(window.MapSheet.toast).toHaveBeenCalledWith(
      "That region couldn't be pinned. Try again.",
    );
    vi.unstubAllGlobals();
  });
});

describe('the list search reads', () => {
  it('publishes the loaded ids in list order, once the boot load settles', async () => {
    await window.pwaPinnedRegions.ready();
    expect(window.pwaPinnedRegions.ids()).toEqual(['CH-4115', 'CH-4222']);
  });

  it('is loaded into the hidden host in the ribbon, never drawn', () => {
    const host = document.getElementById('region-pin-rows');
    expect(host.hidden).toBe(true);
    expect(host.querySelectorAll('[data-row-focus-region]').length).toBeGreaterThan(0);
  });
});
