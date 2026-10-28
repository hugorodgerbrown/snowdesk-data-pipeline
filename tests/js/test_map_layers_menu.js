/*
 * tests/js/test_map_layers_menu.js — the layers menu's pages, its master
 * switches, its derived readout and its sign-in hand-off (SNOW-904,
 * SNOW-1266).
 *
 * SNOW-904 made the menu the sole control for every layer; SNOW-1266 made
 * it on/off first with options one level down. The behaviours covered
 * below are all owned by static/js/map_basemap_picker.js:
 *
 *   - level one is one row per layer; a row with options opens them on a
 *     page of its own, with a back button, and every open starts at level
 *     one;
 *   - bulletins and boundaries have MASTER switches derived from the rows
 *     on their pages, which remember what they turned off;
 *   - the header counts the level-one switches that are on, and a row with
 *     options names what is on in them;
 *   - a row whose overlay needs an account hands an anonymous visitor to
 *     the sign-in sheet rather than ticking a box over an empty layer —
 *     favourites and routes only, never field observations, which are
 *     public data;
 *   - the opacity steps dispatch ``snowdesk:bulletins-step`` and leave the
 *     menu open.
 *
 * The picker is a classic script reading bare identifiers from the shared
 * script scope (`writeStorage`, `OVERLAY_STORAGE_KEY`, …), which under
 * Vitest have to be globals. That is the same harness
 * tests/js/test_map_layers_menu_position.js uses; see its header for why
 * `map_state.js` cannot simply be imported.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/signin_cta.js';

const BULLETINS_LAST_STEP_KEY = 'snowdesk.map.bulletins.last-on-step';
const BOUNDARIES_LAST_ON_KEY = 'snowdesk.map.boundaries.last-on';

/** Every write the picker makes, in order, so a boot-time write is visible. */
let writes;

/**
 * Build the menu in the shape _map_embed.html renders it: a positioned
 * wrapper carrying the header, a level-one `role="menu"` of layer rows, and
 * one hidden `role="menu"` page per layer with options.
 *
 * @param {{favouritesEligible?: boolean, routesEligible?: boolean, step?: string, tier?: string}} [options]
 */
