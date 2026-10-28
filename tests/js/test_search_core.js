/*
 * tests/js/test_search_core.js — Vitest unit tests for
 * static/js/search_core.js (SNOW-618, extended by SNOW-697).
 *
 * The map's search box had no coverage of any kind: matching, accent
 * folding, resort-name folding and result ordering all lived inside
 * ``map.js``'s main IIFE, which cannot be imported under jsdom. SNOW-618
 * extracted them; these are the tests that extraction was for.
 *
 * SNOW-697 gave resorts their own rows again and replaced region-id
 * ordering with match-quality ranking, so the assertions about both have
 * been rewritten rather than added to — the old ones described behaviour
 * that is deliberately gone.
 *
 * ``search_core.js`` is a browser IIFE with no exports — importing it for
 * side effects publishes ``self.pwaSearchCore``.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/search_core.js';

const core = self.pwaSearchCore;

/**
 * Build a full index from `[props, resorts]` pairs, as `map.js` does.
 *
 * @param {Array<[object, string[]]>} pairs
 * @returns {Array<object>}
 */
function buildIndex(pairs) {
  return pairs.flatMap(([props, resorts]) => core.buildEntries(props, resorts));
}

const INDEX = buildIndex([
  [{ regionID: 'CH-4115', name: 'Martigny / Verbier', subregion_name: 'Lower Valais' }, ['Verbier', 'La Fouly']],
  [{ regionID: 'CH-2101', name: 'Évolène', subregion_name: 'Central Valais' }, ['Évolène']],
  [{ regionID: 'AT-07-01', name: 'Arlberg', subregion_name: '' }, ['St. Anton']],
  [{ regionID: 'IT-32-BZ-01', name: 'Graubünden Border', subregion_name: '' }, []],
  [{ regionID: 'FR-73-01', name: 'Chablais', subregion_name: '' }, ['Avoriaz']],
]);

/** @param {Array<object>} rows @returns {string[]} */
const primaries = (rows) => rows.map((r) => r.primary);

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

describe('buildRegionEntry', () => {
  it('labels the row with the region name and its L2 parent', () => {
    const entry = core.buildRegionEntry({
      regionID: 'CH-4115',
      name: 'Martigny / Verbier',
      subregion_name: 'Lower Valais',
    });

    expect(entry.type).toBe('region');
    expect(entry.primary).toBe('Martigny / Verbier');
    expect(entry.secondary).toBe('Lower Valais');
    expect(entry.regionID).toBe('CH-4115');
  });

  it('makes the region id and the L2 name searchable', () => {
    const entry = core.buildRegionEntry({
      regionID: 'CH-4115',
      name: 'Martigny / Verbier',
      subregion_name: 'Lower Valais',
    });

    expect(entry.searchable).toContain('ch-4115');
    expect(entry.searchable).toContain('lower valais');
  });

  it('does not fold resort names in — the resort has its own row now', () => {
    // SNOW-697: folding as well would return two rows for one query that
    // both select the same region.
    const entry = core.buildRegionEntry({ regionID: 'CH-4115', name: 'Martigny / Verbier' });
    expect(entry.searchable).not.toContain('la fouly');
  });

  it('falls back to the id when the region has no name', () => {
    expect(core.buildRegionEntry({ regionID: 'CH-4115' }).primary).toBe('CH-4115');
  });

  it('returns null for props with no region id', () => {
    // A row with nothing to select is worse than no row.
    expect(core.buildRegionEntry({ name: 'Nowhere' })).toBeNull();
    expect(core.buildRegionEntry(null)).toBeNull();
  });
});

