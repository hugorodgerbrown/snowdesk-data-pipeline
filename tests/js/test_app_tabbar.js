/*
 * tests/js/test_app_tabbar.js — the installed phone app's tab bar on the map
 * (SNOW-1476; three tabs since SNOW-1479).
 *
 * static/js/app_tabbar.js against a reduced copy of the bar and the "?" menu,
 * with ``window.MapSheet`` and the panel controllers stubbed — the sheets'
 * own behaviour is tested beside each one, and Saved's
 * (static/js/saved_panel.js, SNOW-1481) in tests/js/test_saved_panel.js.
 * What is pinned here is the bar's:
 *   - three tabs, Map, Saved and Help; a tab opens its panel and lights;
 *     tapping it again, or Map, closes it;
 *   - Saved is window.pwaSavedPanel: it opens on Places, signed out too,
 *     and on the Saved tab last chosen, and a Saved tab switch keeps the
 *     Saved tab lit;
 *   - a panel closed from elsewhere lights Map again;
 *   - Help opens and closes the map's own "?" menu (window.pwaMapHelpMenu),
 *     rising rather than swapping, since it is a card of its own;
 *   - ?panel= opens its panel at boot and leaves the URL, other
 *     parameters kept; ?panel=today opens Saved on Places;
 *   - the Saved tab mirrors the sun roundel's alert dot.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MARKUP = `
  <button id="favourite-add-btn" data-favourites-eligible="true"></button>
  <button id="map-today-panel-toggle"><span data-today-dot hidden></span></button>
  <div data-app-tabbar data-app-tabbar-on-map>
    <button data-app-tab="map" aria-current="page">Map</button>
    <button data-app-tab="saved"><span data-app-tab-dot hidden></span>Saved</button>
    <button data-app-tab="help">Help</button>
  </div>
  <div id="today-sheet" hidden></div>
  <div id="favourite-sheet" hidden></div>
  <div id="route-sheet" hidden></div>
  <div id="trips-sheet" hidden></div>
  <div id="report-sheet" hidden></div>
  <div id="map-help-menu" hidden></div>`;

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

/** Publish every stubbed panel the map would. */
function stubPanels({ signedIn = true } = {}) {
  window.MapSheet = { attach: vi.fn((el) => panel(el.id)) };
  $('favourite-add-btn').setAttribute('data-favourites-eligible', String(signedIn));
  if (signedIn) {
    window.pwaTodayPanel = panel('today-sheet');
    window.pwaTripsSheet = panel('trips-sheet');
  }
  window.pwaFavouritesSheet = panel('favourite-sheet');
  window.pwaRoutesSheet = panel('route-sheet');
  window.pwaReportSheet = panel('report-sheet');
}

/**
 * Load a fresh copy of the module.
 *
 * @param {{signedIn?: boolean, url?: string}} opts
 */
async function load({ signedIn = true, url = '/map/' } = {}) {
  window.history.replaceState(null, '', url);
  stubPanels({ signedIn });
  window.pwaMapHelpMenu = panel('map-help-menu');
  vi.resetModules();
  await import('../../static/js/saved_panel.js');
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
    'pwaTripsSheet', 'pwaReportSheet', 'pwaAppTabbar', 'pwaMapHelpMenu',
    'pwaSavedPanel',
  ]) delete window[key];
  vi.restoreAllMocks();
});

describe('the bar', () => {
  it('has three tabs and no Today', async () => {
    await load();
    const tabs = [...document.querySelectorAll('[data-app-tab]')].map((b) =>
      b.getAttribute('data-app-tab'),
    );
    expect(tabs).toEqual(['map', 'saved', 'help']);
  });
});