function buildFixture({
  favouritesEligible = true, routesEligible = true, step = 'none', tier = 'none',
} = {}) {
  const switchRow = (key, label) => `
    <li role="none">
      <button type="button" role="menuitemcheckbox"
              class="basemap-menu-item basemap-menu-item--overlay basemap-menu-layer-row group"
              data-overlay-key="${key}" data-layer-switch aria-checked="false">
        <span data-row-label>${label}</span>
      </button>
    </li>`;
  const row = (key, label, checked = false) => `
    <li role="none">
      <button type="button" role="menuitemcheckbox"
              class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="${key}" aria-checked="${checked}">${label}</button>
    </li>`;
  const optionsRow = (slug, label) => `
    <li role="none" class="basemap-menu-layer">
      <button type="button" role="menuitem" class="basemap-menu-layer-options"
              data-layers-open="${slug}">
        <span data-row-label>${label}</span>
        <span data-page-summary="${slug}"></span>
      </button>
      <button type="button" role="menuitemcheckbox" class="basemap-menu-switch group"
              data-layer-master="${slug}" data-layer-switch aria-checked="false"
              aria-label="${label}"></button>
    </li>`;
  const basemap = (key, label, checked) => `
    <li role="none">
      <button type="button" role="menuitemradio" class="basemap-menu-item"
              data-basemap-key="${key}"
              data-basemap-url="https://tiles.example.invalid/${key}.json"
              aria-checked="${checked ? 'true' : 'false'}">${label}</button>
    </li>`;
  const page = (slug, body) => `
    <ul id="basemap-menu-page-${slug}" class="basemap-menu-list" role="menu"
        data-layers-page="${slug}" hidden>
      <li role="none" class="basemap-menu-page-header">
        <button type="button" role="menuitem" class="basemap-menu-back" data-layers-back></button>
      </li>
      ${body}
    </ul>`;
  const opacity = `
    <li role="none" class="basemap-menu-opacity">
      <div class="map-fill-steps" id="map-bulletin-opacity" role="radiogroup">
        ${['0.25', '0.5', '0.75', '1'].map((value) => `
          <button type="button" role="radio" class="map-fill-step"
                  aria-checked="${value === step ? 'true' : 'false'}"
                  data-bulletins-step="${value}">
            <span class="map-fill-step-swatch" data-step="${value}"></span>
          </button>`).join('')}
      </div>
    </li>`;

  document.body.innerHTML = `
    <div id="map"
         data-favourites-eligible="${favouritesEligible}"
         data-routes-eligible="${routesEligible}"></div>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div class="map-control-group" id="map-control-group-map">
        <div id="basemap-pill" data-state="collapsed">
          <button id="basemap-toggle" aria-expanded="false"></button>
        </div>
      </div>
      <div id="map-controls-collapsible">
        <div class="map-controls-collapsible-inner map-control-group"></div>
      </div>
      <div id="basemap-menu" data-signin-url="/account/sign-in/" hidden>
        <div class="basemap-menu-header">
          <p class="basemap-menu-title">Map layers</p>
          <p class="basemap-menu-count" data-layers-count></p>
          <button type="button" class="basemap-menu-close" data-layers-close></button>
        </div>
        <ul class="basemap-menu-list" role="menu" data-layers-page="main">
          <li role="presentation" class="basemap-menu-group-label">Reference data</li>
          ${optionsRow('bulletins', 'Avalanche bulletins')}
          ${switchRow('weather', 'Weather')}
          ${switchRow('resorts', 'Resorts')}
          ${optionsRow('boundaries', 'Boundaries')}
          <li role="none" class="basemap-menu-layer">
            <button type="button" role="menuitem" class="basemap-menu-layer-options"
                    data-layers-open="basemap">
              <span data-row-label>Basemap</span>
              <span data-page-summary="basemap"></span>
            </button>
          </li>
          <li role="presentation" class="basemap-menu-group-label">Your data</li>
          ${switchRow('favourites', 'Favourites')}
          ${switchRow('routes', 'Routes')}
          ${switchRow('community_reports', 'Field observations')}
          ${switchRow('downloads', 'Downloaded areas')}
        </ul>
        ${page('bulletins', `
          <li role="none"><ul id="basemap-menu-group-bulletins" class="basemap-menu-group" role="group">
            ${row('country.ch', 'SLF bulletins (CH)', true)}
            ${row('country.fr', 'MétéoFrance (FR)')}
          </ul></li>
          ${opacity}`)}
        ${page('boundaries', `
          <li role="none"><ul id="basemap-menu-group-boundaries" class="basemap-menu-group" role="group">
            ${row('l1', 'Major regions (EAWS L1)', tier === 'l1')}
            ${row('l2', 'Minor regions (EAWS L2)', tier === 'l2')}
            ${row('l4', 'Micro regions (EAWS L4)', tier === 'l4')}
          </ul></li>
          ${row('unsupported', 'Show unsupported regions')}`)}
        ${page('basemap', `
          <li role="none"><ul id="basemap-menu-group-basemap" class="basemap-menu-group" role="group">
            ${basemap('openfreemap_liberty', 'OpenFreeMap', true)}
            ${basemap('swisstopo_winter', 'Swisstopo (CH)', false)}
          </ul></li>`)}
      </div>
    </div>
    <div id="map-layer-signin-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="map-layer-signin-template">
      <div class="sheet-header"><span>Sign in</span></div>
    </template>`;
}

/** Load the picker fresh against the current fixture. */
async function loadPicker() {
  vi.resetModules();
  await import('../../static/js/map_overlay_bounds.js');
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/map_basemap_picker.js');
}

/** Open the menu through its own toggle, as a tap would. */
function openMenu() {
  document.getElementById('basemap-toggle').click();
}

function page(slug) {
  return document.querySelector(`[data-layers-page="${slug}"]`);
}

function opener(slug) {
  return document.querySelector(`[data-layers-open="${slug}"]`);
}

function master(slug) {
  return document.querySelector(`[data-layer-master="${slug}"]`);
}

function summary(slug) {
  return document.querySelector(`[data-page-summary="${slug}"]`).textContent;
}

function count() {
  return document.querySelector('[data-layers-count]').textContent;
}

function rowFor(key) {
  return document.querySelector(`[data-overlay-key="${key}"]`);
}

