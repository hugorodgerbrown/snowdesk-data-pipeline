/*
 * tests/js/test_map_overlay_exclusivity_surfaces.js — every map overlay
 * closes every other one (SNOW-658).
 *
 * The registry's own behaviour is covered in
 * tests/js/test_map_overlay_exclusivity.js against fakes. THIS file loads
 * the real surfaces — the layers menu, the favourites panel, the field-
 * observations panel, the downloads panel, the legend card, the help
 * tour's coachmark and the bulletin fill-strength flyout — into one
 * document, the way public/home.html does, and runs the full ordered
 * matrix over them: open A, open B, A must be shut.
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
 * its own rather than an eighth entry in this one.
 *
 * The fixture is a hand-copy of the seven surfaces' markup, reduced to what
 * the modules bind to — the standing trade-off for this harness (Vitest
 * cannot render a Django template). Each panel's own contents are asserted
 * in its own suite; what matters here is only which of them is on screen.
 *
 * One of the seven hides itself differently, which is the point of asking
 * each surface how to read its own state rather than assuming: the four
 * panels, the coachmark and the fill flyout use the `hidden` attribute (the
 * coachmark's overlay is additionally `pointer-events: none`), while the
 * legend uses `data-state` on its CLUSTER (#map-legend, the card's parent —
 * the (i) toggle and the date ribbon are siblings of the card and stay up
 * when it collapses).
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/**
 * Whether an element hidden by the HTML5 `hidden` attribute is on screen.
 *
 * @param {string} id A DOM id.
 * @returns {boolean}
 */
function shownByAttribute(id) {
  return !document.getElementById(id).hidden;
}

/**
 * The seven surfaces, by their registered name: how to open each one the way
 * a user does — through its own roundel or pill — and how to read whether
 * it is on screen.
 *
 * The registered name is the element's own DOM id in every case. The read
 * is per-surface because the idiom is: `hidden` for the four panels, the
 * coachmark and the fill flyout, `data-state` on the legend card's parent
 * cluster.
 *
 * The downloads sheet is driven through its bridge rather than a DOM
 * click: its roundel (#map-custom-download-control) belongs to
 * map_custom_download.js, a map-bundle module this fixture does not boot,
 * and that roundel's whole handler is `pwaDownloadsManager.toggle()` —
 * asserted in tests/js/test_map_downloads_manager.js. The other roundels
 * are bound by the modules loaded here, so they are clicked.
 */
const SURFACES = {
  'basemap-menu': {
    open: () => document.getElementById('basemap-toggle').click(),
    isOpen: () => shownByAttribute('basemap-menu'),
  },
  'favourite-sheet': {
    open: () => document.getElementById('favourite-add-btn').click(),
    isOpen: () => shownByAttribute('favourite-sheet'),
  },
  'report-sheet': {
    open: () => document.getElementById('report-btn').click(),
    isOpen: () => shownByAttribute('report-sheet'),
  },
  'map-downloads-sheet': {
    open: () => window.pwaDownloadsManager.toggle(),
    isOpen: () => shownByAttribute('map-downloads-sheet'),
  },
  'map-legend-card': {
    open: () => document.getElementById('map-legend-toggle').click(),
    isOpen: () => document.getElementById('map-legend').dataset.state === 'expanded',
  },
  'map-help-overlay': {
    open: () => document.getElementById('map-help-toggle').click(),
    isOpen: () => shownByAttribute('map-help-overlay'),
  },
  'map-fill-flyout': {
    open: () => document.getElementById('map-fill-toggle').click(),
    isOpen: () => shownByAttribute('map-fill-flyout'),
  },
};

const SURFACE_NAMES = Object.keys(SURFACES);

/**
 * The surfaces whose own control toggles — a second tap closes what the
 * first opened.
 *
 * The help "?" roundel is the one exception, and deliberately so: it
 * re-opens the tour from step 1 whatever its state, the "show me again"
 * affordance it has carried since SNOW-457.
 */
const TOGGLING_SURFACE_NAMES = SURFACE_NAMES.filter(
  (name) => name !== 'map-help-overlay',
);

