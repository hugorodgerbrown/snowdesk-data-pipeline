/*
 * tests/js/test_saved_panel.js — Saved, the one panel of the four sheets,
 * on every screen (static/js/saved_panel.js, SNOW-1481).
 *
 * The module against the star and the four sheets, with each sheet's
 * controller stubbed — the sheets' own behaviour is tested beside each one,
 * and the tab bar's use of this module in tests/js/test_app_tabbar.js.
 * Pinned here:
 *   - the star toggles Saved, and `aria-expanded` follows whichever of the
 *     four sheets is open, however it opened or closed;
 *   - Saved opens on the tab last chosen, Places at first; signed out, on
 *     Places always;
 *   - a Saved tab (`saved-tab` radios) and `data-saved-go` close one sheet
 *     as they open its sibling, with no entrance mark, at the same detent
 *     on a phone (SNOW-1486); a first open rises;
 *   - the report sheet holding the binoculars' own form is not Saved.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <button id="favourite-add-btn" data-favourites-eligible="true" aria-expanded="false"></button>
  <div id="favourite-sheet" hidden></div>
  <div id="route-sheet" hidden></div>
  <div id="trips-sheet" hidden></div>
  <div id="report-sheet" hidden></div>`;

const $ = (id) => document.getElementById(id);
const star = () => $('favourite-add-btn');
const tick = () => new Promise((r) => setTimeout(r, 0));

let added = [];

/** A sheet controller that shows and hides its element. */
function panel(id) {
  const el = $(id);
  return {
    open: vi.fn(() => el.removeAttribute('hidden')),
    close: vi.fn(() => {
      el.setAttribute('hidden', '');
      el.replaceChildren();
    }),
    isOpen: () => !el.hasAttribute('hidden'),
  };
}

/**
 * Load a fresh copy of the module.
 *
 * @param {{signedIn?: boolean}} opts
 */
async function load({ signedIn = true } = {}) {
  star().setAttribute('data-favourites-eligible', String(signedIn));
  window.pwaFavouritesSheet = panel('favourite-sheet');
  window.pwaRoutesSheet = panel('route-sheet');
  window.pwaReportSheet = panel('report-sheet');
  if (signedIn) window.pwaTripsSheet = panel('trips-sheet');
  vi.resetModules();
  await import('../../static/js/saved_panel.js');
}

/** Put a tab row in `sheetId` and choose `value`. */
function choose(sheetId, value) {
  const input = document.createElement('input');
  input.type = 'radio';
  input.name = 'saved-tab';
  input.value = value;
  $(sheetId).append(input);
  input.checked = true;
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  window.sessionStorage.clear();
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  for (const key of [
    'pwaFavouritesSheet', 'pwaRoutesSheet', 'pwaTripsSheet', 'pwaReportSheet',
    'pwaSavedPanel',
  ]) delete window[key];
  vi.restoreAllMocks();
});

describe('the star', () => {
  it('opens Saved on Places and is expanded', async () => {
    await load();
    star().click();
    expect($('favourite-sheet').hidden).toBe(false);
    await tick();
    expect(star().getAttribute('aria-expanded')).toBe('true');
    expect(window.pwaSavedPanel.isOpen()).toBe(true);
  });

  it('closes Saved on a second tap', async () => {
    await load();
    star().click();
    star().click();
    expect($('favourite-sheet').hidden).toBe(true);
    await tick();
    expect(star().getAttribute('aria-expanded')).toBe('false');
  });

  it('follows a sheet closed from elsewhere', async () => {
    await load();
    star().click();
    await tick();
    window.pwaFavouritesSheet.close();
    await tick();
    expect(star().getAttribute('aria-expanded')).toBe('false');
  });

  it('closes whichever Saved tab is showing', async () => {
    await load();
    star().click();
    choose('favourite-sheet', 'routes');
    await tick();
    star().click();
    expect($('route-sheet').hidden).toBe(true);
    expect($('favourite-sheet').hidden).toBe(true);
  });

  it('rises in on a first open', async () => {
    await load();
    star().click();
    expect($('favourite-sheet').hasAttribute('data-tab-open')).toBe(true);
  });
});

describe('the remembered tab', () => {
  it('reopens Saved on the tab last chosen', async () => {
    await load();
    star().click();
    choose('favourite-sheet', 'observations');
    await tick();
    star().click();
    star().click();
    expect($('report-sheet').hidden).toBe(false);
    expect($('favourite-sheet').hidden).toBe(true);
  });

  it('remembers Trips', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'trips');
    await load();
    star().click();
    expect($('trips-sheet').hidden).toBe(false);
  });

  it('is Places for a signed-out visitor, whose sheet asks them to sign in', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'routes');
    await load({ signedIn: false });
    star().click();
    expect($('favourite-sheet').hidden).toBe(false);
    expect($('route-sheet').hidden).toBe(true);
  });
});