beforeEach(() => {
  buildFixture();
  writes = [];
  // The picker reads these bare identifiers from the shared classic-script
  // scope; under Vitest each file is a module, so they have to be globals.
  globalThis.BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
  globalThis.MAP = null;
  globalThis.OVERLAY_STORAGE_KEY = {};
  globalThis.OVERLAY_LAYERS = {};
  globalThis.resolveBasemapStyle = () => Promise.resolve({});
  globalThis.countryCodesFor = (key) => [key.slice('country.'.length)];
  globalThis.writeStorage = (key, value) => {
    writes.push([key, value]);
    window.localStorage.setItem(key, value);
  };
  globalThis.readBoolStorage = (key, dflt) => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? dflt : raw === 'true';
  };
  globalThis.readStorage = (key) => window.localStorage.getItem(key);

  for (const name of [
    'pwaFavouritesOverlay',
    'pwaCommunityReportsOverlay',
    'pwaRoutesOverlay',
    'pwaDownloadedOverlay',
  ]) {
    window[name] = { show: vi.fn(), hide: vi.fn(), isEnabled: vi.fn(() => false) };
  }
});

afterEach(() => {
  window.localStorage.clear();
  document.body.innerHTML = '';
  for (const name of [
    'pwaFavouritesOverlay',
    'pwaCommunityReportsOverlay',
    'pwaRoutesOverlay',
    'pwaDownloadedOverlay',
  ]) {
    delete window[name];
  }
});

describe('pages', () => {
  it('opens at level one, with every options page hidden', async () => {
    await loadPicker();
    openMenu();

    expect(page('main').hidden).toBe(false);
    for (const slug of ['bulletins', 'boundaries', 'basemap']) {
      expect(page(slug).hidden).toBe(true);
    }
  });

  it('shows a layer\'s options in place of level one', async () => {
    await loadPicker();
    openMenu();

    opener('boundaries').click();

    expect(page('main').hidden).toBe(true);
    expect(page('boundaries').hidden).toBe(false);
    expect(page('bulletins').hidden).toBe(true);
    // Focus goes with the page, so a keyboard user is not left on a control
    // that has just disappeared.
    expect(document.activeElement).toBe(page('boundaries').querySelector('[data-layers-back]'));
    // And the tap was not read as one outside the menu.
    expect(document.getElementById('basemap-menu').hidden).toBe(false);
  });

  it('goes back to level one, focusing the row that opened the page', async () => {
    await loadPicker();
    openMenu();
    opener('bulletins').click();

    page('bulletins').querySelector('[data-layers-back]').click();

    expect(page('main').hidden).toBe(false);
    expect(page('bulletins').hidden).toBe(true);
    expect(document.activeElement).toBe(opener('bulletins'));
  });

  it('starts at level one on every open', async () => {
    await loadPicker();
    openMenu();
    opener('basemap').click();
    openMenu(); // closes
    openMenu(); // and opens again

    expect(page('main').hidden).toBe(false);
    expect(page('basemap').hidden).toBe(true);
  });

  it('re-measures the menu when the page changes', async () => {
    // positionMenu writes `max-height` inline from the room the viewport
    // leaves, and the content it bounds has just been swapped.
    await loadPicker();
    openMenu();
    const menu = document.getElementById('basemap-menu');
    menu.style.maxHeight = '';

    opener('boundaries').click();

    expect(menu.style.maxHeight).not.toBe('');
  });

  it('writes nothing at boot', async () => {
    await loadPicker();

    expect(writes).toEqual([]);
  });

  it('closes from its own close button', async () => {
    await loadPicker();
    openMenu();

    document.querySelector('[data-layers-close]').click();

    expect(document.getElementById('basemap-menu').hidden).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('basemap-toggle'));
  });
});

describe('the bulletins switch', () => {
  /** Mirror map.js: a dispatched step lands as the checked segment. */
  function mirrorSteps() {
    const listener = (event) => {
      for (const seg of document.querySelectorAll('[data-bulletins-step]')) {
        const on = Number(seg.dataset.bulletinsStep) === event.detail.step;
        seg.setAttribute('aria-checked', on ? 'true' : 'false');
      }
    };
    document.addEventListener('snowdesk:bulletins-step', listener);
    return () => document.removeEventListener('snowdesk:bulletins-step', listener);
  }

  it('reads on while a step above 0 is checked', async () => {
    buildFixture({ step: '0.5' });
    await loadPicker();

    expect(master('bulletins').getAttribute('aria-checked')).toBe('true');
  });

  it('reads off when no step is checked', async () => {
    await loadPicker();

    expect(master('bulletins').getAttribute('aria-checked')).toBe('false');
  });

  it('switches off to step 0 and back on to the step it left', async () => {
    const stop = mirrorSteps();
    buildFixture({ step: '0.75' });
    await loadPicker();
    const seen = [];
    const record = (event) => seen.push(event.detail.step);
    document.addEventListener('snowdesk:bulletins-step', record);

    master('bulletins').click();
    expect(master('bulletins').getAttribute('aria-checked')).toBe('false');
    expect(window.localStorage.getItem(BULLETINS_LAST_STEP_KEY)).toBe('0.75');

    master('bulletins').click();
    expect(seen).toEqual([0, 0.75]);
    expect(master('bulletins').getAttribute('aria-checked')).toBe('true');
    document.removeEventListener('snowdesk:bulletins-step', record);
    stop();
  });

  it('switches on at 50% when nothing is remembered', async () => {
    await loadPicker();
    const seen = [];
    const record = (event) => seen.push(event.detail.step);
    document.addEventListener('snowdesk:bulletins-step', record);

    master('bulletins').click();

    expect(seen).toEqual([0.5]);
    document.removeEventListener('snowdesk:bulletins-step', record);
  });

  it('counts once, however many providers are on', async () => {
    buildFixture({ step: '0.5' });
    await loadPicker();
    rowFor('country.fr').click();

    expect(count()).toBe('1 layer on');
  });
});

