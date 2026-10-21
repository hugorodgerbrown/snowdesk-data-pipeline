/*
 * tests/js/test_map_region_panel.js — search's region label, the pin
 * roundel and the pinned-regions list (static/js/map_region_panel.js;
 * SNOW-814, and SNOW-1173, which retired the chip's dropdown panel and
 * folded the chip into search).
 *
 * What is left of the module, all of it jsdom's to hold:
 *
 *   search's #search-region-label mirrors the (undrawn) chip — the
 *   selection's name and the day's swatch, or the search prompt;
 *   the pin roundel in the ribbon header derives its state from the loaded
 *   list, and toggling it writes through favourites:region_toggle;
 *   search reads the same loaded list (window.pwaPinnedRegions), so the
 *   pinned regions it lists are the ones the roundel agrees with — and a
 *   list that failed to load is retried the next time it is asked for.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const PIN_LIST_URL = '/favourites/partials/regions/';
const TOGGLE_URL_TEMPLATE = '/favourites/partials/region/XX-0000/toggle/';

document.body.innerHTML = `
  <div id="season-ribbon">
    <button id="region-readout" aria-controls="region-sheet">
      <span class="region-readout-swatch"></span>
      <span class="region-readout-leaf"></span>
    </button>
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
  <button id="search-region-label" data-prompt="Find a region or resort">
    <span data-search-region-swatch hidden></span>
    <span data-search-region-name>Find a region or resort</span>
  </button>
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
// `listFails` makes the next load answer an error: htmx still resolves its
// promise then, and reports the outcome on htmx:afterRequest, as the real
// one does.
let listFails = false;
globalThis.htmx = {
  ajax: vi.fn((verb, url, opts) => {
    const successful = !listFails;
    if (successful) opts.target.innerHTML = rowsHtml();
    opts.target.dispatchEvent(new CustomEvent('htmx:afterRequest', { detail: { successful } }));
    return Promise.resolve();
  }),
};

globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
document.cookie = 'csrftoken=tok';
window.MapSheet = { toast: vi.fn() };

// The boot load fails: the first describe below is the retry it needs.
listFails = true;
await import('../../static/js/map_region_panel.js');

const chip = /** @type {HTMLButtonElement} */ (document.getElementById('region-readout'));
const label = /** @type {HTMLElement} */ (document.getElementById('search-region-label'));
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
  listFails = false;
  globalThis.htmx.ajax.mockClear();
});

describe('a list that failed to load at boot (SNOW-1173)', () => {
  it('is fetched again when search next asks for it, and then not again', async () => {
    await settle();
    // Boot asked once and got an error: no rows, so no pinned regions.
    expect(window.pwaPinnedRegions.ids()).toEqual([]);
    globalThis.htmx.ajax.mockClear();

    // beforeEach has cleared `listFails`: the connection is back.
    await window.pwaPinnedRegions.ready();
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(window.pwaPinnedRegions.ids()).toEqual(['CH-4115', 'CH-4222']);

    // A list that has loaded is not fetched on every ask.
    await window.pwaPinnedRegions.ready();
    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
  });
});

describe("search's region label (SNOW-1173)", () => {
  /** Write the chip the way map_season_ribbon.js does. */
  function writeChip(name, background) {
    chip.classList.toggle('has-region', !!name);
    chip.querySelector('.region-readout-leaf').textContent = name || '';
    /** @type {HTMLElement} */ (chip.querySelector('.region-readout-swatch')).style.background =
      background || '';
  }

  it("names the selection and carries the day's swatch", async () => {
    writeChip('Martigny-Verbier', 'yellow');
    await settle();
    const swatch = /** @type {HTMLElement} */ (label.querySelector('[data-search-region-swatch]'));
    expect(label.classList.contains('has-region')).toBe(true);
    expect(label.querySelector('[data-search-region-name]').textContent).toBe('Martigny-Verbier');
    expect(swatch.hidden).toBe(false);
    expect(swatch.style.background).toBe('yellow');
  });

  it('shows the search prompt with nothing selected', async () => {
    writeChip('Martigny-Verbier', 'yellow');
    await settle();
    writeChip(null);
    await settle();
    expect(label.classList.contains('has-region')).toBe(false);
    expect(label.querySelector('[data-search-region-name]').textContent).toBe('Find a region or resort');
    expect(/** @type {HTMLElement} */ (label.querySelector('[data-search-region-swatch]')).hidden).toBe(true);
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
