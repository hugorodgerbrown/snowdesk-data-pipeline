/*
 * tests/js/test_map_panel_overlay_switch.js — each map panel's "Show on the
 * map" switch and its layers-menu row stay in step, both ways.
 *
 * static/js/map_panel_overlay_switch.js binds four switches (favourites,
 * field observations, routes, downloads) to the layers-menu rows for the
 * same layers. A switch flip CLICKS the row, so the row's own handler in
 * static/js/map_basemap_picker.js decides what happens — which is why this
 * file loads the real picker against a menu fixture rather than faking it:
 * the sign-in hand-off and the offline-disabled guard are the picker's, and
 * the switch must inherit them, not reimplement them.
 *
 * The bridges are stateful fakes with the real contract: show()/hide() set
 * the preference ``isEnabled()`` reports and announce
 * ``snowdesk:overlay-visibility-changed``, exactly as map.js's bridges do —
 * that announcement is what carries a row click back to the switch.
 *
 * The picker reads bare identifiers from the shared classic-script scope,
 * so they are globals here — the same harness as test_map_layers_menu.js.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/signin_cta.js';

const KEYS = {
  favourites: { bridge: 'pwaFavouritesOverlay', toggle: 'map-favourites-overlay-toggle' },
  community_reports: {
    bridge: 'pwaCommunityReportsOverlay',
    toggle: 'map-community-reports-overlay-toggle',
  },
  routes: { bridge: 'pwaRoutesOverlay', toggle: 'map-routes-overlay-toggle' },
  downloads: { bridge: 'pwaDownloadedOverlay', toggle: 'map-downloads-overlay-toggle' },
};

/**
 * A bridge with the real contract: a preference, and an announcement on
 * every change.
 *
 * @param {boolean} enabled - the starting preference.
 */
function fakeBridge(enabled) {
  const state = { enabled };
  const announce = () =>
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
  return {
    show: vi.fn(() => {
      state.enabled = true;
      announce();
    }),
    hide: vi.fn(() => {
      state.enabled = false;
      announce();
    }),
    isEnabled: vi.fn(() => state.enabled),
    isVisible: vi.fn(() => state.enabled),
  };
}

/**
 * The layers menu (one section holding the four rows) and one panel body
 * carrying all four switches, as _ugc_panel.html renders each one's foot.
 *
 * @param {{favouritesEligible?: boolean, menu?: boolean}} [options]
 */
function buildFixture({ favouritesEligible = true, menu = true } = {}) {
  const row = (key, label) => `
    <li role="none">
      <button type="button" role="menuitemcheckbox"
              class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="${key}" aria-checked="false">${label}</button>
    </li>`;
  const strip = (id) => `
    <div data-panel-overlay-toggle>
      <label for="${id}">Show on the map</label>
      <label for="${id}"><input id="${id}" type="checkbox" role="switch"></label>
    </div>`;
  const menuMarkup = menu
    ? `
      <div id="basemap-menu" data-signin-url="/account/sign-in/" hidden>
        <p data-layers-count></p>
        <ul class="basemap-menu-list" role="menu">
          <li role="presentation" class="basemap-menu-section">
            <button type="button" data-section-toggle="places" aria-expanded="true"
                    aria-controls="basemap-menu-group-places"></button>
            <ul id="basemap-menu-group-places" class="basemap-menu-group" role="group">
              ${row('favourites', 'Favourites')}
              ${row('routes', 'Routes')}
              ${row('community_reports', 'Field observations')}
              ${row('downloads', 'Display downloaded areas')}
            </ul>
          </li>
        </ul>
      </div>`
    : '';
  document.body.innerHTML = `
    <div id="map"
         data-favourites-eligible="${favouritesEligible}"
         data-routes-eligible="true"></div>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div class="map-control-group" id="map-control-group-map">
        <div id="basemap-pill" data-state="collapsed">
          <button id="basemap-toggle" aria-expanded="false"></button>
        </div>
      </div>
      ${menuMarkup}
    </div>
    <div id="favourite-sheet">
      ${Object.values(KEYS).map(({ toggle }) => strip(toggle)).join('')}
    </div>
    <div id="map-layer-signin-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="map-layer-signin-template">
      <div class="sheet-header"><span>Sign in</span></div>
    </template>`;
}

