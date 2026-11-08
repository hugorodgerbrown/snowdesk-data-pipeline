/*
 * tests/js/test_app_tabbar.js — the installed phone app's tab bar on the map
 * (SNOW-1476).
 *
 * static/js/app_tabbar.js against a reduced copy of the bar, the You sheet
 * and its template, with ``window.MapSheet`` and the four panel controllers
 * stubbed — the sheets' own behaviour is tested beside each one. What is
 * pinned here is the bar's:
 *   - a tab opens its panel and lights; tapping it again, or Map, closes it;
 *   - another tab swaps one panel for the next, marked for the cross-fade;
 *   - signed out, Today and Saved both open the favourites sheet;
 *   - the Saved chips close one sheet as they open its sibling, with no
 *     entrance mark, and Saved reopens on the chip last chosen;
 *   - a panel closed from elsewhere lights Map again;
 *   - the You sheet clones its template on open, and Offline areas hands
 *     off to the downloads manager;
 *   - ?panel= opens its panel at boot and leaves the URL, other
 *     parameters kept;
 *   - the Today tab mirrors the sun roundel's alert dot.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <button id="map-today-panel-toggle"><span data-today-dot hidden></span></button>
  <div data-app-tabbar data-app-tabbar-on-map>
    <button data-app-tab="map" aria-current="page">Map</button>
    <button data-app-tab="today"><span data-app-tab-dot hidden></span>Today</button>
    <button data-app-tab="saved">Saved</button>
    <button data-app-tab="you">You</button>
  </div>
  <div id="today-sheet" hidden></div>
  <div id="favourite-sheet" hidden></div>
  <div id="route-sheet" hidden></div>
  <div id="report-sheet" hidden></div>
  <div id="you-sheet" hidden></div>
  <template id="you-sheet-template">
    <p data-you-body>You</p>
    <button data-you-offline-areas>Offline areas</button>
  </template>`;

const $ = (id) => document.getElementById(id);
const tab = (name) => document.querySelector(`[data-app-tab="${name}"]`);
const lit = () =>
  [...document.querySelectorAll('[data-app-tab]')]
    .filter((b) => b.getAttribute('aria-current') === 'page')
    .map((b) => b.getAttribute('data-app-tab'));
const tick = () => new Promise((r) => setTimeout(r, 0));

let added = [];

/** A panel controller that shows and hides its element. */
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
 * @param {{signedIn?: boolean, url?: string}} opts
 */
async function load({ signedIn = true, url = '/map/' } = {}) {
  window.history.replaceState(null, '', url);
  window.MapSheet = { attach: vi.fn((el) => panel(el.id)) };
  if (signedIn) window.pwaTodayPanel = panel('today-sheet');
  window.pwaFavouritesSheet = panel('favourite-sheet');
  window.pwaRoutesSheet = panel('route-sheet');
  window.pwaReportSheet = panel('report-sheet');
  window.pwaDownloadsManager = { open: vi.fn() };
  vi.resetModules();
  await import('../../static/js/app_tabbar.js');
  await tick();
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
    'MapSheet', 'pwaTodayPanel', 'pwaFavouritesSheet', 'pwaRoutesSheet',
    'pwaReportSheet', 'pwaDownloadsManager', 'pwaYouSheet', 'pwaAppTabbar',
  ]) delete window[key];
  vi.restoreAllMocks();
});

