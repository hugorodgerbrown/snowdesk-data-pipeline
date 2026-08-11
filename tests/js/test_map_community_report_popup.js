/*
 * tests/js/test_map_community_report_popup.js — what a community-report pin
 * says when you tap it.
 *
 * The popup is the field-observation panel's row in popup form: the type as
 * a bold label, then "<region> · <age>" — the same two fields, in the same
 * order, worded the same way. It got there by being wrong twice. It carried
 * no region at all (the pin's position was held to say where the report
 * was), and its age came from a formatter of its own that said "5 h ago"
 * where the panel said "5 hours ago" — for a while against a timestamp the
 * server had floored to a quarter hour, so the two also disagreed on the
 * number.
 *
 * Booting map.js in jsdom follows tests/js/test_map_detail_popup_exclusivity.js's
 * pattern — see its header for the rationale.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Fixed "now", so the age under test is not a moving target. */
const NOW = new Date('2026-08-11T11:20:00Z');

/** The pin every tap in this suite lands on. */
const REPORT_FEATURE = {
  layer: { id: 'community-reports-point' },
  geometry: { type: 'Point', coordinates: [8.29, 46.65] },
  properties: {
    type: 'WHUMPFING',
    type_label: 'Whumpfing',
    // 29 seconds before NOW — the case that exposed the old formatter.
    observed_at: '2026-08-11T11:19:31+00:00',
    region_name: 'Guttannen',
  },
};

/** Every DOM node handed to a popup, newest last. */
const popupNodes = [];

/** Minimal MapLibre stub: enough for map.js to boot, a hit-test that always
 * finds the report pin, and a Popup that keeps the node it was given.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (id === 'community-reports-point' ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => [],
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [REPORT_FEATURE],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: (node) => { popupNodes.push(node); return popup; },
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;

/** Tap the map where the report pin is, and return the popup's content.
 *
 * @returns {HTMLElement} The node the popup was given.
 */
function tapTheReportPin() {
  for (const handler of mapStub.handlers.click || []) {
    handler({ point: { x: 10, y: 10 }, originalEvent: { target: document.body } });
  }
  return popupNodes.at(-1);
}

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // The words the age is rendered in. home.html loads this after the map
  // bundle, exactly as here.
  await import('../../static/js/relative_time.js');
  for (const handler of mapStub.handlers.load || []) await handler();
});

describe('the community-report pin popup', () => {
  it('reads "<region> · <age>" under the observation type', () => {
    vi.setSystemTime(NOW);

    const node = tapTheReportPin();

    expect(node.querySelector('.community-report-popup__type').textContent).toBe(
      'Whumpfing',
    );
    // The panel row's format, to the separator.
    expect(node.querySelector('.community-report-popup__meta').textContent).toBe(
      'Guttannen · 29 seconds ago',
    );
  });

  it('puts the age in a <time> relative_time.js keeps current', () => {
    vi.setSystemTime(NOW);

    const node = tapTheReportPin();
    const timeEl = node.querySelector('time[data-relative-time]');

    expect(timeEl.getAttribute('datetime')).toBe(REPORT_FEATURE.properties.observed_at);

    // MapLibre mounts the node; the stub above only records it, so stand in
    // for the mount — relative_time.js scans the document.
    document.body.appendChild(node);

    // A popup left open while the user reads the map must not freeze at the
    // age it was tapped with.
    vi.setSystemTime(new Date('2026-08-11T15:20:00Z'));
    window.pwaRelativeTime.refresh(document);

    expect(timeEl.textContent).toBe('4 hours ago');
  });
});
