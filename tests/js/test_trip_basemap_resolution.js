/*
 * tests/js/test_trip_basemap_resolution.js — the trip page's own basemap
 * read (SNOW-829).
 *
 * `basemap_style_core.js` owns the arithmetic and is tested next door. This
 * file covers the half that touches the page: reading the catalogue the
 * template emitted, and combining it with `localStorage` and the
 * container's default key to decide which style the MapLibre constructor is
 * handed. Getting that wrong is a BLANK MAP rather than a thrown error, so
 * it is worth asserting directly.
 *
 * jsdom, not a browser: every read here is `getElementById` and
 * `localStorage`, both of which jsdom provides.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_markers_core.js';
import '../../static/js/basemap_style_core.js';
import '../../static/js/trip_map.js';

const core = self.pwaTripMapCore;

const CATALOGUE = {
  openfreemap_liberty: 'https://tiles.openfreemap.org/styles/liberty',
  swisstopo_winter: 'https://vectortiles.geo.admin.ch/styles/winter/style.json',
};

/** Render the page shape _trip_basemaps.html + _trip_map.html produce. */
function renderPage(catalogue, defaultKey) {
  document.body.innerHTML = '';
  if (catalogue) {
    const script = document.createElement('script');
    script.type = 'application/json';
    script.id = 'trip-basemaps';
    script.textContent = JSON.stringify(catalogue);
    document.body.appendChild(script);
  }
  const container = document.createElement('div');
  container.setAttribute('data-trip-map', '');
  container.setAttribute('data-default-basemap-key', defaultKey);
  document.body.appendChild(container);
  return container;
}

beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('readBasemaps', () => {
  it('reads the catalogue the page emitted', () => {
    renderPage(CATALOGUE, 'openfreemap_liberty');
    expect(core.readBasemaps(document)).toEqual(CATALOGUE);
  });

  it('returns null when the page emitted none', () => {
    renderPage(null, 'openfreemap_liberty');
    expect(core.readBasemaps(document)).toBeNull();
  });

  it('returns null for an unparseable catalogue rather than throwing', () => {
    renderPage(null, 'openfreemap_liberty');
    const script = document.createElement('script');
    // type matters: without it jsdom EXECUTES the body as JavaScript.
    script.type = 'application/json';
    script.id = 'trip-basemaps';
    script.textContent = 'not json';
    document.body.appendChild(script);
    expect(core.readBasemaps(document)).toBeNull();
  });
});

describe('resolveBasemapFor', () => {
  it('honours the choice the reader made on the map page', () => {
    // The whole point of the ticket: localStorage is scoped per ORIGIN, so
    // the key the map's picker writes is readable from a trip page.
    localStorage.setItem('snowdesk.map.basemap', 'swisstopo_winter');
    const container = renderPage(CATALOGUE, 'openfreemap_liberty');

    expect(core.resolveBasemapFor(container, document)).toEqual({
      key: 'swisstopo_winter',
      url: CATALOGUE.swisstopo_winter,
    });
  });

  it('falls back to the site default when nothing is stored', () => {
    const container = renderPage(CATALOGUE, 'openfreemap_liberty');

    expect(core.resolveBasemapFor(container, document)).toEqual({
      key: 'openfreemap_liberty',
      url: CATALOGUE.openfreemap_liberty,
    });
  });

  it('falls back for a stored key that is no longer in the catalogue', () => {
    localStorage.setItem('snowdesk.map.basemap', 'retired_basemap');
    const container = renderPage(CATALOGUE, 'openfreemap_liberty');

    expect(core.resolveBasemapFor(container, document).key).toBe(
      'openfreemap_liberty',
    );
  });

  it('falls back when the browser refuses to hand over storage', () => {
    // A browser set to block site data THROWS on access rather than
    // answering null, which would otherwise take the whole map with it.
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('access denied');
    });
    const container = renderPage(CATALOGUE, 'openfreemap_liberty');

    expect(core.resolveBasemapFor(container, document).key).toBe(
      'openfreemap_liberty',
    );
  });

  it('returns null when the page carries no catalogue at all', () => {
    const container = renderPage(null, 'openfreemap_liberty');
    expect(core.resolveBasemapFor(container, document)).toBeNull();
  });
});
