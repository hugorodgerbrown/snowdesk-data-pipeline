/*
 * tests/js/test_map_offline_basemap_core.js — when the map shows Swisstopo
 * offline in place of the reader's own map (SNOW-1478,
 * static/js/map_offline_basemap_core.js).
 *
 * What is pinned:
 *   - offline inside an area saved on Swisstopo, with another map on
 *     screen, the map switches;
 *   - online, outside every such area, on Swisstopo already, or with no
 *     map resolved, it stays;
 *   - once switched it stays while offline and inside, and restores on a
 *     connection or on leaving;
 *   - a swap superseded by a newer one is not current.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_offline_basemap_core.js';

const core = window.pwaOfflineBasemapCore;
const area = { bbox: [7.18, 46.05, 7.28, 46.15], keys: ['swisstopo_winter', 'openfreemap_liberty'] };
const inside = { lng: 7.23, lat: 46.1 };
const outside = { lng: 9.5, lat: 46.9 };
const base = {
  online: false,
  centre: inside,
  activeKey: 'openfreemap_liberty',
  switched: false,
  areas: [area],
};

describe('offlineDecision', () => {
  it('switches offline inside an area saved on Swisstopo', () => {
    expect(core.offlineDecision(base)).toBe('switch');
  });

  it('stays online, outside, on Swisstopo already, or with no map', () => {
    expect(core.offlineDecision({ ...base, online: true })).toBe('stay');
    expect(core.offlineDecision({ ...base, centre: outside })).toBe('stay');
    expect(core.offlineDecision({ ...base, centre: null })).toBe('stay');
    expect(core.offlineDecision({ ...base, activeKey: 'swisstopo_winter' })).toBe('stay');
    expect(core.offlineDecision({ ...base, activeKey: null })).toBe('stay');
  });

  it('stays inside an area that holds no Swisstopo map', () => {
    expect(
      core.offlineDecision({ ...base, areas: [{ ...area, keys: ['openfreemap_liberty'] }] }),
    ).toBe('stay');
    expect(core.offlineDecision({ ...base, areas: [] })).toBe('stay');
  });

  it('stays switched offline and inside, and restores on signal or on leaving', () => {
    expect(core.offlineDecision({ ...base, switched: true })).toBe('stay');
    expect(core.offlineDecision({ ...base, switched: true, online: true })).toBe('restore');
    expect(core.offlineDecision({ ...base, switched: true, centre: outside })).toBe('restore');
  });
});

describe('swapGuard', () => {
  it('drops a swap that a newer one has superseded', () => {
    const guard = core.swapGuard();
    const first = guard.next();
    expect(guard.isCurrent(first)).toBe(true);
    const second = guard.next();
    expect(guard.isCurrent(first)).toBe(false);
    expect(guard.isCurrent(second)).toBe(true);
  });
});

describe('covers', () => {
  it('reads a point inside a box, edges included', () => {
    expect(core.covers(inside, area.bbox)).toBe(true);
    expect(core.covers({ lng: 7.18, lat: 46.05 }, area.bbox)).toBe(true);
    expect(core.covers(outside, area.bbox)).toBe(false);
    expect(core.covers(inside, null)).toBe(false);
  });
});