/**
 * Every document/window listener a loaded module registers, so afterEach can
 * take them off again. Each test re-runs the modules' IIFEs against a fresh
 * fixture, and a listener left on ``document`` by an earlier test would
 * answer this test's events a second time.
 */
let registered = [];

/** Record listeners added to ``target`` while modules load. */
function trackListeners(target) {
  const original = target.addEventListener.bind(target);
  vi.spyOn(target, 'addEventListener').mockImplementation((type, fn, opts) => {
    registered.push([target, type, fn, opts]);
    return original(type, fn, opts);
  });
}

/** Load the picker (when the page has a menu) and the switch module. */
async function loadModules({ menu = true } = {}) {
  trackListeners(document);
  trackListeners(window);
  vi.resetModules();
  if (menu) {
    await import('../../static/js/map_overlay_bounds.js');
    await import('../../static/js/map_sheet.js');
    await import('../../static/js/map_basemap_picker.js');
  }
  await import('../../static/js/map_panel_overlay_switch.js');
  vi.restoreAllMocks();
}

function toggle(key) {
  return /** @type {HTMLInputElement} */ (document.getElementById(KEYS[key].toggle));
}

function rowFor(key) {
  return document.querySelector(`#basemap-menu [data-overlay-key="${key}"]`);
}

/** Flip a switch the way a tap does: the checkbox moves, then `change`. */
function flip(key) {
  const input = toggle(key);
  input.checked = !input.checked;
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

/** Install a fresh bridge per overlay, each starting at ``enabled``. */
function installBridges(enabled = {}) {
  for (const [key, { bridge }] of Object.entries(KEYS)) {
    window[bridge] = fakeBridge(!!enabled[key]);
  }
}

beforeEach(() => {
  globalThis.BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
  globalThis.MAP = null;
  globalThis.OVERLAY_STORAGE_KEY = {};
  globalThis.OVERLAY_LAYERS = {};
  globalThis.resolveBasemapStyle = () => Promise.resolve({});
  globalThis.countryCodesFor = (key) => [key.slice('country.'.length)];
  globalThis.writeStorage = (key, value) => window.localStorage.setItem(key, value);
  globalThis.readBoolStorage = (key, dflt) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? dflt : raw === 'true';
  };
  globalThis.LAYERS_SECTION_STORAGE_KEY = (slug) => `snowdesk.map.layers.section.${slug}`;
  globalThis.LAYERS_SECTION_DEFAULT_OPEN = 'places';
});

afterEach(() => {
  for (const [target, type, fn, opts] of registered) {
    target.removeEventListener(type, fn, opts);
  }
  registered = [];
  window.localStorage.clear();
  document.body.innerHTML = '';
  for (const { bridge } of Object.values(KEYS)) delete window[bridge];
  delete window.pwaPanelOverlaySwitch;
});

describe('seeding a freshly cloned panel', () => {
  it('sets every switch from its bridge\'s isEnabled()', async () => {
    buildFixture();
    installBridges({ favourites: true, downloads: true });
    await loadModules();

    window.pwaPanelOverlaySwitch.sync();

    expect(toggle('favourites').checked).toBe(true);
    expect(toggle('downloads').checked).toBe(true);
    expect(toggle('routes').checked).toBe(false);
    expect(toggle('community_reports').checked).toBe(false);
  });

  it('leaves a switch alone while its bridge is not published', async () => {
    // "Not known" is not "off": a switch the module cannot answer for keeps
    // whatever it was rendered with.
    buildFixture();
    installBridges();
    delete window.pwaRoutesOverlay;
    toggle('routes').checked = true;
    await loadModules();

    window.pwaPanelOverlaySwitch.sync();

    expect(toggle('routes').checked).toBe(true);
  });
});

