/*
 * tests/js/test_map_legend_core.js — which keys the legend's first tier
 * shows for a view (static/js/map_legend_core.js).
 *
 * What is pinned, because each is the way this goes wrong:
 *   - a key needs its layer on AND something of it in view — the danger
 *     key in particular reads the bulletins' own switch, because the
 *     regions' fill is in the style (and queryable) whenever a boundary
 *     tier is drawn;
 *   - a row inside a key follows the marks in view, and a cluster stands
 *     for every row, since it hides which pins it holds;
 *   - the rest zones' three rows split on `binding` and `in_force`;
 *   - `on` (the second tier's mark) ignores the view, `keys` does not;
 *   - the credit line names each provider once.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

beforeAll(async () => {
  await import('../../static/js/map_legend_core.js');
  core = self.pwaMapLegendCore;
});

const feature = (properties) => ({ properties });

describe('KEYS and LAYERS', () => {
  it('lists the day\'s keys first, then every day\'s', () => {
    expect(core.KEYS).toEqual([
      'danger', 'problem_terrain', 'community_reports', 'terrain_filter',
      'rest_zones', 'routes', 'pins', 'downloads',
    ]);
  });

  it('reads the danger key from the edge band and labels as well as the fill', () => {
    expect(core.LAYERS.danger).toEqual(['regions-fill', 'regions-edge', 'regions-level-label']);
  });
});

describe('legendState', () => {
  it('shows nothing for an empty view', () => {
    const { keys } = core.legendState({});
    expect(Object.values(keys).some(Boolean)).toBe(false);
  });

  it('needs the bulletins on for the danger key, whatever the regions answer', () => {
    const rendered = { danger: [feature({})] };
    expect(core.legendState({ bulletins: false, rendered }).keys.danger).toBe(false);
    expect(core.legendState({ bulletins: true, rendered }).keys.danger).toBe(true);
    expect(core.legendState({ bulletins: true, rendered: {} }).keys.danger).toBe(false);
  });

  it('marks a layer on in the second tier even with nothing of it in view', () => {
    const state = core.legendState({ bulletins: true, routeLegsOn: true, rendered: {} });
    expect(state.on.danger).toBe(true);
    expect(state.on.routes).toBe(true);
    expect(state.keys.danger).toBe(false);
    expect(state.keys.routes).toBe(false);
  });

  it('shows a field report family and state only when one is in view', () => {
    const { keys, items } = core.legendState({
      rendered: { community_reports: [feature({ family: 'SURFACE', _state: 'recent' })] },
    });
    expect(keys.community_reports).toBe(true);
    expect(items['report-SURFACE']).toBe(true);
    expect(items['report-WARNING']).toBe(false);
    expect(items['report-TRAIL']).toBe(false);
    expect(items['report-recent']).toBe(true);
    expect(items['report-live']).toBe(false);
  });

  it('treats a report with no family as a warning sign, as the layer does', () => {
    const { items } = core.legendState({
      rendered: { community_reports: [feature({ _state: 'live' })] },
    });
    expect(items['report-WARNING']).toBe(true);
  });

  it('shows every report row for a cluster, which hides its pins', () => {
    const { items } = core.legendState({
      rendered: { community_reports: [feature({ point_count: 4 })] },
    });
    for (const row of ['WARNING', 'SURFACE', 'TRAIL', 'live', 'recent']) {
      expect(items['report-' + row]).toBe(true);
    }
  });

  it('splits the rest zones into by law, advised and out of season', () => {
    const view = (zones) => core.legendState({ rendered: { rest_zones: zones } }).items;
    expect(view([feature({ binding: true, in_force: true })])).toMatchObject({
      'rest-binding': true, 'rest-recommended': false, 'rest-out-of-season': false,
    });
    expect(view([feature({ binding: false, in_force: true })])).toMatchObject({
      'rest-binding': false, 'rest-recommended': true, 'rest-out-of-season': false,
    });
    expect(view([feature({ binding: true, in_force: false })])).toMatchObject({
      'rest-binding': false, 'rest-recommended': false, 'rest-out-of-season': true,
    });
  });

  it('shows resorts and pinned resorts together, favourites on their own', () => {
    const resorts = core.legendState({ rendered: { resorts: [feature({})] } });
    expect(resorts.keys.pins).toBe(true);
    expect(resorts.items).toMatchObject({
      'pin-resort': true, 'pin-pinned': true, 'pin-favourite': false,
    });
    const pins = core.legendState({ rendered: { favourites: [feature({})] } });
    expect(pins.items).toMatchObject({
      'pin-resort': false, 'pin-pinned': false, 'pin-favourite': true,
    });
  });

  it('needs a leg in view as well as the routes key on', () => {
    const legs = { routes: [feature({})] };
    expect(core.legendState({ routeLegsOn: false, rendered: legs }).keys.routes).toBe(false);
    expect(core.legendState({ routeLegsOn: true, rendered: legs }).keys.routes).toBe(true);
  });

  it('passes the terrain and download keys through as their modules decide', () => {
    const { keys } = core.legendState({ problemTerrain: true, terrainFilter: true, downloads: true });
    expect(keys.problem_terrain).toBe(true);
    expect(keys.terrain_filter).toBe(true);
    expect(keys.downloads).toBe(true);
  });
});

describe('shortSources', () => {
  it('names each provider once, without the product or its link', () => {
    const html = '<a href="https://www.swisstopo.admin.ch" target="_blank">© swisstopo</a>'
      + ' · swisstopo — swissALTI3D · © BAFU';
    expect(core.shortSources([html])).toBe('© swisstopo · BAFU');
  });

  it('reads entities and separate strings alike', () => {
    expect(core.shortSources(['&copy; OpenStreetMap', 'OpenFreeMap &middot; © OpenMapTiles']))
      .toBe('© OpenStreetMap · OpenFreeMap · OpenMapTiles');
  });

  it('is empty when nothing is credited', () => {
    expect(core.shortSources([])).toBe('');
    expect(core.shortSources([''])).toBe('');
  });
});