describe('a tab', () => {
  it('opens its panel and lights', async () => {
    await load();
    tab('saved').click();
    expect($('favourite-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
    expect($('favourite-sheet').hasAttribute('data-tab-open')).toBe(true);
  });

  it('closes its panel on a second tap, lighting Map', async () => {
    await load();
    tab('saved').click();
    tab('saved').click();
    expect($('favourite-sheet').hidden).toBe(true);
    expect(lit()).toEqual(['map']);
  });

  it('closes whatever is open when Map is tapped', async () => {
    await load();
    tab('help').click();
    tab('map').click();
    expect($('map-help-menu').hidden).toBe(true);
    expect(lit()).toEqual(['map']);
  });

  it('swaps Help for Saved in one tap', async () => {
    await load();
    tab('help').click();
    tab('saved').click();
    expect($('map-help-menu').hidden).toBe(true);
    expect($('favourite-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
  });
});

describe('Saved', () => {
  it('opens the favourites sheet on Places', async () => {
    await load();
    tab('saved').click();
    expect(window.pwaFavouritesSheet.open).toHaveBeenCalled();
    expect($('favourite-sheet').hidden).toBe(false);
  });

  it('opens on Places signed out too', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'routes');
    await load({ signedIn: false });
    tab('saved').click();
    expect($('favourite-sheet').hidden).toBe(false);
    expect($('route-sheet').hidden).toBe(true);
    expect(lit()).toEqual(['saved']);
  });

  it('opens on the Saved tab last chosen', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'trips');
    await load();
    tab('saved').click();
    expect($('trips-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
  });

  it('stays lit across a Saved tab switch', async () => {
    await load();
    tab('saved').click();
    window.pwaSavedPanel.switchTo('routes');
    await tick();
    expect($('favourite-sheet').hidden).toBe(true);
    expect($('route-sheet').hidden).toBe(false);
    expect(lit()).toEqual(['saved']);
  });
});

describe('the lit tab', () => {
  it('goes back to Map when a panel is closed from elsewhere', async () => {
    await load();
    tab('saved').click();
    window.pwaFavouritesSheet.close();
    await tick();
    expect(lit()).toEqual(['map']);
  });
});

describe('Help', () => {
  it("opens the map's own help menu and lights", async () => {
    await load();
    tab('help').click();
    expect(window.pwaMapHelpMenu.open).toHaveBeenCalled();
    expect(lit()).toEqual(['help']);
  });

  it('rises in over another panel rather than swapping', async () => {
    await load();
    tab('saved').click();
    tab('help').click();
    expect($('favourite-sheet').hidden).toBe(true);
    expect($('map-help-menu').hasAttribute('data-tab-open')).toBe(true);
    expect($('map-help-menu').hasAttribute('data-tab-swap')).toBe(false);
  });

  it('closes on a second tap', async () => {
    await load();
    tab('help').click();
    tab('help').click();
    expect($('map-help-menu').hidden).toBe(true);
    expect(lit()).toEqual(['map']);
  });
});

describe('?panel=', () => {
  it('opens its panel at boot and leaves the URL, other parameters kept', async () => {
    const replace = vi.spyOn(window.history, 'replaceState');
    await load({ url: '/map/?panel=help&d=2026-11-07' });
    expect($('map-help-menu').hidden).toBe(false);
    expect(lit()).toEqual(['help']);
    expect(replace).toHaveBeenLastCalledWith(null, '', '/map/?d=2026-11-07');
  });

  it('waits for the scripts after it before opening Help', async () => {
    // A deferred script runs at readyState 'interactive', before the map
    // bundle that publishes window.pwaMapHelpMenu.
    const ready = vi.spyOn(document, 'readyState', 'get').mockReturnValue('interactive');
    window.history.replaceState(null, '', '/map/?panel=help');
    stubPanels();
    vi.resetModules();
    await import('../../static/js/app_tabbar.js');
    // Taken at once, before map.js's own deep-link reader can strip it.
    expect(window.location.search).toBe('');
    expect($('map-help-menu').hidden).toBe(true);

    window.pwaMapHelpMenu = panel('map-help-menu');
    document.dispatchEvent(new Event('DOMContentLoaded'));
    ready.mockRestore();
    expect($('map-help-menu').hidden).toBe(false);
    expect(lit()).toEqual(['help']);
  });

  it('opens Saved on Places for an old ?panel=today', async () => {
    window.sessionStorage.setItem('snowdesk:app-tab-saved', 'routes');
    await load({ url: '/map/?panel=today' });
    expect($('favourite-sheet').hidden).toBe(false);
    expect($('route-sheet').hidden).toBe(true);
    expect($('today-sheet').hidden).toBe(true);
    expect(window.pwaFavouritesSheet.open).toHaveBeenCalled();
    expect(lit()).toEqual(['saved']);
    expect(window.location.search).toBe('');
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
  it('sits on the Saved tab and mirrors the sun roundel', async () => {
    await load();
    const source = document.querySelector('#map-today-panel-toggle [data-today-dot]');
    const dot = document.querySelector('[data-app-tab-dot]');
    expect(dot.closest('[data-app-tab]').getAttribute('data-app-tab')).toBe('saved');
    expect(dot.hidden).toBe(true);
    source.hidden = false;
    await tick();
    expect(dot.hidden).toBe(false);
  });
});