describe.each(Object.keys(KEYS))('the %s switch and its menu row', (key) => {
  it('flipping the switch on turns the row on, through the bridge', async () => {
    buildFixture();
    installBridges();
    await loadModules();
    window.pwaPanelOverlaySwitch.sync();

    flip(key);

    expect(window[KEYS[key].bridge].show).toHaveBeenCalledTimes(1);
    expect(rowFor(key).getAttribute('aria-checked')).toBe('true');
    expect(toggle(key).checked).toBe(true);
  });

  it('flipping the switch off turns the row off', async () => {
    buildFixture();
    installBridges({ [key]: true });
    await loadModules();
    // The picker seeds its rows from the bridges on the first announcement.
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    expect(rowFor(key).getAttribute('aria-checked')).toBe('true');
    expect(toggle(key).checked).toBe(true);

    flip(key);

    expect(window[KEYS[key].bridge].hide).toHaveBeenCalledTimes(1);
    expect(rowFor(key).getAttribute('aria-checked')).toBe('false');
    expect(toggle(key).checked).toBe(false);
  });

  it('clicking the row moves the switch, both ways', async () => {
    buildFixture();
    installBridges();
    await loadModules();
    window.pwaPanelOverlaySwitch.sync();

    rowFor(key).click();
    expect(toggle(key).checked).toBe(true);

    rowFor(key).click();
    expect(toggle(key).checked).toBe(false);
  });
});

describe('what the row declines, the switch declines', () => {
  it('hands a signed-out visitor to sign-in, and puts the switch back', async () => {
    // The layers-menu row's own hand-off, inherited rather than restated:
    // nothing is ticked and no overlay is asked for.
    buildFixture({ favouritesEligible: false });
    installBridges();
    await loadModules();
    window.pwaPanelOverlaySwitch.sync();

    flip('favourites');

    expect(document.getElementById('map-layer-signin-sheet').hidden).toBe(false);
    expect(window.pwaFavouritesOverlay.show).not.toHaveBeenCalled();
    expect(rowFor('favourites').getAttribute('aria-checked')).toBe('false');
    expect(toggle('favourites').checked).toBe(false);
  });

  it('leaves field observations working signed out', async () => {
    buildFixture({ favouritesEligible: false });
    installBridges();
    await loadModules();

    flip('community_reports');

    expect(document.getElementById('map-layer-signin-sheet').hidden).toBe(true);
    expect(window.pwaCommunityReportsOverlay.show).toHaveBeenCalledTimes(1);
    expect(toggle('community_reports').checked).toBe(true);
  });

  it('respects a row the offline gate has disabled', async () => {
    buildFixture();
    installBridges();
    await loadModules();
    rowFor('routes').setAttribute('aria-disabled', 'true');

    flip('routes');

    expect(window.pwaRoutesOverlay.show).not.toHaveBeenCalled();
    expect(toggle('routes').checked).toBe(false);
  });
});

describe('a page without the layers menu', () => {
  it('drives the bridge directly', async () => {
    buildFixture({ menu: false });
    installBridges();
    await loadModules({ menu: false });

    flip('downloads');
    expect(window.pwaDownloadedOverlay.show).toHaveBeenCalledTimes(1);
    expect(toggle('downloads').checked).toBe(true);

    flip('downloads');
    expect(window.pwaDownloadedOverlay.hide).toHaveBeenCalledTimes(1);
    expect(toggle('downloads').checked).toBe(false);
  });
});

describe('a move from outside both controls', () => {
  it('reaches an open panel\'s switch', async () => {
    // A placement flow, a deep link or row_focus.js's reveal() calls the
    // bridge directly; its announcement is what the switch follows.
    buildFixture();
    installBridges();
    await loadModules();
    window.pwaPanelOverlaySwitch.sync();

    window.pwaFavouritesOverlay.show();

    expect(toggle('favourites').checked).toBe(true);
    expect(rowFor('favourites').getAttribute('aria-checked')).toBe('true');
  });
});
