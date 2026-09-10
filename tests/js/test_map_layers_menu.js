/*
 * tests/js/test_map_layers_menu.js — the layers menu's collapsible
 * sections, its derived readout and its sign-in hand-off (SNOW-904).
 *
 * The menu became the sole control for every layer this ticket: four
 * overlays that were toggled from a "Display on the map" switch in the
 * footer of their own panel (downloads, favourites, field observations,
 * routes) are rows here. Three behaviours came with them, all owned by
 * static/js/map_basemap_picker.js and all covered below:
 *
 *   - each section opens and closes, independently and persistently, with
 *     Conditions the one open on a first visit;
 *   - a heading carries a derived second line naming what is on inside it,
 *     and the menu a header count of the overlay rows that are on;
 *   - a row whose overlay needs an account hands an anonymous visitor to
 *     the sign-in sheet rather than ticking a box over an empty layer —
 *     favourites and routes only, never field observations, which are
 *     public data.
 *
 * The picker is a classic script reading bare identifiers from the shared
 * script scope (`writeStorage`, `OVERLAY_STORAGE_KEY`,
 * `LAYERS_SECTION_STORAGE_KEY`, …), which under Vitest have to be globals.
 * That is the same harness tests/js/test_map_layers_menu_position.js uses;
 * see its header for why `map_state.js` cannot simply be imported.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/signin_cta.js';

const SECTION_KEY = (slug) => `snowdesk.map.layers.section.${slug}`;

/** Every write the picker makes, in order, so a boot-time write is visible. */
let writes;

/**
 * Build the menu in the shape _map_embed.html renders it: a positioned
 * wrapper carrying the header, and an inner scrolling `role="menu"` whose
 * sections each hold a `role="group"` of rows.
 *
 * @param {{favouritesEligible?: boolean, routesEligible?: boolean}} [options]
 */