describe('buildResortEntries', () => {
  const props = { regionID: 'CH-4115', name: 'Martigny / Verbier' };

  it('gives each resort its own row, labelled with its parent region', () => {
    const entries = core.buildResortEntries(props, ['Verbier', 'La Fouly']);

    expect(primaries(entries)).toEqual(['Verbier', 'La Fouly']);
    expect(entries.every((e) => e.type === 'resort')).toBe(true);
    expect(entries.every((e) => e.secondary === 'Martigny / Verbier')).toBe(true);
  });

  it('points every resort row at its parent region, not at itself', () => {
    // The row is a better label for the same destination, not a new one.
    const entries = core.buildResortEntries(props, ['Verbier']);
    expect(entries[0].regionID).toBe('CH-4115');
  });

  it('skips a resort whose name is its region name', () => {
    // "Davos" in "Davos" would be a near-duplicate row selecting exactly
    // what the region row already selects.
    expect(core.buildResortEntries({ regionID: 'CH-5121', name: 'Davos' }, ['Davos'])).toEqual([]);
  });

  it('matches that name case- and accent-insensitively when skipping', () => {
    const entries = core.buildResortEntries({ regionID: 'CH-2101', name: 'Évolène' }, ['evolene']);
    expect(entries).toEqual([]);
  });

  it('tolerates a region with no resorts, and blank names', () => {
    expect(core.buildResortEntries(props, [])).toEqual([]);
    expect(core.buildResortEntries(props)).toEqual([]);
    expect(core.buildResortEntries(props, ['', null])).toEqual([]);
  });

  it('returns nothing for props with no region id', () => {
    expect(core.buildResortEntries({ name: 'Nowhere' }, ['Somewhere'])).toEqual([]);
  });
});

describe('buildEntries', () => {
  it('returns the region row first, then one per resort', () => {
    const entries = core.buildEntries(
      { regionID: 'CH-4115', name: 'Martigny / Verbier', subregion_name: 'Lower Valais' },
      ['Verbier', 'La Fouly'],
    );

    expect(primaries(entries)).toEqual(['Martigny / Verbier', 'Verbier', 'La Fouly']);
  });

  it('returns nothing at all when the region has no id', () => {
    expect(core.buildEntries({ name: 'Nowhere' }, ['Somewhere'])).toEqual([]);
  });
});

