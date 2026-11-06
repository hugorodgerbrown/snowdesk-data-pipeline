/*
 * tests/js/test_map_view_core.js — the map's flat and 3D views' pure rules
 * (SNOW-1449, SNOW-1455, SNOW-1452).
 *
 * What is pinned:
 *   - the URL: 3D from ?view=3d in either mode, flat spelt by leaving the
 *     parameter out, every other parameter kept;
 *   - every availability reason, in order, with the coverage box passed in
 *     and each of its four edges inside, and the device's three reasons
 *     asked first; the mode is never a reason (SNOW-1452);
 *   - the device gate: no WebGL, or 2 GB or less on Android;
 *   - the basemap: swisstopo winter in 3D, the reader's own otherwise, and
 *     the preference never written;
 *   - the camera: a stored pitch boots flat, the bearing kept.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_view_core.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/terrain_filter_core.js';

const core = window.pwaMapViewCore;
const { FLAT, THREE_D } = core.VIEWS;
const BOUNDS = window.pwaTerrainFilterCore.COVERAGE_BOUNDS;
const [WEST, SOUTH, EAST, NORTH] = BOUNDS;
const ZERMATT = { lng: 7.75, lat: 46.02 };

/** An input where 3D is available, with `over` changed. */
const input = (over = {}) => ({
  online: true,
  zoom: 12,
  editing: false,
  centre: ZERMATT,
  bounds: BOUNDS,
  ...over,
});

describe('the view in the URL', () => {
  it('reads 3D from ?view=3d in either mode', () => {
    expect(core.readView('?view=3d&mode=terrain')).toBe(THREE_D);
    expect(core.readView('view=3d')).toBe(THREE_D);
    expect(core.readView('?view=3d&d=2026-02-10')).toBe(THREE_D);
    expect(core.readView('?mode=terrain')).toBe(FLAT);
    expect(core.readView('?view=tilted')).toBe(FLAT);
    expect(core.readView('')).toBe(FLAT);
  });

  it('reads an unknown value as flat', () => {
    expect(core.parseView('3d')).toBe(THREE_D);
    expect(core.parseView('3D')).toBe(FLAT);
    expect(core.parseView(null)).toBe(FLAT);
  });

  it('writes 3D beside the other parameters, and flat by leaving it out', () => {
    expect(core.withView('?mode=terrain&d=2026-02-10', THREE_D)).toBe('?mode=terrain&d=2026-02-10&view=3d');
    expect(core.withView('?mode=terrain&view=3d', FLAT)).toBe('?mode=terrain');
    expect(core.withView('?view=3d', FLAT)).toBe('');
    expect(core.withView('', 'bogus')).toBe('');
  });
});