describe('a Saved tab', () => {
  it('closes one sheet as it opens its sibling, with no entrance', async () => {
    await load();
    star().click();
    choose('favourite-sheet', 'trips');
    await tick();
    expect($('favourite-sheet').hidden).toBe(true);
    expect($('trips-sheet').hidden).toBe(false);
    expect($('trips-sheet').hasAttribute('data-tab-open')).toBe(false);
    expect(star().getAttribute('aria-expanded')).toBe('true');
    expect(window.sessionStorage.getItem('snowdesk:app-tab-saved')).toBe('trips');
  });

  it('switches from a data-saved-go control too', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'trips');
    await load();
    star().click();
    const item = document.createElement('button');
    item.setAttribute('data-saved-go', 'routes');
    $('trips-sheet').append(item);
    item.click();
    await tick();
    expect($('trips-sheet').hidden).toBe(true);
    expect($('route-sheet').hidden).toBe(false);
    expect(window.sessionStorage.getItem('snowdesk:app-tab-saved')).toBe('routes');
  });

  it('hands the detent off before the old sheet closes (SNOW-1486)', async () => {
    const order = [];
    window.MapSheet = { handOff: vi.fn(() => order.push('handOff')) };
    try {
      await load();
      window.pwaFavouritesSheet.close.mockImplementation(() => {
        order.push('close');
        $('favourite-sheet').setAttribute('hidden', '');
      });
      star().click();
      choose('favourite-sheet', 'routes');
      await tick();
      expect(order).toEqual(['handOff', 'close']);
    } finally {
      delete window.MapSheet;
    }
  });

  it('keeps the detent across a switch with the real sheets (SNOW-1486)', async () => {
    vi.resetModules();
    await import('../../static/js/sheet_detents_core.js');
    await import('../../static/js/map_sheet.js');
    const handle = '<button type="button" data-sheet-handle aria-label="Resize panel"></button>';
    for (const id of ['favourite-sheet', 'route-sheet']) {
      const el = $(id);
      el.setAttribute('data-detents', 'half,full');
      el.setAttribute('data-detent-initial', 'half');
      el.setAttribute('data-sheet-card', '');
      el.setAttribute('data-overlay', '');
      el.innerHTML = handle;
    }
    // The star opens Saved itself, so its click is not one outside to close on.
    const places = window.MapSheet.attach($('favourite-sheet'), { triggerSelector: '#favourite-add-btn' });
    const routes = window.MapSheet.attach($('route-sheet'), { triggerSelector: '#favourite-add-btn' });
    try {
      await load();
      window.pwaFavouritesSheet = places;
      window.pwaRoutesSheet = routes;
      star().click();
      expect($('favourite-sheet').getAttribute('data-detent')).toBe('half');
      $('favourite-sheet').querySelector('[data-sheet-handle]').click();
      expect($('favourite-sheet').getAttribute('data-detent')).toBe('full');
      choose('favourite-sheet', 'routes');
      await tick();
      expect($('favourite-sheet').hidden).toBe(true);
      expect($('route-sheet').getAttribute('data-detent')).toBe('full');
    } finally {
      places.close();
      routes.close();
      delete window.MapSheet;
      delete window.pwaSheetDetentsCore;
    }
  });

  it('ignores a value that is not a Saved tab', async () => {
    await load();
    star().click();
    choose('favourite-sheet', 'downloads');
    await tick();
    expect($('favourite-sheet').hidden).toBe(false);
  });
});

describe("the binoculars' own form", () => {
  it('is not Saved, so the star stays outline over it', async () => {
    await load();
    $('report-sheet').setAttribute('data-report-direct', '');
    $('report-sheet').removeAttribute('hidden');
    await tick();
    expect(window.pwaSavedPanel.isOpen()).toBe(false);
    expect(star().getAttribute('aria-expanded')).toBe('false');
  });

  it('gives way to Saved when the star is tapped', async () => {
    await load();
    $('report-sheet').setAttribute('data-report-direct', '');
    $('report-sheet').removeAttribute('hidden');
    star().click();
    expect($('favourite-sheet').hidden).toBe(false);
  });
});

describe('the change event', () => {
  it('tells the tab bar when Saved opens and shuts', async () => {
    await load();
    const seen = [];
    const listener = (event) => seen.push(event.detail.open);
    document.addEventListener('snowdesk:saved-panel-change', listener);
    star().click();
    await tick();
    star().click();
    await tick();
    expect(seen).toEqual([true, false]);
  });
});