describe('runSearch', () => {
  it('matches a region by name, and lists its resorts under it', () => {
    // The resort rows carry their parent's name in `searchable`, so a
    // region query answers "what is in here?" as well as "where is it?" —
    // and the score bands keep the region itself on top.
    expect(primaries(core.runSearch(INDEX, 'arlberg'))).toEqual(['Arlberg', 'St. Anton']);
  });

  it('returns the resort itself as a row', () => {
    // SNOW-697, and the whole point of it: before, this query returned the
    // parent region and never showed the word it matched on.
    const rows = core.runSearch(INDEX, 'la fouly');
    expect(rows).toHaveLength(1);
    expect(rows[0].primary).toBe('La Fouly');
    expect(rows[0].type).toBe('resort');
    expect(rows[0].secondary).toBe('Martigny / Verbier');
  });

  it('still selects the parent region from a resort row', () => {
    expect(core.runSearch(INDEX, 'st. anton')[0].regionID).toBe('AT-07-01');
  });

  it('matches a region by its EAWS id', () => {
    expect(primaries(core.runSearch(INDEX, 'CH-2101'))).toEqual(['Évolène']);
  });

  it('matches a region by its parent sub-region name', () => {
    expect(primaries(core.runSearch(INDEX, 'lower valais'))).toEqual(['Martigny / Verbier']);
  });

  it('matches an accented name typed without accents', () => {
    expect(primaries(core.runSearch(INDEX, 'evolene'))).toEqual(['Évolène']);
    expect(primaries(core.runSearch(INDEX, 'graubunden'))).toEqual(['Graubünden Border']);
  });

  it('ranks a name-prefix match above a mid-name one', () => {
    // The three score bands in one query. "Verbier" the resort starts with
    // it (0); "Martigny / Verbier" the region merely contains it (1); "La
    // Fouly" matched only through its parent's name, which is not on the
    // row (2) — so the resort you asked for, then its region, then that
    // region's other resorts.
    expect(primaries(core.runSearch(INDEX, 'verbier'))).toEqual([
      'Verbier',
      'Martigny / Verbier',
      'La Fouly',
    ]);
  });

  it('ranks rows matched on an invisible field last', () => {
    // A query hitting only the L2 name or the parent's name cannot be seen
    // in the row, so it sorts below anything matched on the row's own name.
    const index = buildIndex([
      [{ regionID: 'CH-1000', name: 'Valais Ridge', subregion_name: '' }, []],
      [{ regionID: 'CH-2000', name: 'Somewhere', subregion_name: 'Lower Valais' }, []],
    ]);

    expect(primaries(core.runSearch(index, 'valais'))).toEqual(['Valais Ridge', 'Somewhere']);
  });

  it('breaks ties by name, then region id — never by insertion order', () => {
    // Determinism is what makes the list safe to aim arrow keys at.
    const index = buildIndex([
      [{ regionID: 'CH-9000', name: 'Alpha Ridge' }, []],
      [{ regionID: 'CH-1000', name: 'Alpha Ridge' }, []],
      [{ regionID: 'AT-5000', name: 'Alpha Basin' }, []],
    ]);
    const rows = core.runSearch(index, 'alpha');

    expect(primaries(rows)).toEqual(['Alpha Basin', 'Alpha Ridge', 'Alpha Ridge']);
    expect(rows.map((r) => r.regionID)).toEqual(['AT-5000', 'CH-1000', 'CH-9000']);
  });

  it('is a pure function of the query — same input, same order', () => {
    expect(core.runSearch(INDEX, 'v')).toEqual(core.runSearch(INDEX, 'v'));
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

  it('shows an exact resort match that region-id ordering would have buried', () => {
    // The concrete failure that retired SNOW-188's sort: eight regions all
    // matching "val", sorting ahead of the resort actually named "Val".
    const index = buildIndex([
      ...Array.from({ length: 8 }, (_, i) => [
        { regionID: `AT-07-${10 + i}`, name: `Valley ${i}` },
        [],
      ]),
      [{ regionID: 'CH-4115', name: 'Martigny' }, ['Val']],
    ]);

    expect(primaries(core.runSearch(index, 'val'))[0]).toBe('Val');
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

  it('tolerates an empty or missing index, and malformed entries', () => {
    expect(core.runSearch([], 'anything')).toEqual([]);
    expect(core.runSearch(null, 'anything')).toEqual([]);
    expect(core.runSearch([null, {}, { searchable: 'x' }], 'x')).toEqual([]);
  });
});

describe('groupResults (SNOW-1173)', () => {
  const { groupResults } = self.pwaSearchCore;
  const row = (type, primary) => ({
    type, primary, secondary: '', primaryKey: primary.toLowerCase(), regionID: 'CH-4115', searchable: '',
  });

  it('leads with the kind that holds the best-ranked row', () => {
    const groups = groupResults([
      row('resort', 'Verbier'),
      row('region', 'Martigny-Verbier'),
      row('resort', 'Nendaz'),
    ]);
    expect(groups.map((g) => g.type)).toEqual(['resort', 'region']);
    expect(groups[0].items.map((r) => r.primary)).toEqual(['Verbier', 'Nendaz']);
    expect(groups[1].items.map((r) => r.primary)).toEqual(['Martigny-Verbier']);
  });

  it('puts regions first when a region ranks first', () => {
    const groups = groupResults([row('region', 'Haut Val de Bagnes'), row('resort', 'Bruson')]);
    expect(groups.map((g) => g.type)).toEqual(['region', 'resort']);
  });

  it('returns no group for a kind with no rows, and nothing for no rows', () => {
    expect(groupResults([row('region', 'Zermatt')]).map((g) => g.type)).toEqual(['region']);
    expect(groupResults([])).toEqual([]);
  });
});

describe('buildPinnedResortEntries', () => {
  const ADELBODEN = {
    resort_slug: 'adelboden', resort_name: 'Adelboden',
    resort_region_id: 'CH-1226', resort_region_name: 'Adelboden',
  };
  const WENGEN = {
    resort_slug: 'wengen', resort_name: 'Wengen',
    resort_region_id: 'CH-1233', resort_region_name: 'Lauterbrunnen',
  };
  const PIN = { resort_slug: null, resort_name: null, resort_region_id: null, resort_region_name: null };

  it('builds a resort row carrying the slug it opens', () => {
    const [row] = core.buildPinnedResortEntries([WENGEN]);
    expect(row).toMatchObject({
      type: 'resort', primary: 'Wengen', secondary: 'Lauterbrunnen',
      regionID: 'CH-1233', slug: 'wengen',
    });
  });

  it('keeps a resort named like its region, which the index skips', () => {
    expect(core.buildPinnedResortEntries([ADELBODEN]).map((r) => r.primary)).toEqual(['Adelboden']);
  });

  it('ignores a plain dropped pin', () => {
    expect(core.buildPinnedResortEntries([PIN, WENGEN]).map((r) => r.slug)).toEqual(['wengen']);
  });

  it('sorts by name and lists a resort once', () => {
    const rows = core.buildPinnedResortEntries([WENGEN, ADELBODEN, WENGEN]);
    expect(rows.map((r) => r.slug)).toEqual(['adelboden', 'wengen']);
  });

  it('falls back to the region id when the region has no name', () => {
    const [row] = core.buildPinnedResortEntries([{ ...WENGEN, resort_region_name: null }]);
    expect(row.secondary).toBe('CH-1233');
  });

  it('is empty for no feed', () => {
    expect(core.buildPinnedResortEntries(null)).toEqual([]);
  });
});

describe('buildPinEntries (SNOW-1264)', () => {
  const pin = (over) => ({
    uuid: 'u1', name: 'Col de Mille', created_at: '2026-10-01T10:00:00+00:00',
    resort_slug: null, region_id: 'CH-4115', region_name: 'Martigny-Verbier', ...over,
  });

  it('builds a pin row carrying the uuid it opens', () => {
    const [row] = core.buildPinEntries([pin()], 'Unnamed pin');
    expect(row).toMatchObject({
      type: 'pin', primary: 'Col de Mille', secondary: 'Martigny-Verbier',
      regionID: 'CH-4115', uuid: 'u1',
    });
  });

  it('labels an unnamed pin and leaves it nothing to match on', () => {
    const [row] = core.buildPinEntries([pin({ name: '' })], 'Unnamed pin');
    expect(row.primary).toBe('Unnamed pin');
    expect(row.primaryKey).toBe('');
    expect(row.searchable).toBe('');
  });

  it('ignores a resort favourite and a pin not yet saved', () => {
    const rows = core.buildPinEntries(
      [pin({ resort_slug: 'verbier' }), pin({ uuid: null }), pin({ uuid: 'u2' })],
      'Unnamed pin',
    );
    expect(rows.map((r) => r.uuid)).toEqual(['u2']);
  });

  it('lists named pins by name, then unnamed ones newest first', () => {
    const rows = core.buildPinEntries([
      pin({ uuid: 'old', name: '', created_at: '2026-01-01T00:00:00+00:00' }),
      pin({ uuid: 'z', name: 'Zinal hut' }),
      pin({ uuid: 'new', name: '', created_at: '2026-10-01T00:00:00+00:00' }),
      pin({ uuid: 'a', name: 'Arolla car park' }),
    ], 'Unnamed pin');
    expect(rows.map((r) => r.uuid)).toEqual(['a', 'z', 'new', 'old']);
  });

  it('is found by its name, accents folded, and not by its region', () => {
    const index = core.buildPinEntries([pin({ name: 'Évolène bus stop' })], 'Unnamed pin');
    expect(core.runSearch(index, 'evol').map((r) => r.uuid)).toEqual(['u1']);
    expect(core.runSearch(index, 'martigny')).toEqual([]);
  });

  it('is never found by typing when unnamed, even by its label', () => {
    const index = core.buildPinEntries([pin({ name: '' })], 'Unnamed pin');
    expect(core.runSearch(index, 'unnamed')).toEqual([]);
  });

  it('ranks beside regions and resorts and groups as its own kind', () => {
    const index = buildIndex([[{ regionID: 'CH-4115', name: 'Verbier' }, []]])
      .concat(core.buildPinEntries([pin({ name: 'Verbier chalet' })], 'Unnamed pin'));
    const results = core.runSearch(index, 'verbier');
    expect(results.map((r) => r.type)).toEqual(['region', 'pin']);
    expect(core.groupResults(results).map((g) => g.type)).toEqual(['region', 'pin']);
  });

  it('is empty for no feed', () => {
    expect(core.buildPinEntries(null, 'Unnamed pin')).toEqual([]);
  });
});