describe('the boundaries switch', () => {
  it('reads on while any tier is on', async () => {
    buildFixture({ tier: 'l4' });
    await loadPicker();

    expect(master('boundaries').getAttribute('aria-checked')).toBe('true');
  });

  it('turns every tier off, and back on to the ones it turned off', async () => {
    const seen = [];
    const record = (event) => seen.push([event.detail.key, event.detail.visible]);
    document.addEventListener('snowdesk:overlays-changed', record);
    buildFixture({ tier: 'l4' });
    await loadPicker();
    rowFor('l1').click();

    master('boundaries').click();
    expect(rowFor('l1').getAttribute('aria-checked')).toBe('false');
    expect(rowFor('l4').getAttribute('aria-checked')).toBe('false');
    expect(master('boundaries').getAttribute('aria-checked')).toBe('false');
    expect(JSON.parse(window.localStorage.getItem(BOUNDARIES_LAST_ON_KEY)))
      .toEqual(['l1', 'l4']);

    master('boundaries').click();
    expect(rowFor('l1').getAttribute('aria-checked')).toBe('true');
    expect(rowFor('l2').getAttribute('aria-checked')).toBe('false');
    expect(rowFor('l4').getAttribute('aria-checked')).toBe('true');
    // Each tier went through its own row, so map.js heard about each one.
    expect(seen).toContainEqual(['l4', false]);
    expect(seen).toContainEqual(['l4', true]);
    document.removeEventListener('snowdesk:overlays-changed', record);
  });

  it('switches on Micro regions when nothing is remembered', async () => {
    await loadPicker();

    master('boundaries').click();

    expect(rowFor('l4').getAttribute('aria-checked')).toBe('true');
    expect(rowFor('l1').getAttribute('aria-checked')).toBe('false');
  });

  it('leaves "Show unsupported regions" alone', async () => {
    buildFixture({ tier: 'l4' });
    await loadPicker();

    master('boundaries').click();

    expect(rowFor('unsupported').getAttribute('aria-checked')).toBe('false');
  });
});

describe('the derived readout', () => {
  it('counts the level-one switches that are on, never the basemap', async () => {
    await loadPicker();
    expect(count()).toBe('No layers on');

    rowFor('weather').click();
    expect(count()).toBe('1 layer on');

    rowFor('resorts').click();
    expect(count()).toBe('2 layers on');
  });

  it('counts a master switch as one layer', async () => {
    buildFixture({ step: '0.5', tier: 'l4' });
    await loadPicker();

    // Bulletins at 50% and Micro regions: two layers, whatever is inside.
    expect(count()).toBe('2 layers on');
  });

  it('says "None selected" for a page with nothing on', async () => {
    await loadPicker();

    expect(summary('boundaries')).toBe('None selected');
  });

  it('names the rows that are on, shortened from their own labels', async () => {
    await loadPicker();

    rowFor('country.fr').click();

    // The trailing parenthetical is stripped; nothing else is.
    expect(summary('bulletins')).toBe('SLF bulletins, MétéoFrance');
  });

  it('names the basemap\'s own selection', async () => {
    await loadPicker();

    expect(summary('basemap')).toBe('OpenFreeMap');
  });

  it('falls back to a count when the names would not fit', async () => {
    buildFixture({ tier: 'l4' });
    await loadPicker();

    rowFor('l1').click();
    rowFor('l2').click();

    // Three tier names are past the line, so the summary states the count
    // rather than clipping a name mid-word.
    expect(summary('boundaries')).toBe('3 of 3 on');
  });
});