describe('a tab', () => {
  it('opens its panel and lights', async () => {
    await load();
    tab('today').click();
    expect($('today-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['today']);
    expect($('today-sheet').hasAttribute('data-tab-open')).toBe(true);
  });

  it('closes its panel on a second tap, lighting Map', async () => {
    await load();
    tab('today').click();
    tab('today').click();
    expect($('today-sheet').hidden).toBe(true);
    expect(lit()).toEqual(['map']);
  });

  it('closes whatever is open when Map is tapped', async () => {
    await load();
    tab('you').click();
    tab('map').click();
    expect($('you-sheet').hidden).toBe(true);
    expect(lit()).toEqual(['map']);
  });

  it('swaps one panel for the next, marked for the cross-fade', async () => {
    await load();
    tab('today').click();
    tab('saved').click();
    expect($('today-sheet').hidden).toBe(true);
    expect($('favourite-sheet').hidden).toBe(false);
    expect($('favourite-sheet').hasAttribute('data-tab-swap')).toBe(true);
    expect(lit()).toEqual(['saved']);
  });
});

describe('signed out', () => {
  it('opens the favourites sheet from Today and from Saved', async () => {
    await load({ signedIn: false });
    tab('today').click();
    expect($('favourite-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['today']);
    tab('saved').click();
    expect($('favourite-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
  });
});

describe('the Saved chips', () => {
  /** Put a chip row in the open sheet and choose `value`. */
  function choose(sheetId, value) {
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = 'saved-tab';
    input.value = value;
    $(sheetId).append(input);
    input.checked = true;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  it('close one sheet as they open its sibling', async () => {
    await load();
    tab('saved').click();
    choose('favourite-sheet', 'routes');
    await tick();
    expect($('favourite-sheet').hidden).toBe(true);
    expect($('route-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
  });

  it('replace the content with no entrance at all', async () => {
    await load();
    tab('saved').click();
    choose('favourite-sheet', 'routes');
    await tick();
    expect($('route-sheet').hasAttribute('data-tab-swap')).toBe(false);
    expect($('route-sheet').hasAttribute('data-tab-open')).toBe(false);
  });

  it('are remembered: Saved reopens on the last chip', async () => {
    await load();
    tab('saved').click();
    choose('favourite-sheet', 'observations');
    await tick();
    tab('map').click();
    tab('saved').click();
    expect($('report-sheet').hidden).toBe(false);
    expect($('favourite-sheet').hidden).toBe(true);
  });
});

describe('the lit tab', () => {
  it('goes back to Map when a panel is closed from elsewhere', async () => {
    await load();
    tab('today').click();
    window.pwaTodayPanel.close();
    await tick();
    expect(lit()).toEqual(['map']);
  });
});

describe('the You sheet', () => {
  it('clones its template on open', async () => {
    await load();
    tab('you').click();
    expect($('you-sheet').querySelector('[data-you-body]')).not.toBeNull();
    expect(window.pwaYouSheet.isOpen()).toBe(true);
  });

  it('hands Offline areas to the downloads manager', async () => {
    await load();
    tab('you').click();
    $('you-sheet').querySelector('[data-you-offline-areas]').click();
    expect(window.pwaDownloadsManager.open).toHaveBeenCalled();
    expect($('you-sheet').hidden).toBe(true);
  });
});

describe('?panel=', () => {
  it('opens its panel at boot and leaves the URL, other parameters kept', async () => {
    const replace = vi.spyOn(window.history, 'replaceState');
    await load({ url: '/map/?panel=you&d=2026-11-07' });
    expect($('you-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['you']);
    expect(replace).toHaveBeenLastCalledWith(null, '', '/map/?d=2026-11-07');
  });

  it("leaves map.js's own panel names in the URL for it to open", async () => {
    const replace = vi.spyOn(window.history, 'replaceState');
    await load({ url: '/map/?panel=routes&d=2026-11-07' });
    expect(lit()).toEqual(['map']);
    expect(replace).not.toHaveBeenCalledWith(null, '', '/map/?d=2026-11-07');
    expect(window.location.search).toBe('?panel=routes&d=2026-11-07');
  });
});

describe('the alert dot', () => {
  it('mirrors the sun roundel', async () => {
    await load();
    const source = document.querySelector('#map-today-panel-toggle [data-today-dot]');
    const dot = document.querySelector('[data-app-tab-dot]');
    expect(dot.hidden).toBe(true);
    source.hidden = false;
    await tick();
    expect(dot.hidden).toBe(false);
  });
});
