/*
 * tests/js/test_map_overlay_exclusivity_surfaces.js — every map overlay
 * closes every other one (SNOW-658).
 *
 * The registry's own behaviour is covered in
 * tests/js/test_map_overlay_exclusivity.js against fakes. THIS file loads
 * the real surfaces — the layers menu, the favourites panel, the field-
 * observations panel and the downloads panel — into one document, the way
 * public/home.html does, and runs the full ordered matrix over them: open
 * A, open B, A must be shut.
 *
 * Why a matrix rather than the pairs a bug report would name: the wiring
 * this ticket replaced WAS the named pairs, three of the fifteen
 * relationships between six surfaces, each added by whichever ticket
 * introduced the newest overlay. The report sheet had none at all. A
 * matrix is the only shape in which a surface that forgets to register is
 * a failing test rather than a surface nobody thought about — which is
 * why ``names()`` is asserted first, so the failure says which one.
 *
 * The anchored detail popup is registered too (map.js), and is covered in
 * tests/js/test_map_detail_popup_exclusivity.js — reaching it needs the
 * whole map bundle booted against a MapLibre stub, which is a fixture of
 * its own rather than a sixth entry in this one.
 *
 * The fixture is a hand-copy of the four surfaces' markup, reduced to what
 * the modules bind to — the standing trade-off for this harness (Vitest
 * cannot render a Django template). Each panel's own contents are asserted
 * in its own suite; what matters here is only which of them is on screen.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/**
 * The four surfaces, by their registered name, with how to open each one
 * the way a user does — through its own roundel or pill.
 *
 * The registered name is the element's own DOM id in every case, which is
 * also what the "is it open?" read below keys off.
 *
 * The downloads sheet is driven through its bridge rather than a DOM
 * click: its roundel (#map-custom-download-control) belongs to
 * map_custom_download.js, a map-bundle module this fixture does not boot,
 * and that roundel's whole handler is `pwaDownloadsManager.toggle()` —
 * asserted in tests/js/test_map_downloads_manager.js. The other three
 * roundels are bound by the modules loaded here, so they are clicked.
 */
const SURFACES = {
  'basemap-menu': () => document.getElementById('basemap-toggle').click(),
  'favourite-sheet': () => document.getElementById('favourite-add-btn').click(),
  'report-sheet': () => document.getElementById('report-btn').click(),
  'map-downloads-sheet': () => window.pwaDownloadsManager.toggle(),
};

const SURFACE_NAMES = Object.keys(SURFACES);

/** home.html's markup for the four surfaces, cut to what the modules bind. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"></div>
    <div id="basemap-pill" data-state="collapsed">
      <button id="basemap-toggle" aria-expanded="false"></button>
      <ul id="basemap-menu" hidden>
        <li><button class="basemap-menu-item" data-basemap-key="openfreemap_liberty"></button></li>
      </ul>
    </div>
    <button id="favourite-add-btn"
            data-favourites-eligible="true"
            data-favourite-create-url="/favourites/partials/create/"
            data-favourite-list-url="/favourites/partials/list/?variant=map"
            data-favourite-rename-url-template="/favourites/partials/__UUID__/rename/"
            data-favourite-delete-url-template="/favourites/partials/__UUID__/delete/"></button>
    <div id="favourite-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="favourite-list-template">
      <div><div data-favourites-rows></div>
        <button type="button" data-panel-add></button>
        <input id="map-favourites-overlay-toggle" type="checkbox" role="switch"></div>
    </template>
    <template id="favourite-create-template">
      <form id="favourite-create-form">
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
        <input type="hidden" name="lat"><input type="hidden" name="lon">
      </form>
    </template>
    <button id="report-btn"
            data-report-eligible="true"
            data-report-form-url="/partials/report/form/"
            data-report-list-url="/partials/report/list/"></button>
    <div id="report-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="report-list-template">
      <div><div data-report-rows></div>
        <button type="button" data-panel-add></button>
        <input id="map-community-reports-overlay-toggle" type="checkbox" role="switch"></div>
    </template>
    <div id="map-downloads-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="map-downloads-body-template">
      <div><div data-downloads-group="region" hidden><ul data-downloads-list-region></ul></div>
        <div data-downloads-group="custom" hidden><ul data-downloads-list-custom></ul></div>
        <p data-downloads-empty hidden></p>
        <button type="button" data-panel-add></button>
        <input id="map-downloads-overlay-toggle" type="checkbox" role="switch"></div>
    </template>
    <template id="map-downloads-row-template">
      <li><span data-row-label></span><span data-row-meta></span><span data-row-value></span></li>
    </template>`;
}

/**
 * Load the registry and the four surface modules in home.html's order.
 *
 * @returns {Promise<void>}
 */
async function loadSurfaces() {
  vi.resetModules();
  delete window.pwaMapOverlays;
  delete window.MapSheet;
  delete window.pwaDownloadsManager;
  await import('../../static/js/map_overlay_exclusivity.js');
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/favourites.js');
  await import('../../static/js/report.js');
  await import('../../static/js/map_downloads_manager.js');
  await import('../../static/js/map_basemap_picker.js');
}

/**
 * Whether a surface is on screen. All four hide with the `hidden`
 * attribute — the menu through `menu.hidden`, the three sheets through
 * the shared overlay-sheet idiom.
 *
 * @param {string} name A registered overlay name (also its DOM id).
 * @returns {boolean}
 */
function isOpen(name) {
  return !document.getElementById(name).hidden;
}

/** Let the downloads sheet's async render settle. */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(async () => {
  buildFixture();
  // The picker reads these from the shared classic-script scope; under
  // Vitest each file is a module, so they have to be globals.
  globalThis.BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
  globalThis.MAP = null;
  globalThis.writeStorage = () => {};
  globalThis.OVERLAY_STORAGE_KEY = {};
  globalThis.resolveBasemapStyle = () => Promise.resolve({});
  globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };
  window.snowdeskMapState = { map: { getCenter: () => ({ lat: 46.1, lng: 7.2 }) } };
  await loadSurfaces();
});

describe('the registered set', () => {
  it('is every surface the matrix below runs over', () => {
    // A new overlay that forgets to register fails HERE, naming itself,
    // rather than opening over whatever happens to be up. `map-detail-popup`
    // is map.js's and is not loaded by this fixture.
    expect(window.pwaMapOverlays.names()).toEqual([...SURFACE_NAMES].sort());
  });
});

describe('opening one overlay closes every other', () => {
  for (const opened of SURFACE_NAMES) {
    for (const already of SURFACE_NAMES) {
      if (opened === already) continue;

      it(`${opened} closes ${already}`, async () => {
        SURFACES[already]();
        await settle();
        expect(isOpen(already)).toBe(true);

        SURFACES[opened]();
        await settle();

        expect(isOpen(already)).toBe(false);
        expect(isOpen(opened)).toBe(true);
      });
    }
  }
});

describe('the roundel that opened a surface closes it', () => {
  for (const name of SURFACE_NAMES) {
    it(`${name} closes on a second tap of its own control`, async () => {
      SURFACES[name]();
      await settle();
      expect(isOpen(name)).toBe(true);

      SURFACES[name]();
      await settle();

      expect(isOpen(name)).toBe(false);
    });
  }
});
