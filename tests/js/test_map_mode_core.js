/*
 * tests/js/test_map_mode_core.js — the Conditions / Terrain mode's pure
 * rules (SNOW-1394, SNOW-1421).
 *
 * What is pinned:
 *   - the per-mode table: bulletins, their grouping, problem terrain and
 *     weather are Conditions' own; Steep terrain, SAC's tours and summits
 *     and the rest zones are Terrain's; the field reports, the region tiers
 *     and the reader's data are shared. Each mode holds the other's;
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

const DATED_ONLY = ['bulletins', 'l3', 'problem_terrain', 'weather'];
const TERRAIN_ONLY = ['terrain_filter', 'ski_tours', 'ski_summits', 'rest_zones'];
// The region tiers are fixed (a region exists beyond the day's bulletin),
// and the field reports matter to a planner too (Hugo, 5 Nov 2026).
const SHARED = ['l1', 'l2', 'l4', 'community_reports', 'routes', 'resorts', 'favourites', 'downloads'];

describe('the per-mode table', () => {
  it('lists each mode\'s own layers', () => {
    expect([...core.ONLY_IN[DATED]]).toEqual(DATED_ONLY);
    expect([...core.ONLY_IN[TERRAIN]]).toEqual(TERRAIN_ONLY);
  });

  it.each(DATED_ONLY)('%s is Conditions\' own: held in Terrain, drawn in Conditions', (key) => {
    expect(core.modeOf(key)).toBe(DATED);
    expect(core.isSuppressed(key, TERRAIN)).toBe(true);
    expect(core.isSuppressed(key, DATED)).toBe(false);
  });

  it.each(TERRAIN_ONLY)('%s is Terrain\'s own: held in Conditions, drawn in Terrain', (key) => {
    expect(core.modeOf(key)).toBe(TERRAIN);
    expect(core.isSuppressed(key, DATED)).toBe(true);
    expect(core.isSuppressed(key, TERRAIN)).toBe(false);
  });

  it.each(SHARED)('%s is shared, and neither mode holds it', (key) => {
    expect(core.modeOf(key)).toBeNull();
    expect(core.isSuppressed(key, DATED)).toBe(false);
    expect(core.isSuppressed(key, TERRAIN)).toBe(false);
  });

  it('holds the other mode\'s own in each', () => {
    expect([...core.suppressedIn(DATED)]).toEqual(TERRAIN_ONLY);
    expect([...core.suppressedIn(TERRAIN)]).toEqual(DATED_ONLY);
    // An unknown mode reads as Conditions.
    expect([...core.suppressedIn('bogus')]).toEqual(TERRAIN_ONLY);
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

  it('keeps ?view=3d with the mode in Terrain (SNOW-1449)', () => {
    expect(core.withDate('?d=2026-02-10&mode=terrain&view=3d', '2026-02-11')).toBe(
      '?d=2026-02-11&mode=terrain&view=3d',
    );
  });

  it('keeps ?view=3d in Conditions too (SNOW-1452), and drops any value but 3d', () => {
    expect(core.withDate('?d=2026-02-10&view=3d', '2026-02-11')).toBe('?d=2026-02-11&view=3d');
    expect(core.withDate('?mode=terrain&view=tilted', '2026-02-11')).toBe(
      '?d=2026-02-11&mode=terrain',
    );
    expect(core.withDate('?d=2026-02-10&view=tilted', '2026-02-11')).toBe('?d=2026-02-11');
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

    // In Conditions: its own as the reader had them, Steep terrain held,
    // the shared reports as the reader had them.
    expect(dated.weather).toBe(prefs.weather);
    expect(dated.terrain_filter).toBe(false);
    expect(dated.community_reports).toBe(prefs.community_reports);
    // Steep terrain is not drawn here, so it never hides the sync.
    expect(dated.problem_terrain).toBe(prefs.problem_terrain);

    // Into Terrain: Conditions' own off, Steep terrain as the reader had it.
    const inTerrain = drawn(prefs, TERRAIN);
    expect(inTerrain.weather).toBe(false);
    expect(inTerrain.problem_terrain).toBe(false);
    expect(inTerrain.community_reports).toBe(prefs.community_reports);
    expect(inTerrain.terrain_filter).toBe(prefs.terrain_filter);

    // Steep terrain toggled there and back changes Steep terrain alone.
    const toggled = { ...prefs, terrain_filter: !prefs.terrain_filter };
    expect(drawn(toggled, TERRAIN).terrain_filter).toBe(!prefs.terrain_filter);
    expect(drawn(toggled, DATED)).toEqual(dated);
    expect(toggled.problem_terrain).toBe(prefs.problem_terrain);
    const back = { ...toggled, terrain_filter: prefs.terrain_filter };

    // Back to Conditions: exactly what was drawn before, from untouched choices.
    expect(back).toEqual(start);
    expect(drawn(back, DATED)).toEqual(dated);
  });

  it('never lets a Steep terrain the mode holds hide the sync (SNOW-1377)', () => {
    // Steep terrain takes precedence only where it is drawn, and with the
    // two layers in different modes that is never in Conditions.
    const shown = (steepTerrain, mode) => core.problemTerrainShown({
      available: true, problemTerrain: true, steepTerrain, mode,
    });
    expect(shown(true, DATED)).toBe(true);
    expect(shown(false, DATED)).toBe(true);
    expect(shown(true, TERRAIN)).toBe(false);
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
