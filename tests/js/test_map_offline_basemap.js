/*
 * tests/js/test_map_offline_basemap.js — the offline switch to Swisstopo on
 * the page (SNOW-1478, static/js/map_offline_basemap.js).
 *
 * What is pinned:
 *   - offline inside an area saved on Swisstopo, with OpenFreeMap picked,
 *     the map swaps to Swisstopo and the banner shows, without the
 *     reader's choice being written;
 *   - leaving 3-D, whose swap back to the reader's map is not a choice,
 *     switches to Swisstopo again (Codex, PR #1542);
 *   - a map picked while switched stands until the connection changes.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SWISSTOPO_URL = 'https://tiles.example.test/swisstopo/style.json';
const OFM_URL = 'https://tiles.example.test/ofm/style.json';

let listeners;
let map;

/** Build the page the module reads, then load it. */
async function boot() {
  document.body.innerHTML = `
    <div id="basemap-menu">
      <button class="basemap-menu-item" data-basemap-key="swisstopo_winter"
        data-basemap-url="${SWISSTOPO_URL}" aria-checked="false"></button>
      <button class="basemap-menu-item" data-basemap-key="openfreemap_liberty"
        data-basemap-url="${OFM_URL}" aria-checked="true"></button>
    </div>
    <div id="offline-basemap-banner" class="hidden"></div>`;
  document.documentElement.dataset.mapView = 'flat';
  listeners = {};
  map = {
    getCenter: () => ({ lng: 7.23, lat: 46.1 }),
    setStyle: vi.fn(),
    on: (name, fn) => {
      listeners[name] = fn;
    },
  };
  window.snowdeskMap = map;
  window.pwaConnectivity = { isOnline: () => false };
  window.pwaBasemapAreas = {
    downloadedAreas: async () => [{ bbox: [7.18, 46.05, 7.28, 46.15], keys: ['swisstopo_winter'] }],
  };
  window.pwaBasemapDownloadCore = {
    areaBBox: (area) => area.bbox,
    recordBasemaps: (area) => area.keys.map((key) => ({ key })),
  };
  vi.resetModules();
  await import('../../static/js/map_offline_basemap_core.js');
  await import('../../static/js/map_offline_basemap.js');
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** Let the style promise settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

const banner = () => document.getElementById('offline-basemap-banner');

describe('the offline switch to Swisstopo', () => {
  beforeEach(async () => {
    localStorage.clear();
    await boot();
    await settle();
  });

  afterEach(() => {
    delete window.pwaOfflineBasemap;
  });

  it('swaps to Swisstopo offline inside the area, and writes no choice', () => {
    expect(window.pwaOfflineBasemap.switched()).toBe(true);
    expect(map.setStyle).toHaveBeenLastCalledWith(SWISSTOPO_URL);
    expect(banner().classList.contains('hidden')).toBe(false);
    expect(localStorage.getItem('snowdesk.map.basemap')).toBeNull();
  });

  it('switches again on leaving 3-D, whose swap back is not a choice', async () => {
    document.documentElement.dataset.mapView = '3d';
    document.dispatchEvent(
      new CustomEvent('snowdesk:basemap-changing', {
        detail: { key: 'openfreemap_liberty', url: OFM_URL },
      }),
    );
    expect(window.pwaOfflineBasemap.switched()).toBe(false);
    document.documentElement.dataset.mapView = 'flat';
    document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail: { view: 'flat' } }));
    await settle();
    expect(window.pwaOfflineBasemap.switched()).toBe(true);
    expect(map.setStyle).toHaveBeenLastCalledWith(SWISSTOPO_URL);
  });

  it('honours a map picked while switched until the connection changes', async () => {
    document.dispatchEvent(
      new CustomEvent('snowdesk:basemap-changing', {
        detail: { key: 'openfreemap_liberty', url: OFM_URL, source: 'picker' },
      }),
    );
    listeners.moveend();
    expect(window.pwaOfflineBasemap.switched()).toBe(false);
    expect(banner().classList.contains('hidden')).toBe(true);
    document.dispatchEvent(new CustomEvent('snowdesk:connectivity-changed'));
    expect(window.pwaOfflineBasemap.switched()).toBe(true);
  });
});
