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
 *   - the rest zones' three rows split on `binding` and `in_force`, and a
 *     pinned resort's row on the reader's pins;
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
      'danger', 'problem_terrain', 'community_reports', 'weather',
      'terrain_filter', 'ski_tours', 'rest_zones', 'routes', 'pins',
      'boundaries', 'location', 'downloads',
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

  it('keeps the danger scale for problem slopes, which are painted in its colours', () => {
    const state = core.legendState({ bulletins: false, problemTerrain: true });
    expect(state.keys.danger).toBe(true);
    expect(state.keys.problem_terrain).toBe(true);
  });

  it('marks a switched-on layer On with nothing of it in view or at this zoom', () => {
    const { on, keys } = core.legendState({
      switchedOn: { rest_zones: true, community_reports: true, resorts: true },
      problemTerrainOn: true,
      terrainFilterOn: true,
      rendered: {},
    });
    expect(on).toMatchObject({
      rest_zones: true, community_reports: true, pins: true,
      problem_terrain: true, terrain_filter: true,
    });
    expect(keys.rest_zones).toBe(false);
    expect(keys.problem_terrain).toBe(false);
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

  it('keys a pinned resort only when one is in view, favourites on their own', () => {
    const resorts = core.legendState({ rendered: { resorts: [feature({ id: 'verbier' })] } });
    expect(resorts.keys.pins).toBe(true);
    expect(resorts.items).toMatchObject({
      'pin-resort': true, 'pin-pinned': false, 'pin-favourite': false,
    });
    const pinnedOnly = core.legendState({
      isPinnedResort: (slug) => slug === 'verbier',
      rendered: { resorts: [feature({ id: 'verbier' })] },
    });
    expect(pinnedOnly.items).toMatchObject({ 'pin-resort': false, 'pin-pinned': true });
    const pins = core.legendState({ rendered: { favourites: [feature({})] } });
    expect(pins.items).toMatchObject({
      'pin-resort': false, 'pin-pinned': false, 'pin-favourite': true,
    });
  });

  it('keys a ski tour\'s way up and down, and summits apart from huts', () => {
    const { keys, items } = core.legendState({
      rendered: {
        ski_tours: [feature({ climbing: null })],
        ski_marks: [feature({ kind: 'hut' })],
      },
    });
    expect(keys.ski_tours).toBe(true);
    // A tour with no terrain yet is drawn as the way up.
    expect(items).toMatchObject({
      'tour-up': true, 'tour-down': false, 'tour-summit': false, 'tour-hut': true,
    });
  });

  it('keys the weather symbols and the position dot when in view', () => {
    expect(core.legendState({ rendered: { weather: [feature({})] } }).keys.weather).toBe(true);
    expect(core.legendState({ location: true }).keys.location).toBe(true);
    expect(core.legendState({}).keys.location).toBe(false);
  });

  it('shows a boundary row per tier in view, and the key with any', () => {
    const { keys, items } = core.legendState({
      rendered: { boundary_minor: [feature({})], boundary_grouping: [feature({})] },
    });
    expect(keys.boundaries).toBe(true);
    expect(items).toMatchObject({
      'boundary-major': false, 'boundary-minor': true, 'boundary-micro': false,
      'boundary-unsupported': false, 'boundary-grouping': true,
    });
  });

  it('does not mark boundaries On for the bulletins\' grouping outline alone', () => {
    const { on } = core.legendState({ switchedOn: { boundary_grouping: true } });
    expect(on.boundaries).toBe(false);
    expect(core.legendState({ switchedOn: { boundary_micro: true } }).on.boundaries).toBe(true);
  });

  it('keys a route by its legs, its flat line, its slope colours and its marks', () => {
    const { keys, items } = core.legendState({
      routeLegsOn: true,
      rendered: { route_line: [feature({})], route_ends: [feature({})] },
    });
    expect(keys.routes).toBe(true);
    expect(items).toMatchObject({
      'route-flat': true, 'route-climb': false, 'route-descent': false,
      'route-slope': false, 'route-transition': false, 'route-ends': true,
    });
    const legs = core.legendState({
      routeLegsOn: true,
      rendered: { routes: [feature({ climbing: true })], route_slope: [feature({})] },
    }).items;
    expect(legs).toMatchObject({ 'route-climb': true, 'route-descent': false, 'route-slope': true });
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
  it('names each provider once, without the product', () => {
    expect(core.shortSources(['© swisstopo · swisstopo — swissALTI3D · © BAFU']))
      .toBe('© swisstopo · BAFU');
  });

  it('reads separate strings alike', () => {
    expect(core.shortSources(['© OpenStreetMap', 'OpenFreeMap · © OpenMapTiles']))
      .toBe('© OpenStreetMap · OpenFreeMap · OpenMapTiles');
  });

  it('is empty when nothing is credited', () => {
    expect(core.shortSources([])).toBe('');
    expect(core.shortSources([''])).toBe('');
  });
});
