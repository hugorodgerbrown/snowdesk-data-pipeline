/*
 * tests/js/test_map_3d_terrain.js — MapLibre terrain on a map, shared by
 * /map/3d/ and /map/'s 3-D view (SNOW-1449).
 *
 * Pinned: the elevation protocol is registered once per MapLibre whoever
 * asks; the install adds the source, the terrain, the sky and the sunlight
 * under the first label once, and puts back only what a setStyle wiped;
 * the removal keeps the source; the prefetch fetches once per view.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/map_3d_core.js';
import '../../static/js/map_3d_terrain.js';

const terrain = window.pwaMap3dTerrain;
const core = window.pwaMap3dCore;

/** A map that keeps what it is given, with a basemap of two layers. */
function fakeMap() {
  const sources = {};
  let layers = [{ id: 'relief', type: 'raster' }, { id: 'labels', type: 'symbol' }];
  let spec = null;
  const map = {
    addSource: vi.fn((id, source) => { sources[id] = source; }),
    getSource: (id) => sources[id],
    addLayer: vi.fn((layer, before) => {
      const at = before ? layers.findIndex((l) => l.id === before) : layers.length;
      layers.splice(at, 0, layer);
    }),
    getLayer: (id) => layers.find((l) => l.id === id),
    removeLayer: vi.fn((id) => { layers = layers.filter((l) => l.id !== id); }),
    getStyle: () => ({ layers }),
    setTerrain: vi.fn((t) => { spec = t; }),
    getTerrain: () => spec,
    setSky: vi.fn(),
    layerIds: () => layers.map((l) => l.id),
    wipe: () => {
      for (const id of Object.keys(sources)) delete sources[id];
      layers = [{ id: 'labels', type: 'symbol' }];
      spec = null;
    },
  };
  return map;
}

describe('registerDemProtocol', () => {
  it('registers map3ddem once per MapLibre', () => {
    const maplibre = { addProtocol: vi.fn() };
    expect(terrain.registerDemProtocol(maplibre, 'https://t.example/{z}/{x}/{y}.webp')).toBe(true);
    expect(terrain.registerDemProtocol(maplibre, 'https://t.example/{z}/{x}/{y}.webp')).toBe(false);
    expect(maplibre.addProtocol).toHaveBeenCalledTimes(1);
    expect(maplibre.addProtocol.mock.calls[0][0]).toBe(core.DEM_PROTOCOL);
  });

  it('answers a tile from the primary template', async () => {
    const maplibre = { addProtocol: vi.fn() };
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true, status: 200, arrayBuffer: () => Promise.resolve('dem') }));
    window.fetch = fetchSpy;
    terrain.registerDemProtocol(maplibre, 'https://t.example/{z}/{x}/{y}.webp');
    const handler = maplibre.addProtocol.mock.calls[0][1];
    await expect(handler({ url: 'map3ddem://9/266/180' }, new AbortController())).resolves.toEqual({ data: 'dem' });
    expect(fetchSpy.mock.calls[0][0]).toBe('https://t.example/9/266/180.webp');
    expect(fetchSpy.mock.calls[0][1].credentials).toBe('omit');
    await expect(handler({ url: 'https://elsewhere' }, new AbortController())).rejects.toThrow();
  });
});

describe('installTerrain', () => {
  it('adds the source, the terrain, the sky and the sunlight under the first label', () => {
    const map = fakeMap();
    terrain.installTerrain(map);
    expect(map.getSource(core.DEM_SOURCE_ID)).toEqual(core.demSource());
    expect(map.getTerrain()).toEqual(core.terrainSpec());
    expect(map.setSky).toHaveBeenCalledWith(core.skySpec());
    expect(map.layerIds()).toEqual(['relief', terrain.SUNLIGHT_LAYER, 'labels']);
    expect(terrain.hasTerrain(map)).toBe(true);
  });

  it('is idempotent', () => {
    const map = fakeMap();
    terrain.installTerrain(map);
    terrain.installTerrain(map);
    expect(map.addSource).toHaveBeenCalledTimes(1);
    expect(map.setTerrain).toHaveBeenCalledTimes(1);
    expect(map.addLayer).toHaveBeenCalledTimes(1);
  });

  it('puts back what a setStyle wiped', () => {
    const map = fakeMap();
    terrain.installTerrain(map);
    map.wipe();
    terrain.installTerrain(map);
    expect(map.addSource).toHaveBeenCalledTimes(2);
    expect(terrain.hasTerrain(map)).toBe(true);
    expect(map.layerIds()).toEqual([terrain.SUNLIGHT_LAYER, 'labels']);
  });
});

describe('removeTerrain', () => {
  it('lays the map flat and keeps the source for a cheap return', () => {
    const map = fakeMap();
    terrain.installTerrain(map);
    terrain.removeTerrain(map);
    expect(map.getTerrain()).toBeNull();
    expect(map.getLayer(terrain.SUNLIGHT_LAYER)).toBeUndefined();
    expect(map.getSource(core.DEM_SOURCE_ID)).toBeDefined();
    terrain.removeTerrain(map);
    expect(map.setTerrain).toHaveBeenCalledTimes(2);
    terrain.installTerrain(map);
    expect(map.addSource).toHaveBeenCalledTimes(1);
  });
});

describe('createPrefetcher', () => {
  it('fetches the 3x3 block once per view, and nothing without a template', () => {
    const fetchSpy = vi.fn(() => Promise.resolve({ ok: true }));
    window.fetch = fetchSpy;
    const prefetch = terrain.createPrefetcher('https://t.example/{z}/{x}/{y}.webp');
    expect(prefetch({ lng: 7.5, lat: 46.2 }, 11)).toBe(9);
    expect(prefetch({ lng: 7.5, lat: 46.2 }, 11)).toBe(0);
    expect(fetchSpy).toHaveBeenCalledTimes(9);
    expect(prefetch({ lng: 8.5, lat: 46.2 }, 11)).toBe(9);
    expect(terrain.createPrefetcher('')({ lng: 7.5, lat: 46.2 }, 11)).toBe(0);
  });
});
