/*
 * tests/js/test_beyond_switzerland_core.js — the winter style spliced to
 * draw past the Swiss border (SNOW-1460, static/js/beyond_switzerland_core.js).
 *
 * The style below is a cut of swisstopo's winter style (v1.26.0, 8 Nov
 * 2026): one layer of each kind the splice treats differently.
 *
 * What is pinned:
 *   - the plan adds the OSM and relief sources and every copy once, and a
 *     style already spliced gets no plan;
 *   - a style that is not the winter one (the light twin, OpenFreeMap) is
 *     left alone;
 *   - every OSM layer reads the `osmclip://` source, sits just above its
 *     original, and only base.vt layers on OpenMapTiles source-layers are
 *     copied;
 *   - the relief sits above the hillshade, under the scree;
 *   - OpenMapTiles' classes are fixed: peaks by height, roads' is_route,
 *     lifts' subclass, the glacier left to its own fill, the wood, glacier
 *     and rock fills, the scree pattern on rock that is not bare rock, and
 *     the Swiss border left out;
 *   - the Alpine gate.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/beyond_switzerland_core.js';

const core = window.pwaBeyondSwitzerlandCore;
const BASE = 'base_v1.0.0';
const RELIEF = 'relief_v1.0.0';

/** A cut of the winter style. */
function winterStyle() {
  return {
    version: 8,
    name: 'basemap-winter_v1.19.0',
    sprite: 'https://vectortiles.geo.admin.ch/styles/ch.swisstopo.basemap-winter.vt/v1.26.0/sprite/sprite',
    sources: {
      [RELIEF]: { type: 'vector', url: 'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.relief.vt/v1.0.0/tiles.json' },
      [BASE]: { type: 'vector', url: 'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.base.vt/v1.0.0/tiles.json' },
    },
    layers: [
      { id: 'background', type: 'background' },
      { id: 'hillshade_grey', type: 'fill', source: RELIEF, 'source-layer': 'hillshade' },
      { id: 'scree_z13', type: 'fill', source: RELIEF, 'source-layer': 'scree', paint: { 'fill-pattern': ['match', ['get', 'weight'], 15, 'scree_medium_1', 10, 'scree_medium_2', ''] } },
      { id: 'contour_line', type: 'line', source: BASE, 'source-layer': 'contour_line' },
      { id: 'landcover', type: 'fill', source: BASE, 'source-layer': 'landcover', filter: ['in', 'subclass', 'forest', 'glacier', 'park'] },
      { id: 'hachure', type: 'fill', source: RELIEF, 'source-layer': 'hachure' },
      { id: 'boundary', type: 'line', source: BASE, 'source-layer': 'boundary', filter: ['all', ['in', 'admin_level', 2, 4]] },
      {
        id: 'road_fill', type: 'line', source: BASE, 'source-layer': 'transportation',
        paint: { 'line-width': ['interpolate', ['linear'], ['zoom'], 9, ['match', ['get', 'is_route'], [99], 2, 0]] },
      },
      {
        id: 'aerialway', type: 'line', source: BASE, 'source-layer': 'transportation',
        filter: ['match', ['get', 'class'], ['gondola', 'chair_lift'], true, false],
        paint: { 'line-width': ['case', ['has', 'is_route'], 1, 0] },
      },
      {
        id: 'peak_rank1', type: 'symbol', source: BASE, 'source-layer': 'mountain_peak',
        layout: { 'icon-image': ['match', ['get', 'class'], ['alpine_peak', 'peak'], 'mountain', 'hill'] },
      },
      { id: 'place_city', type: 'symbol', source: BASE, 'source-layer': 'place', filter: ['==', 'class', 'city'] },
    ],
  };
}

const ids = (plan) => plan.layers.map(({ layer }) => layer.id);
const byId = (plan, id) => plan.layers.find(({ layer }) => layer.id === id);

describe('which styles are spliced', () => {
  it('splices the winter style once', () => {
    const style = winterStyle();
    const plan = core.plan(style);
    expect(Object.keys(plan.sources)).toEqual([core.OSM_SOURCE_ID, core.RELIEF_SOURCE_ID]);
    expect(new Set(ids(plan)).size).toBe(plan.layers.length);
    const spliced = { ...style, sources: { ...style.sources, ...plan.sources } };
    expect(core.isSpliced(spliced)).toBe(true);
    expect(core.plan(spliced)).toBeNull();
  });

  it('leaves the light twin and any other style alone', () => {
    const light = { ...winterStyle(), name: 'lightbasemap', sprite: 'https://vectortiles.geo.admin.ch/styles/ch.swisstopo.lightbasemap.vt/sprite/sprite' };
    expect(core.plan(light)).toBeNull();
    expect(core.plan({ version: 8, name: 'Liberty', sources: {}, layers: [] })).toBeNull();
    expect(core.plan(null)).toBeNull();
  });
});