describe('the four rows driven by a bridge', () => {
  it('shows and hides through the bridge rather than writing storage itself', async () => {
    await loadPicker();

    rowFor('community_reports').click();
    expect(window.pwaCommunityReportsOverlay.show).toHaveBeenCalledTimes(1);

    rowFor('community_reports').click();
    expect(window.pwaCommunityReportsOverlay.hide).toHaveBeenCalledTimes(1);
    // The bridge persists its own key; the picker must not write a second
    // copy of the same preference.
    expect(writes).toEqual([]);
  });

  it('never writes the downloads key on any path but a real click', async () => {
    // SNOW-857's tri-state, seen from this side: the picker's own storage
    // write is what would convert "untouched" into "explicitly off".
    await loadPicker();
    openMenu();

    expect(writes).toEqual([]);

    rowFor('downloads').click();

    expect(window.pwaDownloadedOverlay.show).toHaveBeenCalledTimes(1);
    expect(writes).toEqual([]);
  });

  it('follows the downloads overlay when something else moves it', async () => {
    // Untouched means "on while offline", so a connectivity flip repaints
    // the overlay with nobody having touched this row.
    await loadPicker();
    expect(rowFor('downloads').getAttribute('aria-checked')).toBe('false');

    document.dispatchEvent(
      new CustomEvent('snowdesk:downloaded-overlay-changed', {
        detail: { visible: true },
      }),
    );

    expect(rowFor('downloads').getAttribute('aria-checked')).toBe('true');
    expect(count()).toBe('1 layer on');
  });

  it('follows the other three when something outside the menu moves them', async () => {
    // SNOW-904 review: the menu is the only CONTROL, but not the only
    // CALLER. row_focus.js's reveal() turns a layer on when someone focuses
    // or creates a favourite, route or observation while it is off, and the
    // deep-link paths do the same. Without this the row's aria-checked went
    // stale, so the header undercounted and the row's next click called
    // show() on an already-shown layer — a wasted click for the user.
    await loadPicker();
    expect(rowFor('routes').getAttribute('aria-checked')).toBe('false');

    window.pwaRoutesOverlay.isEnabled = vi.fn(() => true);
    document.dispatchEvent(
      new CustomEvent('snowdesk:overlay-visibility-changed'),
    );

    expect(rowFor('routes').getAttribute('aria-checked')).toBe('true');
    expect(count()).toBe('1 layer on');
  });

  it('re-seeds every bridge-backed row, not just the one that moved', async () => {
    // The listener reads all four rather than trusting an event payload to
    // say which changed — the bridges are the state, so reading them all is
    // both simpler and correct whichever one moved.
    await loadPicker();

    window.pwaFavouritesOverlay.isEnabled = vi.fn(() => true);
    window.pwaCommunityReportsOverlay.isEnabled = vi.fn(() => true);
    document.dispatchEvent(
      new CustomEvent('snowdesk:overlay-visibility-changed'),
    );

    expect(rowFor('favourites').getAttribute('aria-checked')).toBe('true');
    expect(rowFor('community_reports').getAttribute('aria-checked')).toBe('true');
    expect(count()).toBe('2 layers on');
  });
});

describe('the sign-in hand-off', () => {
  it('opens the sheet instead of ticking the row, for favourites', async () => {
    buildFixture({ favouritesEligible: false });
    await loadPicker();
    openMenu();

    rowFor('favourites').click();

    const sheet = document.getElementById('map-layer-signin-sheet');
    expect(sheet.hidden).toBe(false);
    expect(sheet.textContent).toContain('show your favourites on the map');
    expect(sheet.querySelector('a').getAttribute('href')).toBe('/account/sign-in/');
    // Nothing was toggled and no overlay was asked for.
    expect(rowFor('favourites').getAttribute('aria-checked')).toBe('false');
    expect(window.pwaFavouritesOverlay.show).not.toHaveBeenCalled();
  });

  it('opens the sheet for routes, with its own sentence', async () => {
    buildFixture({ routesEligible: false });
    await loadPicker();
    openMenu();

    rowFor('routes').click();

    const sheet = document.getElementById('map-layer-signin-sheet');
    expect(sheet.hidden).toBe(false);
    expect(sheet.textContent).toContain('upload routes');
    expect(window.pwaRoutesOverlay.show).not.toHaveBeenCalled();
  });

  it('leaves field observations working signed out', async () => {
    // #map's data-community-reports-eligible is a hardcoded "true" because
    // community reports are public data. This row must toggle, not hand off.
    buildFixture({ favouritesEligible: false, routesEligible: false });
    await loadPicker();
    openMenu();

    rowFor('community_reports').click();

    expect(document.getElementById('map-layer-signin-sheet').hidden).toBe(true);
    expect(window.pwaCommunityReportsOverlay.show).toHaveBeenCalledTimes(1);
    expect(rowFor('community_reports').getAttribute('aria-checked')).toBe('true');
  });

  it('toggles normally once the visitor has an account', async () => {
    await loadPicker();
    openMenu();

    rowFor('favourites').click();

    expect(document.getElementById('map-layer-signin-sheet').hidden).toBe(true);
    expect(window.pwaFavouritesOverlay.show).toHaveBeenCalledTimes(1);
  });
});