describe('availability', () => {
  it('is available online, in Switzerland, from z9', () => {
    expect(core.availability(input())).toEqual({ enabled: true, reason: null });
    expect(core.availability(input({ zoom: core.MIN_ZOOM }))).toEqual({ enabled: true, reason: null });
  });

  it('is available in Conditions as in Terrain: the mode is not a reason', () => {
    expect(core.availability(input({ mode: 'dated' }))).toEqual({ enabled: true, reason: null });
    expect(core.availability(input({ mode: 'terrain' }))).toEqual({ enabled: true, reason: null });
    expect(Object.values(core.REASONS)).not.toContain('mode');
  });

  it.each([
    ['device', { gated: true }],
    ['slow', { tooSlow: true }],
    ['webgl', { contextLost: true }],
    ['offline', { online: false }],
    ['editing', { editing: true }],
    ['coverage', { centre: { lng: 2.35, lat: 48.86 } }],
    ['zoom', { zoom: 8.99 }],
  ])('says %s', (reason, over) => {
    expect(core.availability(input(over))).toEqual({ enabled: false, reason });
  });

  it('asks the reasons in order: offline, editing, coverage, zoom', () => {
    const all = { online: false, editing: true, centre: { lng: 0, lat: 0 }, zoom: 4 };
    expect(core.availability(input(all)).reason).toBe('offline');
    expect(core.availability(input({ ...all, online: true })).reason).toBe('editing');
    expect(core.availability(input({ zoom: 4, centre: { lng: 0, lat: 0 } })).reason).toBe('coverage');
  });

  // SNOW-1453: a downloaded area with its elevation tiles pinned stands in
  // for the connection.
  describe('offline, in a downloaded area', () => {
    const AREA = [7.6, 45.95, 7.9, 46.1];
    const ELSEWHERE = [7.2, 46.05, 7.3, 46.12];

    it('is available with the centre inside a downloaded area', () => {
      expect(core.availability(input({ online: false, offlineAreas: [ELSEWHERE, AREA] })))
        .toEqual({ enabled: true, reason: null });
    });

    it('says offline with the centre outside every downloaded area', () => {
      expect(core.availability(input({ online: false, offlineAreas: [ELSEWHERE] })))
        .toEqual({ enabled: false, reason: 'offline' });
      expect(core.availability(input({ online: false, offlineAreas: [] })).reason).toBe('offline');
      expect(core.availability(input({ online: false, offlineAreas: null })).reason).toBe('offline');
    });

    it('still asks every other reason inside one', () => {
      expect(core.availability(input({ online: false, offlineAreas: [AREA], zoom: 8 })).reason)
        .toBe('zoom');
      expect(core.availability(input({ online: false, offlineAreas: [AREA], editing: true })).reason)
        .toBe('editing');
    });

    it('changes nothing online', () => {
      expect(core.availability(input({ offlineAreas: [] }))).toEqual({ enabled: true, reason: null });
      expect(core.availability(input({ offlineAreas: [ELSEWHERE] })))
        .toEqual({ enabled: true, reason: null });
    });
  });

  it('asks the device first: device, slow, webgl, then the rest', () => {
    const all = {
      gated: true, tooSlow: true, contextLost: true,
      online: false, editing: true, centre: { lng: 0, lat: 0 }, zoom: 4,
    };
    expect(core.availability(input(all)).reason).toBe('device');
    expect(core.availability(input({ ...all, gated: false })).reason).toBe('slow');
    expect(core.availability(input({ ...all, gated: false, tooSlow: false })).reason).toBe('webgl');
    expect(core.availability(input({
      ...all, gated: false, tooSlow: false, contextLost: false,
    })).reason).toBe('offline');
  });

  it.each([
    ['west', { lng: WEST, lat: 46.5 }, { lng: WEST - 0.001, lat: 46.5 }],
    ['east', { lng: EAST, lat: 46.5 }, { lng: EAST + 0.001, lat: 46.5 }],
    ['south', { lng: 8, lat: SOUTH }, { lng: 8, lat: SOUTH - 0.001 }],
    ['north', { lng: 8, lat: NORTH }, { lng: 8, lat: NORTH + 0.001 }],
  ])('counts the %s edge of the passed-in box as inside', (_edge, on, past) => {
    expect(core.availability(input({ centre: on })).enabled).toBe(true);
    expect(core.availability(input({ centre: past })).reason).toBe('coverage');
  });

  it('reads coverage from the box it is given, not a copy', () => {
    const elsewhere = [0, 0, 1, 1];
    expect(core.availability(input({ bounds: elsewhere })).reason).toBe('coverage');
    expect(core.availability(input({ bounds: elsewhere, centre: { lng: 0.5, lat: 0.5 } })).enabled).toBe(true);
    expect(core.availability(input({ bounds: null, centre: { lng: 100, lat: 0 } })).enabled).toBe(true);
  });

  it('counts a centre it cannot read as outside', () => {
    expect(core.availability(input({ centre: { lng: NaN, lat: 46 } })).reason).toBe('coverage');
  });
});

describe('the basemap', () => {
  it('is swisstopo winter in 3D and the reader\'s own when flat', () => {
    expect(core.basemapFor(THREE_D, 'openfreemap_liberty')).toBe('swisstopo_winter');
    expect(core.basemapFor(FLAT, 'openfreemap_liberty')).toBe('openfreemap_liberty');
    expect(core.basemapFor('bogus', 'ign_plan')).toBe('ign_plan');
  });

  it('never writes the stored preference', () => {
    window.localStorage.setItem('snowdesk.map.basemap', 'ign_plan');
    core.basemapFor(THREE_D, 'ign_plan');
    core.basemapFor(FLAT, 'ign_plan');
    expect(window.localStorage.getItem('snowdesk.map.basemap')).toBe('ign_plan');
    expect(window.localStorage.length).toBe(1);
    window.localStorage.clear();
  });
});

describe('the boot camera', () => {
  const stored = { center: [7.5, 46.2], zoom: 11, bearing: 30, pitch: 55 };

  it('boots a stored pitch flat, keeping the bearing', () => {
    expect(core.bootCamera(stored, FLAT)).toEqual({ ...stored, pitch: 0 });
    expect(stored.pitch).toBe(55);
  });

  it('keeps the stored camera in 3D, and nothing stays nothing', () => {
    expect(core.bootCamera(stored, THREE_D)).toBe(stored);
    expect(core.bootCamera(null, FLAT)).toBeNull();
  });
});

describe('deviceGate', () => {
  it.each([
    ['no WebGL', { webgl: false, deviceMemory: 8, android: false }, 'device'],
    ['2 GB on Android', { webgl: true, deviceMemory: 2, android: true }, 'device'],
    ['1 GB on Android', { webgl: true, deviceMemory: 1, android: true }, 'device'],
    ['2 GB on a desktop', { webgl: true, deviceMemory: 2, android: false }, null],
    ['4 GB on Android', { webgl: true, deviceMemory: 4, android: true }, null],
    ['no deviceMemory on Android', { webgl: true, deviceMemory: undefined, android: true }, null],
  ])('%s', (_case, gateInput, reason) => {
    expect(core.deviceGate(gateInput)).toBe(reason);
  });

  it('answers the availability reason it gates on', () => {
    expect(core.deviceGate({ webgl: false })).toBe(core.REASONS.DEVICE);
    expect(core.LOW_MEMORY_GB).toBe(2);
  });
});
