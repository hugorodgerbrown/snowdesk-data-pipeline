/*
 * tests/js/test_map_mode_core.js — the Dated / Terrain mode's pure rules
 * (SNOW-1394).
 *
 * What is pinned:
 *   - the dated-overlay set: bulletins, regions, problem terrain, weather and
 *     field reports in; Steep terrain and the static overlays out;
 *   - the URL and storage round trip, an unknown value read as Dated, a
 *     link's ?mode= winning over the device's last mode;
 *   - a mode change keeps ?d= and every other parameter, and a date write
 *     keeps ?mode=terrain;
 *   - SUPPRESSION, NEVER PREFERENCE: with every overlay on and off, a trip
 *     into Terrain, a Steep-terrain toggle there and back leaves every
 *     preference as it was, and only what is drawn moved.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_mode_core.js';

const core = window.pwaMapModeCore;
const { DATED, TERRAIN } = core.MODES;

describe('the dated overlays', () => {
  it.each(['bulletins', 'l3', 'problem_terrain', 'weather', 'community_reports'])(
    '%s is dated',
    (key) => {
      expect(core.isDated(key)).toBe(true);
    },
  );

  // The region tiers are fixed: a region exists beyond the day's bulletin.
  it.each([
    'l1', 'l2', 'l4',
    'terrain_filter', 'routes', 'resorts', 'ski_tours', 'ski_summits', 'rest_zones', 'favourites',
  ])('%s is not, and Terrain never holds it', (key) => {
    expect(core.isDated(key)).toBe(false);
    expect(core.isSuppressed(key, TERRAIN)).toBe(false);
  });

  it('leaves the boundaries rows live in Terrain', () => {
    for (const selector of core.DATED_ROW_SELECTORS) expect(selector).not.toMatch(/boundaries/);
  });

  it('holds nothing in Dated', () => {
    for (const key of core.DATED_OVERLAYS) expect(core.isSuppressed(key, DATED)).toBe(false);
  });
});

describe('reading the mode', () => {
  it('reads terrain, and anything else as dated', () => {
    expect(core.parseMode('terrain')).toBe(TERRAIN);
    for (const value of ['dated', 'TERRAIN', '', null, undefined, 'now', 1]) {
      expect(core.parseMode(value)).toBe(DATED);
    }
  });

  it("takes a link's ?mode= over the device's last mode", () => {
    expect(core.readMode('?mode=terrain', 'dated')).toBe(TERRAIN);
    expect(core.readMode('?mode=dated', 'terrain')).toBe(DATED);
    expect(core.readMode('?mode=bogus', 'terrain')).toBe(DATED);
  });

  it("takes the device's last mode on a bare URL", () => {
    expect(core.readMode('', 'terrain')).toBe(TERRAIN);
    expect(core.readMode('?d=2026-02-10', 'terrain')).toBe(TERRAIN);
    expect(core.readMode('', null)).toBe(DATED);
  });

  it('round-trips through the URL', () => {
    expect(core.readMode(core.withMode('?d=2026-02-10', TERRAIN), null)).toBe(TERRAIN);
    expect(core.readMode(core.withMode('?d=2026-02-10&mode=terrain', DATED), null)).toBe(DATED);
  });
});

describe('writing the URL', () => {
  it('keeps ?d= and every other parameter through a mode change', () => {
    expect(core.withMode('?d=2026-02-10&panel=routes', TERRAIN)).toBe(
      '?d=2026-02-10&panel=routes&mode=terrain',
    );
    expect(core.withMode('?d=2026-02-10&mode=terrain&panel=routes', DATED)).toBe(
      '?d=2026-02-10&panel=routes',
    );
  });

  it('spells Dated by leaving the parameter out', () => {
    expect(core.withMode('?mode=terrain', DATED)).toBe('');
    expect(core.withMode('', DATED)).toBe('');
  });

  it('keeps ?mode=terrain through a date write', () => {
    expect(core.withDate('?d=2026-02-10&mode=terrain', '2026-02-11')).toBe(
      '?d=2026-02-11&mode=terrain',
    );
  });

  it("writes ?d= alone in Dated, as the date write always has", () => {
    expect(core.withDate('?d=2026-02-10', '2026-02-11')).toBe('?d=2026-02-11');
    expect(core.withDate('?d=2026-02-10&mode=bogus', '2026-02-11')).toBe('?d=2026-02-11');
  });
});

describe('suppression, never preference', () => {
  /**
   * What is drawn for one set of preferences in one mode. The preferences
   * are an input the function cannot write: it returns a fresh object.
   */
  const drawn = (prefs, mode) => ({
    weather: core.effective('weather', prefs.weather, mode),
    community_reports: core.effective('community_reports', prefs.community_reports, mode),
    terrain_filter: core.effective('terrain_filter', prefs.terrain_filter, mode),
    problem_terrain: core.problemTerrainShown({
      available: true,
      problemTerrain: prefs.problem_terrain,
      steepTerrain: prefs.terrain_filter,
      mode,
    }),
  });

  const COMBINATIONS = [];
  for (const weather of [true, false]) {
    for (const reports of [true, false]) {
      for (const steep of [true, false]) {
        for (const problem of [true, false]) {
          COMBINATIONS.push({
            weather, community_reports: reports, terrain_filter: steep, problem_terrain: problem,
          });
        }
      }
    }
  }

  it.each(COMBINATIONS)('a round trip through Terrain keeps %o', (start) => {
    const prefs = Object.freeze({ ...start });
    const dated = drawn(prefs, DATED);

    // Into Terrain: every dated overlay off, Steep terrain as the reader had it.
    const inTerrain = drawn(prefs, TERRAIN);
    expect(inTerrain.weather).toBe(false);
    expect(inTerrain.community_reports).toBe(false);
    expect(inTerrain.problem_terrain).toBe(false);
    expect(inTerrain.terrain_filter).toBe(prefs.terrain_filter);

    // Steep terrain toggled there and back changes Steep terrain alone.
    const toggled = { ...prefs, terrain_filter: !prefs.terrain_filter };
    expect(drawn(toggled, TERRAIN).terrain_filter).toBe(!prefs.terrain_filter);
    expect(toggled.problem_terrain).toBe(prefs.problem_terrain);
    const back = { ...toggled, terrain_filter: prefs.terrain_filter };

    // Back to Dated: exactly what was drawn before, from untouched choices.
    expect(back).toEqual(start);
    expect(drawn(back, DATED)).toEqual(dated);
  });

  it('lets Steep terrain take precedence over the sync in Dated (SNOW-1377)', () => {
    expect(core.problemTerrainShown({
      available: true, problemTerrain: true, steepTerrain: true, mode: DATED,
    })).toBe(false);
    expect(core.problemTerrainShown({
      available: true, problemTerrain: true, steepTerrain: false, mode: DATED,
    })).toBe(true);
    expect(core.problemTerrainShown({
      available: false, problemTerrain: true, steepTerrain: false, mode: DATED,
    })).toBe(false);
  });
});

describe('toggled', () => {
  it('swaps the two modes', () => {
    expect(core.toggled(DATED)).toBe(TERRAIN);
    expect(core.toggled(TERRAIN)).toBe(DATED);
    expect(core.toggled('bogus')).toBe(TERRAIN);
  });
});