describe('the bulletin opacity row', () => {
  /** Every ``snowdesk:bulletins-step`` dispatched while ``fn`` runs. */
  function captureSteps(fn) {
    const seen = [];
    const listener = (event) => seen.push(event.detail.step);
    document.addEventListener('snowdesk:bulletins-step', listener);
    try {
      fn();
    } finally {
      document.removeEventListener('snowdesk:bulletins-step', listener);
    }
    return seen;
  }

  function step(value) {
    return document.querySelector(`#basemap-menu [data-bulletins-step="${value}"]`);
  }

  it('dispatches the chosen step and leaves the menu open', async () => {
    await loadPicker();
    openMenu();
    const menu = document.getElementById('basemap-menu');
    expect(menu.hidden).toBe(false);

    const seen = captureSteps(() => step('0.75').click());

    expect(seen).toEqual([0.75]);
    // A setting tuned against the map, not a choice that ends the visit:
    // neither the step's own click nor the document dismiss closes it.
    expect(menu.hidden).toBe(false);
    expect(document.getElementById('basemap-toggle').getAttribute('aria-expanded'))
      .toBe('true');
  });

  it('writes no aria-checked of its own', async () => {
    // map.js mirrors the EFFECTIVE step back onto every [data-bulletins-step];
    // an optimistic tick here could disagree with it.
    buildFixture({ step: '0.5' });
    await loadPicker();
    openMenu();

    step('1').click();

    expect(step('1').getAttribute('aria-checked')).toBe('false');
    expect(step('0.5').getAttribute('aria-checked')).toBe('true');
  });

  it('is not a menu row: no style swap, no storage write', async () => {
    const swap = vi.fn(() => Promise.resolve({}));
    globalThis.resolveBasemapStyle = swap;
    await loadPicker();
    openMenu();

    step('0.25').click();

    expect(swap).not.toHaveBeenCalled();
    expect(summary('bulletins')).toBe('SLF bulletins');
    expect(writes).toEqual([]);
  });

  it('asks for the regions layers back when a style swap dropped them', async () => {
    globalThis.MAP = { getLayer: () => null };
    const reinstall = vi.fn();
    document.addEventListener('snowdesk:regions-reinstall', reinstall);
    buildFixture({ step: '0.5' });
    await loadPicker();

    step('0.5').click();
    expect(reinstall).toHaveBeenCalledTimes(1);

    // Switching off paints nothing, so it needs nothing reinstalled.
    master('bulletins').click();
    expect(reinstall).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:regions-reinstall', reinstall);
  });

  it('stays open when the chevron folds the "yours" group away', async () => {
    // Layers sits in the always-visible "map" capsule now, so collapsing the
    // other group leaves the menu's roundel on screen — the menu has no
    // reason to close with it, as it did while layers was collapsible.
    await loadPicker();
    openMenu();

    document.getElementById('map-controls-br').dataset.expanded = 'false';
    await Promise.resolve();

    expect(document.getElementById('basemap-menu').hidden).toBe(false);
  });
});

// SNOW-1196: map_weather_window.js keeps "Forecast only" on the weather row
// while the layer is on. The off path hides the layers directly, with no
// bridge to announce it, so the picker has to.
describe('the weather row', () => {
  it('announces the layer going off', async () => {
    globalThis.MAP = { getLayer: () => null, setLayoutProperty: vi.fn() };
    globalThis.OVERLAY_LAYERS = { weather: ['weather-symbols'] };
    const announced = vi.fn();
    document.addEventListener('snowdesk:overlay-visibility-changed', announced);
    await loadPicker();

    rowFor('weather').click();
    expect(announced).not.toHaveBeenCalled();
    rowFor('weather').click();
    expect(announced).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:overlay-visibility-changed', announced);
  });
});
