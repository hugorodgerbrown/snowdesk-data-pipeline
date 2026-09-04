/*
 * tests/js/test_map_region_panel_pins.js — pinned regions in the region +
 * date panel (SNOW-814, static/js/map_region_panel.js).
 *
 * The chip at the map's top-left discloses the panel; this covers the
 * section that moved into it from the pins sheet. Four behaviours the move
 * introduced, all of them jsdom's to hold:
 *
 *   the chip is pressable with NOTHING selected, because the pinned list is
 *   what it opens onto — it was simply disabled before;
 *   the pin roundel in the ribbon header derives its state from that list,
 *   and toggling it writes through favourites:region_toggle. The pill this
 *   replaced was covered by tests/js/test_favourites_region_pin.js, which
 *   went with it (SNOW-814): the control is no longer a favourites-sheet
 *   concern, and its state is no longer server-rendered;
 *   pressing a row SELECTS its region (through the hash, map.js's own deep
 *   link) as well as framing it, so the chip above cannot end up naming a
 *   different region from the one on screen;
 *   the row for the region already on screen is marked rather than removed;
 *   a pin toggled anywhere makes the list stale.
 *
 * The panel's older behaviour (the summary fetch, the render cache key) is
 * still uncovered — see the module's own header.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_removed.js';

const PIN_LIST_URL = '/favourites/partials/regions/';
const TOGGLE_URL_TEMPLATE = '/favourites/partials/region/XX-0000/toggle/';

document.body.innerHTML = `
  <div id="map"
       data-region-summary-url="/api/region/XX-0000/summary/"
       data-resorts-by-region-url="/api/resorts-by-region/"></div>
  <div id="season-ribbon">
    <button id="region-readout" aria-expanded="false" aria-controls="region-panel"></button>
    <button id="map-region-pin-control" data-pin-state="no-region"
            aria-disabled="true" aria-pressed="false"></button>
    <div id="region-panel"
         data-region-pin-list-url="${PIN_LIST_URL}"
         data-region-pin-toggle-url="${TOGGLE_URL_TEMPLATE}"
         hidden></div>
    <template id="region-panel-strings-template">
      <span data-string="resorts">Resorts in this region</span>
      <span data-string="unavailable">Region details are unavailable offline.</span>
      <span data-string="pinned">Pinned regions</span>
      <span data-string="pinned-failed">Your pinned regions couldn't be loaded.</span>
      <span data-string="pin-region">Pin this region</span>
      <span data-string="unpin-region">Unpin this region</span>
      <span data-string="pin-failed">That region couldn't be pinned. Try again.</span>
    </template>
  </div>
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

// The summary/resorts fetches the panel makes are not this suite's subject;
// the toggle's is stubbed per-test.
globalThis.fetch = vi.fn(() => Promise.resolve({ ok: false }));
document.cookie = 'csrftoken=tok';
window.MapSheet = { toast: vi.fn() };

const focusRegion = vi.fn();
window.pwaMapFocus = { region: focusRegion };

await import('../../static/js/map_region_panel.js');

const chip = document.getElementById('region-readout');
const panel = document.getElementById('region-panel');
const pinControl = document.getElementById('map-region-pin-control');

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

/** Open the panel from its collapsed state and wait for the list. */
async function openPanel() {
  if (panel.hidden) chip.click();
  await settle();
}

beforeEach(() => {
  window.location.hash = '';
  pinnedIds = [
    ['CH-4115', 'Martigny-Verbier'],
    ['CH-4222', 'Zermatt'],
  ];
  focusRegion.mockClear();
  globalThis.htmx.ajax.mockClear();
});

describe('the chip with nothing selected', () => {
  it('is pressable, because the pinned list is what it opens onto', async () => {
    selectRegion(null);
    expect(chip.disabled).toBe(false);

    await openPanel();

    expect(panel.hidden).toBe(false);
    expect(panel.textContent).toContain('Pinned regions');
    expect(panel.querySelectorAll('[data-row-focus-region]')).toHaveLength(2);
  });

  it('loads the list once, not on every open', async () => {
    await openPanel();
    const calls = globalThis.htmx.ajax.mock.calls.length;
    chip.click();
    await openPanel();

    expect(globalThis.htmx.ajax.mock.calls.length).toBe(calls);
  });
});

describe('pressing a pinned region', () => {
  it('selects it through the hash and frames it', async () => {
    await openPanel();
    panel.querySelector('[data-row-focus-region="CH-4222"]').click();

    expect(window.location.hash).toBe('#CH-4222');
    expect(focusRegion).toHaveBeenCalledWith('CH-4222');
  });

  it('leaves the panel open, unlike the sheets', async () => {
    await openPanel();
    panel.querySelector('[data-row-focus-region="CH-4115"]').click();

    expect(panel.hidden).toBe(false);
  });

  it('still frames the region already selected, without moving the hash', async () => {
    selectRegion('CH-4222');
    await openPanel();
    window.location.hash = '#CH-4222';
    panel.querySelector('[data-row-focus-region="CH-4222"]').click();

    expect(focusRegion).toHaveBeenCalledWith('CH-4222');
    expect(window.location.hash).toBe('#CH-4222');
  });
});

describe('the row for the region on screen', () => {
  it('is marked rather than hidden, so the list does not reorder', async () => {
    selectRegion('CH-4222');
    await openPanel();

    const rows = panel.querySelectorAll('li');
    expect(rows).toHaveLength(2);
    expect(rows[1].classList.contains('is-current')).toBe(true);
    expect(rows[0].classList.contains('is-current')).toBe(false);
    expect(
      rows[1].querySelector('[data-row-label]').getAttribute('aria-current'),
    ).toBe('true');
  });

  it('moves with the selection', async () => {
    selectRegion('CH-4222');
    await openPanel();
    selectRegion('CH-4115');
    await settle();

    const rows = panel.querySelectorAll('li');
    expect(rows[0].classList.contains('is-current')).toBe(true);
    expect(rows[1].classList.contains('is-current')).toBe(false);
  });
});

describe('a pin toggled elsewhere', () => {
  it('reloads the list while the panel is open', async () => {
    await openPanel();
    globalThis.htmx.ajax.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:region-pin-changed'));
    await settle();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
  });
});

describe('the pin roundel', () => {
  /**
   * Bring the panel up with `regionId` selected, then hand back a fetch spy
   * that has seen nothing yet.
   *
   * The stub goes in AFTER the setup on purpose: opening the panel makes the
   * summary and resorts fetches, and a spy installed before them would put
   * those calls in front of the toggle's in `mock.calls`.
   */
  async function armed(regionId, response) {
    await openPanel();
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
    await openPanel();
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