/** home.html's markup for the seven surfaces, cut to what the modules bind. */
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
    </template>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="map-controls-collapsible">
        <div class="map-controls-collapsible-inner">
          <div class="map-utility-pill map-utility-pill--fill" id="map-fill-pill" data-state="collapsed">
            <button id="map-fill-toggle" type="button" aria-expanded="false"
                    aria-controls="map-fill-flyout"></button>
          </div>
        </div>
      </div>
      <div id="map-fill-flyout" class="map-fill-flyout" role="group" hidden>
        <button type="button" role="radio" aria-checked="true"
                class="map-fill-step" data-bulletins-step="0.5"></button>
      </div>
    </div>
    <div id="map-legend" data-state="collapsed">
      <div id="map-legend-card"></div>
      <button id="map-legend-toggle" type="button" aria-expanded="false"></button>
    </div>
    <ul id="map-help-steps" hidden>
      <li data-help-target="#basemap-toggle" data-help-title="Map detail">Switch base maps.</li>
      <li data-help-target="#map-legend-toggle" data-help-title="Map information">Attribution and the danger key.</li>
    </ul>
    <button id="map-help-toggle" type="button">?</button>
    <div id="map-help-overlay" hidden role="dialog" data-overlay data-map-help-no-autostart>
      <div id="map-help-ring"></div>
      <div id="map-help-tooltip" tabindex="-1">
        <button type="button" id="map-help-close" data-action="dismiss"></button>
        <p id="map-help-step-count"></p>
        <span id="map-help-step-template" hidden>Step {n} of {total}</span>
        <h3 id="map-help-tooltip-title"></h3>
        <p id="map-help-tooltip-body"></p>
        <button type="button" id="map-help-back"></button>
        <button type="button" id="map-help-next" data-label-next="Next" data-label-done="Done"></button>
      </div>
    </div>`;
}

/**
 * Load the registry and the surface modules in home.html's order.
 *
 * Seven surfaces, six modules: map_basemap_picker.js carries both the layers
 * menu and the bulletin fill-strength flyout, in two IIFEs.
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
  await import('../../static/js/map_legend.js');
  await import('../../static/js/map_basemap_picker.js');
  await import('../../static/js/map_help.js');
}

/**
 * Whether a surface is on screen, asked in that surface's own terms.
 *
 * @param {string} name A registered overlay name (also its DOM id).
 * @returns {boolean}
 */
function isOpen(name) {
  return SURFACES[name].isOpen();
}

/**
 * Open a surface the way a user does.
 *
 * @param {string} name A registered overlay name.
 * @returns {void}
 */
function openSurface(name) {
  SURFACES[name].open();
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
  // map_legend.js reads its persisted state through the same shared trio;
  // null means "collapsed", matching a first visit.
  globalThis.readStorage = () => null;
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
        openSurface(already);
        await settle();
        expect(isOpen(already)).toBe(true);

        openSurface(opened);
        await settle();

        expect(isOpen(already)).toBe(false);
        expect(isOpen(opened)).toBe(true);
      });
    }
  }
});

describe('the registry alone closes a surface', () => {
  // The matrix above goes through each surface's own control, and most of
  // the seven ALSO carry an outside-click dismiss that a click on another
  // control happens to satisfy — so a cell could pass with the registration
  // missing. This asks the registry directly, with no click in play, which
  // only a registered controller whose close() drives the real DOM can
  // answer.
  for (const name of SURFACE_NAMES) {
    it(`${name} closes when another surface announces its open`, async () => {
      openSurface(name);
      await settle();
      expect(isOpen(name)).toBe(true);

      window.pwaMapOverlays.opening('some-other-surface');
      await settle();

      expect(isOpen(name)).toBe(false);
    });
  }
});

describe('the roundel that opened a surface closes it', () => {
  for (const name of TOGGLING_SURFACE_NAMES) {
    it(`${name} closes on a second tap of its own control`, async () => {
      openSurface(name);
      await settle();
      expect(isOpen(name)).toBe(true);

      openSurface(name);
      await settle();

      expect(isOpen(name)).toBe(false);
    });
  }

  it('the help "?" roundel re-opens the tour instead of closing it', async () => {
    // Not an oversight in the loop above: the tour's own control is a "show
    // me again" affordance (SNOW-457), so a second tap restarts it from
    // step 1. Escape, "×" and Done are its close paths.
    openSurface('map-help-overlay');
    await settle();

    openSurface('map-help-overlay');
    await settle();

    expect(isOpen('map-help-overlay')).toBe(true);
  });
});