describe('the OSM copies', () => {
  const plan = core.plan(winterStyle());

  it('copy only base.vt layers on OpenMapTiles source-layers', () => {
    expect(ids(plan)).toEqual([
      core.RELIEF_LAYER_ID,
      'beyond-scree_z13',
      'beyond-landcover', 'beyond-wood', 'beyond-glacier', 'beyond-rock',
      'beyond-boundary',
      'beyond-road_fill',
      'beyond-aerialway',
      'beyond-peak_rank1',
      'beyond-place_city',
    ]);
  });

  it('point every OSM layer at the osmclip:// source', () => {
    expect(plan.sources[core.OSM_SOURCE_ID].tiles).toEqual(['osmclip://{z}/{x}/{y}']);
    expect(plan.sources[core.OSM_SOURCE_ID].attribution).toContain('OpenStreetMap contributors');
    for (const { layer } of plan.layers) {
      if (layer.id === core.RELIEF_LAYER_ID) continue;
      expect(layer.source).toBe(core.OSM_SOURCE_ID);
    }
  });

  it('sit just above their originals', () => {
    expect(byId(plan, 'beyond-landcover').beforeId).toBe('hachure');
    expect(byId(plan, 'beyond-road_fill').beforeId).toBe('aerialway');
    expect(byId(plan, 'beyond-place_city').beforeId).toBeNull();
  });

  it('put the relief above the hillshade, under the scree', () => {
    const relief = byId(plan, core.RELIEF_LAYER_ID);
    expect(relief.beforeId).toBe('scree_z13');
    expect(relief.layer).toMatchObject({ type: 'raster', source: core.RELIEF_SOURCE_ID });
    expect(plan.sources[core.RELIEF_SOURCE_ID].tiles).toEqual(['alpinerelief://{z}/{x}/{y}']);
  });
});

describe('the class fixes', () => {
  const plan = core.plan(winterStyle());
  const layer = (id) => byId(plan, id).layer;
  const text = (id) => JSON.stringify(layer(id));

  it('class peaks by height', () => {
    expect(text('beyond-peak_rank1')).toContain('"alpine_peak"');
    expect(text('beyond-peak_rank1')).toContain('3000');
  });

  it('read is_route from the road class, and lifts from their subclass', () => {
    expect(text('beyond-road_fill')).not.toContain('["get","is_route"]');
    expect(text('beyond-road_fill')).toContain('"motorway"');
    expect(text('beyond-aerialway')).not.toContain('["has","is_route"]');
    expect(JSON.stringify(layer('beyond-aerialway').filter)).toContain('"aerialway"');
    expect(JSON.stringify(layer('beyond-aerialway').filter)).toContain('["get","subclass"]');
  });

  it('leave glaciers to their own fill, and add wood and rock', () => {
    expect(layer('beyond-landcover').filter).toEqual(['in', 'subclass', 'forest', 'park']);
    expect(layer('beyond-glacier').filter).toEqual(['==', ['get', 'class'], 'ice']);
    expect(layer('beyond-wood').filter).toEqual(['all', ['==', ['get', 'class'], 'wood'], ['!=', ['get', 'subclass'], 'forest']]);
    expect(layer('beyond-rock').filter).toEqual(['==', ['get', 'class'], 'rock']);
  });

  it('draw swisstopo\'s scree pattern on rock that is not bare rock', () => {
    const scree = layer('beyond-scree_z13');
    expect(scree['source-layer']).toBe('landcover');
    expect(scree.filter).toEqual(['all', ['==', ['get', 'class'], 'rock'], ['!=', ['get', 'subclass'], 'bare_rock']]);
    expect(scree.paint['fill-pattern']).toEqual(['match', 10, 15, 'scree_medium_1', 10, 'scree_medium_2', '']);
  });

  it('leave out the Swiss and Liechtenstein border', () => {
    expect(layer('beyond-boundary').filter).toEqual([
      'all', ['all', ['in', 'admin_level', 2, 4]], ['!in', 'adm0_l', 'CHE', 'LIE'], ['!in', 'adm0_r', 'CHE', 'LIE'],
    ]);
  });

  it('leave the winter style\'s own layers untouched', () => {
    const style = winterStyle();
    const before = JSON.stringify(style);
    core.plan(style);
    expect(JSON.stringify(style)).toBe(before);
  });
});

describe('the Alpine gate', () => {
  it('is a closed ring over the Alps, leaving out the Pyrenees and Corsica', () => {
    const gate = core.ALPINE_GATE;
    expect(gate[0]).toEqual(gate[gate.length - 1]);
    const lngs = gate.map(([lng]) => lng);
    const lats = gate.map(([, lat]) => lat);
    expect(Math.min(...lngs)).toBe(5.0);
    expect(Math.max(...lngs)).toBe(16.6);
    expect(Math.min(...lats)).toBe(43.7);
    expect(Math.max(...lats)).toBe(48.3);
    expect(Object.isFrozen(gate)).toBe(true);
  });
});