function buildFixture({ favouritesEligible = true, routesEligible = true } = {}) {
  const row = (key, label) => `
    <li role="none">
      <button type="button" role="menuitemcheckbox"
              class="basemap-menu-item basemap-menu-item--overlay"
              data-overlay-key="${key}" aria-checked="false">${label}</button>
    </li>`;
  const basemap = (key, label, checked) => `
    <li role="none">
      <button type="button" role="menuitemradio" class="basemap-menu-item"
              data-basemap-key="${key}"
              data-basemap-url="https://tiles.example.invalid/${key}.json"
              aria-checked="${checked ? 'true' : 'false'}">${label}</button>
    </li>`;
  const section = (slug, title, rows) => `
    <li role="presentation" class="basemap-menu-section">
      <button type="button" class="basemap-menu-section-label"
              data-section-toggle="${slug}" aria-expanded="false"
              aria-controls="basemap-menu-group-${slug}">
        <span class="basemap-menu-section-text">
          <span class="basemap-menu-section-title">${title}</span>
          <span class="basemap-menu-section-summary" data-section-summary></span>
        </span>
      </button>
      <ul id="basemap-menu-group-${slug}" class="basemap-menu-group" role="group" hidden>
        ${rows}
      </ul>
    </li>`;

  document.body.innerHTML = `
    <div id="map"
         data-favourites-eligible="${favouritesEligible}"
         data-routes-eligible="${routesEligible}"></div>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="map-controls-collapsible">
        <div id="basemap-pill" data-state="collapsed">
          <button id="basemap-toggle" aria-expanded="false"></button>
        </div>
      </div>
      <div id="basemap-menu" data-signin-url="/account/sign-in/" hidden>
        <div class="basemap-menu-header">
          <p class="basemap-menu-title">Map display options</p>
          <p class="basemap-menu-count" data-layers-count></p>
        </div>
        <ul class="basemap-menu-list" role="menu">
          ${section(
            'places',
            'Places',
            row('resorts', 'Resorts')
              + row('favourites', 'Favourites')
              + row('routes', 'Routes'),
          )}
          ${section(
            'conditions',
            'Conditions',
            row('country.ch', 'SLF bulletins (CH)')
              + row('weather', 'Weather')
              + row('community_reports', 'Field observations'),
          )}
          ${section(
            'basemap',
            'Basemap',
            basemap('openfreemap_liberty', 'OpenFreeMap', true)
              + basemap('swisstopo_winter', 'Swisstopo (CH)', false)
              + row('slope', 'Display slope angles')
              + row('downloads', 'Display downloaded areas'),
          )}
        </ul>
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

function group(slug) {
  return document.getElementById(`basemap-menu-group-${slug}`);
}

function heading(slug) {
  return document.querySelector(`[data-section-toggle="${slug}"]`);
}

function summary(slug) {
  return heading(slug)
    .closest('.basemap-menu-section')
    .querySelector('[data-section-summary]')
    .textContent;
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
  globalThis.LAYERS_SECTION_STORAGE_KEY = SECTION_KEY;
  globalThis.LAYERS_SECTION_DEFAULT_OPEN = 'conditions';

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

describe('collapsible sections', () => {
  it('opens Conditions and nothing else on a first visit', async () => {
    await loadPicker();

    expect(group('conditions').hidden).toBe(false);
    expect(heading('conditions').getAttribute('aria-expanded')).toBe('true');
    expect(group('places').hidden).toBe(true);
    expect(group('basemap').hidden).toBe(true);
  });

  it('writes nothing while seeding from storage', async () => {
    // Reading a preference must never write one back — the same discipline
    // the downloads row's tri-state depends on next door.
    await loadPicker();

    expect(writes).toEqual([]);
  });

  it('toggles one section without touching its neighbours', async () => {
    await loadPicker();

    heading('places').click();

    expect(group('places').hidden).toBe(false);
    // The rows a collapsed heading does not govern must stay where they are.
    expect(group('conditions').hidden).toBe(false);
    expect(group('basemap').hidden).toBe(true);
  });

  it('persists each section under its own key', async () => {
    await loadPicker();

    heading('places').click();
    heading('conditions').click();

    expect(window.localStorage.getItem(SECTION_KEY('places'))).toBe('true');
    expect(window.localStorage.getItem(SECTION_KEY('conditions'))).toBe('false');
  });

  it('restores what the last visit left', async () => {
    window.localStorage.setItem(SECTION_KEY('places'), 'true');
    window.localStorage.setItem(SECTION_KEY('conditions'), 'false');
    await loadPicker();

    expect(group('places').hidden).toBe(false);
    expect(group('conditions').hidden).toBe(true);
  });

  it('re-measures the menu after a collapse toggle', async () => {
    // positionMenu writes `max-height` inline from the room the viewport
    // leaves, and the content it bounds has just changed height.
    await loadPicker();
    openMenu();
    const menu = document.getElementById('basemap-menu');
    menu.style.maxHeight = '';

    heading('places').click();

    expect(menu.style.maxHeight).not.toBe('');
  });
});

describe('the derived readout', () => {
  it('counts the overlay rows that are on, never the basemap radio', async () => {
    await loadPicker();
    expect(count()).toBe('No layers on');

    rowFor('weather').click();
    expect(count()).toBe('1 layer on');

    rowFor('resorts').click();
    expect(count()).toBe('2 layers on');
  });

  it('says "None selected" for a section with nothing on', async () => {
    await loadPicker();

    expect(summary('places')).toBe('None selected');
  });

  it('names the rows that are on, shortened from their own labels', async () => {
    await loadPicker();

    rowFor('country.ch').click();
    rowFor('weather').click();

    // The trailing parenthetical is stripped; nothing else is.
    expect(summary('conditions')).toBe('SLF bulletins, Weather');
  });

  it('strips the "Display" verb the two Basemap overlay rows carry', async () => {
    await loadPicker();

    rowFor('slope').click();

    expect(summary('basemap')).toBe('OpenFreeMap, slope angles');
  });

  it('names the radio section\'s own selection', async () => {
    await loadPicker();

    expect(summary('basemap')).toBe('OpenFreeMap');
  });

  it('falls back to a count when the names would not fit', async () => {
    await loadPicker();

    rowFor('slope').click();
    rowFor('downloads').click();

    // "OpenFreeMap, slope angles, downloaded areas" is past the line, so
    // the summary states the count rather than clipping a name mid-word.
    expect(summary('basemap')).toBe('3 of 4 on');
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
