/*
 * tests/js/test_search_core.js — Vitest unit tests for
 * static/js/search_core.js (SNOW-618).
 *
 * The map's search box had no coverage of any kind: matching, accent
 * folding, resort-name folding and result ordering all lived inside
 * ``map.js``'s main IIFE, which cannot be imported under jsdom. This
 * ticket extracted them; these are the tests that extraction was for.
 *
 * ``search_core.js`` is a browser IIFE with no exports — importing it for
 * side effects publishes ``self.pwaSearchCore``.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/search_core.js';

const core = self.pwaSearchCore;

/**
 * Build an index from `[props, resorts]` pairs, as `map.js` does.
 *
 * @param {Array<[object, string[]]>} pairs
 * @returns {Array<object>}
 */
function buildIndex(pairs) {
  return pairs.map(([props, resorts]) => core.buildEntry(props, resorts)).filter(Boolean);
}

const INDEX = buildIndex([
  [{ regionID: 'CH-4115', name: 'Martigny / Verbier', subregion_name: 'Lower Valais' }, ['Verbier', 'La Fouly']],
  [{ regionID: 'CH-2101', name: 'Évolène', subregion_name: 'Central Valais' }, []],
  [{ regionID: 'AT-07-01', name: 'Arlberg', subregion_name: '' }, ['St. Anton']],
  [{ regionID: 'IT-32-BZ-01', name: 'Graubünden Border', subregion_name: '' }, []],
  [{ regionID: 'FR-73-01', name: 'Chablais', subregion_name: '' }, []],
]);

describe('normalise', () => {
  it('folds case and accents so an unaccented query still matches', () => {
    // What an Alpine map's visitors actually type.
    expect(core.normalise('Évolène')).toBe('evolene');
    expect(core.normalise('Graubünden')).toBe('graubunden');
    expect(core.normalise('MARTIGNY')).toBe('martigny');
  });

  it('leaves an unaccented string alone', () => {
    expect(core.normalise('arlberg')).toBe('arlberg');
  });

  it('tolerates null and undefined', () => {
    expect(core.normalise(null)).toBe('');
    expect(core.normalise(undefined)).toBe('');
  });
});

describe('buildEntry', () => {
  it('folds resort names into the searchable string but not the label', () => {
    const entry = core.buildEntry(
      { regionID: 'CH-4115', name: 'Martigny / Verbier', subregion_name: 'Lower Valais' },
      ['Verbier', 'La Fouly'],
    );

    // A query for a resort has to find its parent region — but the row
    // shows the L2 name, because the resort list is either empty or long
    // enough that truncating it is noise.
    expect(entry.searchable).toContain('verbier');
    expect(entry.searchable).toContain('la fouly');
    expect(entry.primary).toBe('Martigny / Verbier');
    expect(entry.subregionName).toBe('Lower Valais');
  });

  it('makes the region id searchable', () => {
    const entry = core.buildEntry({ regionID: 'CH-4115', name: 'Verbier' }, []);
    expect(entry.searchable).toContain('ch-4115');
  });

  it('falls back to the id when the region has no name', () => {
    expect(core.buildEntry({ regionID: 'CH-4115' }, []).primary).toBe('CH-4115');
  });

  it('returns null for props with no region id', () => {
    // A row with nothing to select is worse than no row.
    expect(core.buildEntry({ name: 'Nowhere' }, [])).toBeNull();
    expect(core.buildEntry(null, [])).toBeNull();
  });

  it('tolerates a region with no resorts', () => {
    expect(core.buildEntry({ regionID: 'AT-07-01', name: 'Arlberg' })).toBeTruthy();
  });
});

describe('runSearch', () => {
  it('matches a region by name', () => {
    expect(core.runSearch(INDEX, 'arlberg').map((r) => r.regionID)).toEqual(['AT-07-01']);
  });

  it('matches a region by one of its resorts', () => {
    // The behaviour the folding exists for.
    expect(core.runSearch(INDEX, 'la fouly').map((r) => r.regionID)).toEqual(['CH-4115']);
    expect(core.runSearch(INDEX, 'st. anton').map((r) => r.regionID)).toEqual(['AT-07-01']);
  });

  it('matches a region by its EAWS id', () => {
    expect(core.runSearch(INDEX, 'CH-2101').map((r) => r.regionID)).toEqual(['CH-2101']);
  });

  it('matches a region by its parent sub-region name', () => {
    expect(core.runSearch(INDEX, 'lower valais').map((r) => r.regionID)).toEqual(['CH-4115']);
  });

  it('matches an accented name typed without accents', () => {
    expect(core.runSearch(INDEX, 'evolene').map((r) => r.regionID)).toEqual(['CH-2101']);
    expect(core.runSearch(INDEX, 'graubunden').map((r) => r.regionID)).toEqual(['IT-32-BZ-01']);
  });

  it('groups results by country, in id order', () => {
    // Not relevance ranking: the list must not reshuffle as the user
    // types, because they are aiming at it with arrow keys.
    const ids = core.runSearch(INDEX, '-').map((r) => r.regionID);
    expect(ids).toEqual([...ids].sort((a, b) => a.localeCompare(b)));
    expect(ids[0].startsWith('AT-')).toBe(true);
  });

  it('caps the result list', () => {
    const many = buildIndex(
      Array.from({ length: 20 }, (_, i) => [
        { regionID: `CH-${1000 + i}`, name: `Region ${i}` },
        [],
      ]),
    );

    expect(core.runSearch(many, 'region')).toHaveLength(core.MAX_RESULTS);
    expect(core.runSearch(many, 'region', 3)).toHaveLength(3);
  });

  it('returns nothing for a blank or whitespace-only query', () => {
    // An empty box must not read as "no matches" — it has not been asked
    // anything yet.
    expect(core.runSearch(INDEX, '')).toEqual([]);
    expect(core.runSearch(INDEX, '   ')).toEqual([]);
  });

  it('returns nothing for a query that matches nothing', () => {
    expect(core.runSearch(INDEX, 'zermatt')).toEqual([]);
  });

  it('tolerates an empty or missing index', () => {
    expect(core.runSearch([], 'anything')).toEqual([]);
    expect(core.runSearch(null, 'anything')).toEqual([]);
  });
});
