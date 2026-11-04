/*
 * tests/js/test_offline_journey_core.js — Vitest unit tests for
 * static/js/offline_journey_core.js (SNOW-1308).
 *
 * One panel per downloaded area on /offline/: three stages and a verdict.
 * The cases worth most are the ones where a naive reading says Yes — a
 * bucket whose documents verify and whose tiles do not, an area with no
 * record of what the reader's things are, a reading that did not come
 * back. Each must read No or "Can't tell", never Ready.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

let journey;

const NOW = '2026-09-11T08:00:00.000Z';
const ORIGIN = 'https://snowdesk.info';
const TRIP = '11111111-1111-4111-8111-111111111111';
const PIN = '22222222-2222-4222-8222-222222222222';

/** A region area whose bucket verifies and whose content is today's. */
function area(overrides) {
  return {
    id: 'region-ch-4115',
    kind: 'region',
    name: 'Martigny',
    basemapKey: 'openfreemap',
    template: 'https://t/{z}/{x}/{y}.pbf',
    z: { 14: [8510, 8511, 5815, 5815] },
    deps: ['https://t/style.json'],
    bucketPresent: true,
    bucketReadable: true,
    entries: ['https://t/style.json', 'https://t/14/8510/5815.pbf'],
    contentAt: '2026-09-11T07:00:00.000Z',
    items: {
      trips: [
        {
          uuid: TRIP,
          name: 'Saturday tour',
          urls: ['/trips/' + TRIP + '/', '/trips/' + TRIP + '/route.geojson'],
        },
      ],
      pins: [
        {
          uuid: PIN,
          name: 'Hut',
          urls: [
            '/partials/place-sheet/favourite/' + PIN + '/?d=2026-09-11',
            '/api/weather/W1/detail/?date=2026-09-11',
          ],
        },
      ],
      // The account whose sync wrote these names (SNOW-1308 review).
      principal: 'acct-1',
    },
    ...(overrides || {}),
  };
}

/** Readings for one signed-in reader holding every url `area()` names. */
function readings(areas, overrides) {
  return {
    now: NOW,
    currentPrincipal: 'acct-1',
    cachesReadable: true,
    // Each stamped for this reader, as the worker keeps them (SNOW-1308
    // review: presence alone is not enough for these families).
    shellEntries: [
      { url: ORIGIN + '/trips/' + TRIP + '/', isPage: true, principal: 'acct-1' },
      { url: ORIGIN + '/trips/' + TRIP + '/route.geojson', principal: 'acct-1', scoped: true },
      { url: ORIGIN + '/partials/place-sheet/favourite/' + PIN + '/?d=2026-09-11', principal: 'acct-1', scoped: true },
      { url: ORIGIN + '/api/weather/W1/detail/?date=2026-09-11' },
    ],
    areas: areas || [area()],
    ...(overrides || {}),
  };
}

/** The one panel for `readings`, with the tiles verified unless told. */
function panel(r, tiles) {
  const tileMap = {};
  (r.areas || []).forEach((a) => {
    tileMap[a.id] = tiles === undefined ? true : tiles;
  });
  return journey.buildPanels(r, {}, { tiles: tileMap, origin: ORIGIN, canFetch: true })[0];
}

beforeAll(async () => {
  await import('../../static/js/offline_audit_core.js');
  await import('../../static/js/offline_journey_core.js');
  journey = window.pwaOfflineJourneyCore;
});

describe('the verdict', () => {
  it('is Ready only when every stage is Yes', () => {
    const p = panel(readings());

    expect(p.stages.map.status).toBe('yes');
    expect(p.stages.conditions.status).toBe('yes');
    expect(p.stages.things.status).toBe('yes');
    expect(p.verdict).toBe('ready');
  });

  it('needs a refresh, tap to fix, when the bulletins are behind', () => {
    const p = panel(readings([area({ contentAt: '2026-09-08T07:00:00.000Z' })]));

    expect(p.stages.conditions).toEqual({ status: 'no', reason: 'stale' });
    expect(p.verdict).toBe('refresh');
  });

  it('needs a refresh, tap to fix, when one of your things is missing', () => {
    const r = readings();
    r.shellEntries = r.shellEntries.filter((entry) => !entry.url.includes('route.geojson'));
    const p = panel(r);

    expect(p.stages.things.status).toBe('no');
    expect(p.stages.things.rows).toEqual([
      { kind: 'trip', name: 'Saturday tour', status: 'no' },
      { kind: 'pin', name: 'Hut', status: 'yes' },
    ]);
    expect(p.verdict).toBe('refresh');
  });

  it('sends a tile gap to the map, because tiles cannot be fetched here', () => {
    const p = panel(readings(), false);

    expect(p.stages.map).toEqual({ status: 'no', reason: 'tiles' });
    expect(p.verdict).toBe('open-map');
  });

  it('sends the map first even when another stage could be tapped', () => {
    const p = panel(readings([area({ contentAt: null })]), false);

    expect(p.verdict).toBe('open-map');
  });

  it("can't tell when a stage could not be read and none is No", () => {
    const p = panel(readings(), null);

    expect(p.stages.map).toEqual({ status: 'unknown', reason: 'tiles-unread' });
    expect(p.verdict).toBe('unknown');
  });

  it("promises no tap where Sync now cannot run — it can't tell instead", () => {
    // A bucket that did not answer is not completable, so no Sync is
    // offered; a missing thing beside it must not read "tap to fix".
    const r = readings([area({ bucketReadable: false, bucketPresent: false, entries: [] })]);
    r.shellEntries = [];
    const p = panel(r);

    expect(p.stages.things.status).toBe('no');
    expect(p.canSync).toBe(false);
    expect(p.verdict).toBe('unknown');
  });

  it('prefers the tap over "can\'t tell" when something is fixable here', () => {
    const p = panel(readings([area({ contentAt: null })]), null);

    expect(p.verdict).toBe('refresh');
  });

  it('reads every stage unknown when the collection threw', () => {
    const p = panel(readings(undefined, { failure: 'TypeError: boom' }));

    expect(p.stages.map.status).toBe('unknown');
    expect(p.stages.conditions.status).toBe('unknown');
    expect(p.stages.things.status).toBe('unknown');
    expect(p.verdict).toBe('unknown');
    expect(p.canSync).toBe(false);
  });
});

describe('the map stage', () => {
  it('is unknown for a bucket that did not answer, never a No', () => {
    const p = panel(
      readings([area({ bucketPresent: false, bucketReadable: false, entries: [] })]),
    );

    expect(p.stages.map).toEqual({ status: 'unknown', reason: 'unreadable' });
    expect(p.verdict).toBe('unknown');
  });

  it('is No for a TileJSON that is missing, whatever the tiles say', () => {
    const p = panel(readings([area({ entries: ['https://t/14/8510/5815.pbf'] })]));

    expect(p.stages.map).toEqual({ status: 'no', reason: 'incomplete' });
  });

  it('is No for an orphaned bucket, which offers no Sync', () => {
    const p = panel(
      readings([
        {
          id: 'region-CH-9999',
          kind: 'region',
          orphaned: true,
          name: '',
          deps: [],
          bucketPresent: true,
          entries: ['https://t/1.pbf'],
        },
      ]),
    );

    expect(p.name).toBe('region-CH-9999');
    expect(p.orphaned).toBe(true);
    expect(p.stages.map).toEqual({ status: 'no', reason: 'orphaned' });
    expect(p.canSync).toBe(false);
  });
});

describe('the tile reading', () => {
  it('asks blobFullyCached with the stored template, the ground and the bucket', () => {
    const download = { blobFullyCached: vi.fn(() => true) };
    const a = area();

    expect(journey.tileReading(a, download)).toBe(true);
    expect(download.blobFullyCached).toHaveBeenCalledWith(a.template, { z: a.z }, a.entries);
  });

  it('rebuilds a custom area from its box and band, and a drop zone as its circle', () => {
    const download = {
      blobFullyCached: vi.fn(() => false),
      buildBlob: vi.fn(() => ({ z: { 12: [1, 1, 1, 1] } })),
      circleBlobForBBox: vi.fn(() => ({ z: { 12: [2, 2, 2, 2] } })),
    };
    const box = [7, 46, 7.1, 46.1];
    const custom = area({ id: 'custom-a', kind: 'custom', z: null, bbox: box, band: [10, 14] });
    const drop = { ...custom, id: 'custom-b', type: 'dropzone' };

    expect(journey.tileReading(custom, download)).toBe(false);
    expect(journey.tileReading(drop, download)).toBe(false);
    expect(download.buildBlob).toHaveBeenCalledWith(box, 10, 14);
    expect(download.circleBlobForBBox).toHaveBeenCalledWith(box, 10, 14);
  });

  it.each([
    ['no download core (static/offline.html)', area(), null],
    ['an unreadable bucket', area({ bucketReadable: false }), { blobFullyCached: () => true }],
    ['a record with no template', area({ template: null }), { blobFullyCached: () => true }],
    ['a record with no ground', area({ z: null }), { blobFullyCached: () => true }],
    [
      'a reading that throws',
      area(),
      {
        blobFullyCached: () => {
          throw new Error('bad template');
        },
      },
    ],
  ])('is unknown for %s', (_label, a, download) => {
    expect(journey.tileReading(a, download)).toBeNull();
  });
});

describe('your things', () => {
  it('is unknown, with no rows, when no sync recorded what to expect', () => {
    const p = panel(readings([area({ items: null })]));

    expect(p.stages.things).toEqual({ status: 'unknown', reason: 'unrecorded', rows: [] });
    expect(p.verdict).toBe('unknown');
  });

  it('is Yes with nothing to list when the area holds none of your things', () => {
    const p = panel(readings([area({ items: { trips: [], pins: [], principal: 'acct-1' } })]));

    expect(p.stages.things).toEqual({ status: 'yes', reason: 'none-here', rows: [] });
  });

  it('is unknown, not No, when the shell cache could not be listed', () => {
    const p = panel(readings(undefined, { cachesReadable: false }));

    expect(p.stages.things.status).toBe('unknown');
    expect(p.stages.things.rows.every((row) => row.status === 'unknown')).toBe(true);
  });

  it('is no stage at all for a signed-out reader, and no fault', () => {
    const p = panel(readings(undefined, { currentPrincipal: null }));

    expect(p.stages.things).toBeNull();
    expect(p.verdict).toBe('ready');
  });

  it("never names another account's things", () => {
    const p = panel(
      readings([area({ items: { ...area().items, principal: 'acct-2' } })]),
    );

    expect(p.stages.things).toEqual({ status: 'unknown', reason: 'other-account', rows: [] });
  });

  it('never shows A the names B synced into an area A downloaded (SNOW-1308 review)', () => {
    // A downloaded the area; B signed in on this device and pressed Sync
    // now, so the record holds B's names under A's `principal`. The guard
    // is the principal stamped WITH the names, not the area's own.
    const synced = area({ principal: 'acct-A', items: { ...area().items, principal: 'acct-B' } });

    const asA = panel(readings([synced], { currentPrincipal: 'acct-A' }));
    const asB = panel(readings([synced], { currentPrincipal: 'acct-B' }));

    expect(asA.stages.things).toEqual({ status: 'unknown', reason: 'other-account', rows: [] });
    expect(asB.stages.things.rows.map((row) => row.name)).toEqual(['Saturday tour', 'Hut']);
  });

  it('names nothing from an expectation stamped with no principal, or an empty one', () => {
    // An anonymous ('') download, or a record stamped before the syncing
    // principal was recorded: nobody can be shown to own these names.
    const { principal: _gone, ...unstamped } = area().items;
    for (const items of [unstamped, { ...unstamped, principal: '' }]) {
      const p = panel(readings([area({ principal: '', items })]));

      expect(p.stages.things).toEqual({ status: 'unknown', reason: 'other-account', rows: [] });
    }
  });

  it("counts a trip cached under another account's stamp as No (SNOW-1308 review)", () => {
    // The worker refuses it (`_principalMatches`), so it will not open.
    const r = readings();
    r.shellEntries = r.shellEntries.map((entry) =>
      entry.url.endsWith('route.geojson') ? { ...entry, principal: 'acct-2' } : entry,
    );
    const p = panel(r);

    expect(p.stages.things.rows[0]).toEqual({ kind: 'trip', name: 'Saturday tour', status: 'no' });
    expect(p.verdict).toBe('refresh');
  });

  it('counts a place sheet or trip page with another stamp, or none, as No', () => {
    for (const stamp of ['acct-2', null]) {
      const r = readings();
      r.shellEntries = r.shellEntries.map((entry) =>
        entry.url.includes('place-sheet') || entry.isPage ? { ...entry, principal: stamp } : entry,
      );
      const p = panel(r);

      expect(p.stages.things.rows.map((row) => row.status)).toEqual(['no', 'no']);
    }
  });

  it('reads an unread stamp as unknown, never Yes', () => {
    const r = readings();
    r.shellEntries = r.shellEntries.map((entry) =>
      entry.url.includes('place-sheet') ? { ...entry, principal: null, stampUnread: true } : entry,
    );
    const p = panel(r);

    expect(p.stages.things.rows[1]).toEqual({ kind: 'pin', name: 'Hut', status: 'unknown' });
  });

  it('needs no stamp on a public weather sheet', () => {
    // The fixture's weather sheet carries none and still counts.
    expect(panel(readings()).stages.things.rows[1].status).toBe('yes');
  });

  it('lists an item with no urls as unknown', () => {
    const p = panel(
      readings([
        area({ items: { trips: [{ uuid: 't9', name: 'X', urls: [] }], pins: [], principal: 'acct-1' } }),
      ]),
    );

    expect(p.stages.things.rows).toEqual([{ kind: 'trip', name: 'X', status: 'unknown' }]);
  });
});

describe("the conditions stage's problem-terrain line (SNOW-1379)", () => {
  const DAYS = ['/api/problem-terrain/?d=2026-09-11', '/api/problem-terrain/?d=2026-09-12'];

  /** An area downloaded with terrain tiles whose last sync saved `DAYS`. */
  function terrainArea(overrides) {
    return area({ terrain: true, items: { ...area().items, problemTerrain: DAYS }, ...(overrides || {}) });
  }

  /** Readings holding `held` of the feed's days. */
  function withDays(areas, held, overrides) {
    const r = readings(areas, overrides);
    r.shellEntries = r.shellEntries.concat(held.map((url) => ({ url: ORIGIN + url })));
    return r;
  }

  it('counts every saved day as held, and says Yes', () => {
    const p = panel(withDays([terrainArea()], DAYS));

    expect(p.stages.conditions).toEqual({
      status: 'yes',
      terrain: { status: 'yes', held: 2, total: 2 },
    });
    expect(p.verdict).toBe('ready');
  });

  it('turns a missing day into a No stage and "Needs a refresh" (SNOW-1387)', () => {
    const p = panel(withDays([terrainArea()], DAYS.slice(0, 1)));

    expect(p.stages.conditions).toEqual({
      status: 'no',
      reason: 'terrain',
      terrain: { status: 'no', held: 1, total: 2 },
    });
    expect(p.verdict).toBe('refresh');
  });

  it("keeps a stale stage's own reason over a missing day", () => {
    const stale = terrainArea({ contentAt: '2026-09-08T07:00:00.000Z' });
    const p = panel(withDays([stale], DAYS.slice(0, 1)));

    expect(p.stages.conditions.status).toBe('no');
    expect(p.stages.conditions.reason).toBe('stale');
    expect(p.verdict).toBe('refresh');
  });

  it('counts the feed for a signed-out reader too: it is public', () => {
    const p = panel(withDays([terrainArea()], DAYS, { currentPrincipal: null }));

    expect(p.stages.conditions.terrain).toEqual({ status: 'yes', held: 2, total: 2 });
  });

  it('is unknown when the shell cache could not be listed, and so is the stage', () => {
    const p = panel(withDays([terrainArea()], DAYS, { cachesReadable: false }));

    expect(p.stages.conditions.terrain).toEqual({ status: 'unknown', held: 0, total: 2 });
    expect(p.stages.conditions.status).not.toBe('yes');
  });

  it('is absent for an area without terrain tiles', () => {
    const p = panel(withDays([terrainArea({ terrain: false })], DAYS));

    expect('terrain' in p.stages.conditions).toBe(false);
  });

  it('is absent where the last sync saved no day, or recorded none', () => {
    const outside = terrainArea({ items: { ...area().items, problemTerrain: [] } });
    const unsynced = terrainArea({ items: area().items });

    expect('terrain' in panel(readings([outside])).stages.conditions).toBe(false);
    expect('terrain' in panel(readings([unsynced])).stages.conditions).toBe(false);
    expect('terrain' in panel(readings([terrainArea({ items: null })])).stages.conditions).toBe(
      false,
    );
  });
});

describe("the conditions stage's tours and reports lines (SNOW-1417)", () => {
  const T1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const T2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const tourUrls = (uuid) => [
    '/routes/ski-tours/' + uuid + '/feature.json',
    '/routes/ski-tours/' + uuid + '/terrain.json',
  ];
  const TOURS = [
    { uuid: T1, name: 'Pigne', urls: tourUrls(T1) },
    { uuid: T2, name: 'Rosablanche', urls: tourUrls(T2) },
  ];

  /** An area whose last sync saved two tours and two reports. */
  function publicArea(extra) {
    return area({
      items: {
        ...area().items,
        tours: TOURS,
        reports: [{ id: 'r1' }, { id: 'r2' }],
        reportsNewestAt: '2026-09-10T12:00:00Z',
        ...(extra || {}),
      },
    });
  }

  /** Readings holding the tour files `held` and the report ids `ids`. */
  function holding(held, ids, overrides) {
    const r = readings([publicArea()], overrides);
    r.shellEntries = r.shellEntries.concat(held.map((url) => ({ url: ORIGIN + url })));
    r.reportIds = ids;
    return r;
  }

  const ALL = [...tourUrls(T1), ...tourUrls(T2)];

  it('counts each tour whose two files are held, and each report, and says Yes', () => {
    const p = panel(holding(ALL, new Set(['r1', 'r2'])));

    expect(p.stages.conditions).toEqual({
      status: 'yes',
      tours: { status: 'yes', held: 2, total: 2 },
      reports: { status: 'yes', held: 2, total: 2, newestAt: '2026-09-10T12:00:00Z' },
    });
    expect(p.verdict).toBe('ready');
  });

  it('does not count a tour with only one of its files', () => {
    const p = panel(holding([...tourUrls(T1), tourUrls(T2)[0]], new Set(['r1', 'r2'])));

    expect(p.stages.conditions.tours).toEqual({ status: 'no', held: 1, total: 2 });
    expect(p.stages.conditions.status).toBe('no');
    expect(p.stages.conditions.reason).toBe('tours-missing');
    expect(p.verdict).toBe('refresh');
  });

  it('turns a report no longer in the overlay store into a No', () => {
    const p = panel(holding(ALL, new Set(['r1'])));

    expect(p.stages.conditions.reports.status).toBe('no');
    expect(p.stages.conditions.reports.held).toBe(1);
    expect(p.stages.conditions.reason).toBe('reports-missing');
  });

  it("keeps a stale stage's own reason over a missing tour", () => {
    const r = holding([], new Set(['r1', 'r2']));
    r.areas = [publicArea()].map((a) => ({ ...a, contentAt: '2026-09-08T07:00:00.000Z' }));

    expect(panel(r).stages.conditions.reason).toBe('stale');
  });

  it('is unknown when the overlay store could not be read', () => {
    const p = panel(holding(ALL, null));

    expect(p.stages.conditions.reports).toEqual({
      status: 'unknown',
      held: 0,
      total: 2,
      newestAt: '2026-09-10T12:00:00Z',
    });
    expect(p.stages.conditions.status).toBe('unknown');
  });

  it('is unknown for the tours when the shell cache could not be listed', () => {
    const p = panel(holding(ALL, new Set(['r1', 'r2']), { cachesReadable: false }));

    expect(p.stages.conditions.tours).toEqual({ status: 'unknown', held: 0, total: 2 });
  });

  it('counts them for a signed-out reader too: they are public', () => {
    const p = panel(holding(ALL, new Set(['r1', 'r2']), { currentPrincipal: null }));

    expect(p.stages.conditions.tours.status).toBe('yes');
    expect(p.stages.conditions.reports.status).toBe('yes');
  });

  it('says Yes for an area whose last sync found no reports in it', () => {
    const r = holding(ALL, new Set());
    r.areas = [publicArea({ reports: [], reportsNewestAt: null })];

    expect(panel(r).stages.conditions.reports).toEqual({
      status: 'yes',
      held: 0,
      total: 0,
      newestAt: null,
    });
  });

  it('draws neither line for an area synced before they were saved', () => {
    const p = panel(readings());

    expect('tours' in p.stages.conditions).toBe(false);
    expect('reports' in p.stages.conditions).toBe(false);
  });
});

describe('the panel list', () => {
  it('is empty with no areas, and with no audit core', () => {
    expect(journey.buildPanels(readings([]), {}, {})).toEqual([]);
  });

  it('offers Sync only where the host can fetch', () => {
    const r = readings();
    const here = journey.buildPanels(r, {}, { canFetch: true })[0];
    const fallback = journey.buildPanels(r, {}, { canFetch: false })[0];

    expect(here.canSync).toBe(true);
    expect(fallback.canSync).toBe(false);
  });
});

describe('the stages from a record alone (SNOW-1415)', () => {
  const TODAY = '2026-09-11T07:00:00.000Z';
  const YESTERDAY = '2026-09-10T07:00:00.000Z';
  const verified = (fields) => ({ at: TODAY, ...fields });

  it('reads a record no tile-capable page has stamped as unknown, never Yes', () => {
    const stages = journey.stagesFromRecord({ contentAt: TODAY }, NOW);
    expect(stages.map.status).toBe('unknown');
    expect(journey.verdictFor(stages)).toBe('unknown');
  });

  it('reads Ready when the map verified, today’s content is here and it all landed', () => {
    const record = {
      contentAt: TODAY,
      verified: verified({ map: 'yes', content: 'yes', principal: '42' }),
    };
    const stages = journey.stagesFromRecord(record, NOW, { signedIn: true, principal: '42' });
    expect(stages.map.status).toBe('yes');
    expect(stages.conditions.status).toBe('yes');
    expect(stages.things.status).toBe('yes');
    expect(journey.verdictFor(stages)).toBe('ready');
  });

  it('never shows one account’s things answer to another', () => {
    // Account A synced on this device; B is signed in now. A's Yes says
    // nothing about B's things, which /offline/ reports as another
    // account's.
    const record = {
      contentAt: TODAY,
      verified: verified({ map: 'yes', content: 'yes', principal: 'A' }),
    };
    const stages = journey.stagesFromRecord(record, NOW, { signedIn: true, principal: 'B' });
    expect(stages.things.status).toBe('unknown');
    expect(journey.verdictFor(stages)).not.toBe('ready');
    const unstamped = { contentAt: TODAY, verified: verified({ map: 'yes', content: 'yes' }) };
    expect(
      journey.stagesFromRecord(unstamped, NOW, { signedIn: true, principal: 'B' }).things.status,
    ).toBe('unknown');
  });

  it('sends a map No to the map, with its reason', () => {
    const record = {
      contentAt: TODAY,
      verified: verified({ map: 'no', reason: 'absent', content: 'yes' }),
    };
    const stages = journey.stagesFromRecord(record, NOW);
    expect(stages.map).toEqual({ status: 'no', reason: 'absent' });
    expect(journey.verdictFor(stages)).toBe('open-map');
  });

  it('reads yesterday’s bulletins, none at all, or a short fetch as a refresh', () => {
    const map = verified({ map: 'yes' });
    const cases = [
      [{ contentAt: YESTERDAY, verified: map }, 'stale'],
      [{ verified: map }, 'never'],
      [{ contentAt: TODAY, contentIncomplete: true, verified: map }, 'incomplete'],
    ];
    for (const [record, reason] of cases) {
      const stages = journey.stagesFromRecord(record, NOW);
      expect(stages.conditions).toEqual({ status: 'no', reason });
      expect(journey.verdictFor(stages)).toBe('refresh');
    }
  });

  it('reads an unparseable content stamp as unknown', () => {
    const stages = journey.stagesFromRecord({ contentAt: 'nonsense' }, NOW);
    expect(stages.conditions.status).toBe('unknown');
  });

  it('gives a signed-out reader no things stage at all', () => {
    const record = { contentAt: TODAY, verified: verified({ map: 'yes', content: 'no' }) };
    const stages = journey.stagesFromRecord(record, NOW);
    expect(stages.things).toBeNull();
    expect(journey.verdictFor(stages)).toBe('ready');
  });

  it('reads a content No as a things No for a signed-in reader', () => {
    const record = {
      contentAt: TODAY,
      verified: verified({ map: 'yes', content: 'no', principal: '42' }),
    };
    const stages = journey.stagesFromRecord(record, NOW, { signedIn: true, principal: '42' });
    expect(stages.things.status).toBe('no');
    expect(journey.verdictFor(stages)).toBe('refresh');
  });

  it('ignores a stamp answer that is not a Yes or a No', () => {
    const record = { contentAt: TODAY, verified: verified({ map: 'maybe', content: 7 }) };
    const stages = journey.stagesFromRecord(record, NOW, { signedIn: true });
    expect(stages.map.status).toBe('unknown');
    expect(stages.things.status).toBe('unknown');
  });
});

describe('the verified stamp (SNOW-1415)', () => {
  const AT = '2026-09-11T07:00:00.000Z';

  it('reads a map-page sync: a full tile grid is Yes, gone, failed or short is No', () => {
    expect(
      journey.verifiedFromSync({ tiles: 'ok', tilesHeld: true, content: true }, AT, '42'),
    ).toEqual({ at: AT, map: 'yes', content: 'yes', principal: '42' });
    expect(
      journey.verifiedFromSync({ tiles: 'none', tilesHeld: true, content: false }, AT, '42'),
    ).toEqual({ at: AT, map: 'yes', content: 'no', principal: '42' });
    expect(journey.verifiedFromSync({ tiles: 'failed', content: true }, AT, '42')).toEqual({
      at: AT,
      map: 'no',
      reason: 'failed',
      content: 'yes',
      principal: '42',
    });
    expect(journey.verifiedFromSync({ tiles: 'absent', content: false }, AT, '42')).toEqual({
      at: AT,
      map: 'no',
      reason: 'absent',
    });
    for (const result of [
      { tiles: 'incomplete', tilesHeld: false },
      { tiles: 'none', tilesHeld: false },
    ]) {
      expect(journey.verifiedFromSync(result, AT)).toEqual({ at: AT, map: 'no', reason: 'tiles' });
    }
  });

  it('never says Yes for the map from the render dependencies alone', () => {
    // Codex review: `'none'` means the style, sprites and glyphs are here.
    // Without a tile-grid reading that is not a claim about the tiles.
    expect(journey.verifiedFromSync({ tiles: 'none', content: true }, AT)).toBeNull();
    expect(journey.verifiedFromSync({ tiles: 'ok', tilesHeld: null }, AT)).toBeNull();
  });

  it('claims nothing about tiles it could not judge', () => {
    expect(journey.verifiedFromSync({ tiles: 'unknown', content: true }, AT, '42')).toEqual({
      at: AT,
      content: 'yes',
      principal: '42',
    });
    expect(journey.verifiedFromSync(null, AT)).toBeNull();
  });

  it('stamps a content answer only for a signed-in account', () => {
    expect(journey.verifiedFromSync({ tiles: 'unknown', content: true }, AT, '')).toBeNull();
    expect(
      journey.verifiedFromStages({ map: { status: 'yes' }, things: { status: 'yes' } }, AT),
    ).toEqual({ at: AT, map: 'yes' });
  });

  it('reads an /offline/ panel’s map and things stages, and nothing unknown', () => {
    expect(
      journey.verifiedFromStages(
        { map: { status: 'no', reason: 'tiles' }, things: { status: 'yes' } },
        AT,
        '42',
      ),
    ).toEqual({ at: AT, map: 'no', reason: 'tiles', content: 'yes', principal: '42' });
    expect(
      journey.verifiedFromStages({ map: { status: 'unknown' }, things: null }, AT, '42'),
    ).toBeNull();
  });

  it('merges a stamp field by field, keeping what the new reading did not claim', () => {
    const entry = {
      id: 'a',
      verified: { at: 'old', map: 'no', reason: 'tiles', content: 'yes', principal: 'A' },
    };
    expect(
      journey.stampVerifiedRecord(entry, { at: AT, content: 'no', principal: 'B' }).verified,
    ).toEqual({ at: AT, map: 'no', reason: 'tiles', content: 'no', principal: 'B' });
    expect(journey.stampVerifiedRecord(entry, { at: AT, map: 'yes' }).verified).toEqual({
      at: AT,
      map: 'yes',
      content: 'yes',
      principal: 'A',
    });
    expect(journey.stampVerifiedRecord(entry, null)).toBe(entry);
    expect(entry.verified.at).toBe('old');
  });
});

describe('the Downloads inventory (SNOW-1418)', () => {
  const TODAY = '2026-09-11T07:00:00.000Z';
  const YESTERDAY = '2026-09-10T07:00:00.000Z';
  const TOUR = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const ROUTE = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
  const TOUR_URLS = [
    '/routes/ski-tours/' + TOUR + '/feature.json',
    '/routes/ski-tours/' + TOUR + '/terrain.json',
  ];
  const RESORT_URLS = ['/partials/place-sheet/resort/verbier/?d=2026-09-11'];

  /** A record synced today by acct-1, with every inventory field stamped. */
  function record(overrides, items) {
    const base = area();
    return {
      contentAt: TODAY,
      verified: { at: TODAY, map: 'yes', content: 'yes', principal: 'acct-1' },
      ...(overrides || {}),
      items: {
        ...base.items,
        routes: [{ uuid: ROUTE }],
        tours: [{ uuid: TOUR, name: 'Rosablanche', urls: TOUR_URLS }],
        tourBoxes: [],
        resorts: [{ slug: 'verbier', urls: RESORT_URLS }],
        reports: [{ id: 'r1' }, { id: 'r2' }],
        reportsAt: TODAY,
        reportsNewestAt: TODAY,
        ...(items || {}),
      },
    };
  }

  /** Every stamped url, as the worker would answer for a whole area. */
  function allHeld(r) {
    return new Set(journey.inventoryUrls(r, ORIGIN));
  }

  const OVERLAYS = {
    routes: new Set([ROUTE]),
    reports: new Set(['r1', 'r2']),
  };

  function inventory(r, options) {
    return journey.inventoryFor(r, NOW, {
      signedIn: true,
      principal: 'acct-1',
      origin: ORIGIN,
      ...(options || {}),
    });
  }

  const byKey = (rows) => Object.fromEntries(rows.map((row) => [row.key, row]));

  it('lists every row, with its count and its stamp, when it all landed', () => {
    const r = record();
    const rows = inventory(r, { held: allHeld(r), overlayIds: OVERLAYS });
    expect(rows.map((row) => row.key)).toEqual([
      'map',
      'bulletin',
      'weather',
      'routes',
      'trips',
      'places',
      'tours',
      'resorts',
      'reports',
    ]);
    expect(rows.every((row) => row.status === 'yes')).toBe(true);
    const k = byKey(rows);
    expect(k.bulletin.at).toBe(TODAY);
    expect(k.weather.at).toBe(TODAY);
    expect([k.routes.count, k.trips.count, k.places.count]).toEqual([1, 1, 1]);
    expect([k.tours.count, k.resorts.count, k.reports.count]).toEqual([1, 1, 2]);
  });

  it('marks a stale bulletin and its weather partial, and the content they ride with', () => {
    const r = record({ contentAt: YESTERDAY });
    const k = byKey(inventory(r));
    expect(k.bulletin.status).toBe('partial');
    expect(k.weather.status).toBe('partial');
    expect(k.bulletin.at).toBe(YESTERDAY);
    // Nothing read about the tours: they follow the conditions stage.
    expect(k.tours.status).toBe('partial');
  });

  it('marks a missing map as missing, not partial', () => {
    const r = record({ verified: { at: TODAY, map: 'no', reason: 'tiles' } });
    expect(byKey(inventory(r)).map.status).toBe('missing');
  });

  it('reads an unchecked map as unknown', () => {
    const r = record({ verified: undefined });
    expect(byKey(inventory(r)).map.status).toBe('unknown');
  });

  it('lists no routes, trips or places for a reader who is signed out', () => {
    const keys = inventory(record(), { signedIn: false, principal: '' }).map((row) => row.key);
    expect(keys).toEqual(['map', 'bulletin', 'weather', 'tours', 'resorts', 'reports']);
  });

  it('lists none of another account’s things', () => {
    const keys = inventory(record(), { principal: 'acct-2' }).map((row) => row.key);
    expect(keys).not.toContain('routes');
    expect(keys).not.toContain('trips');
    expect(keys).not.toContain('places');
  });

  it('lists only the map, bulletin and weather for a record synced before the stamp', () => {
    const keys = inventory({ contentAt: TODAY }).map((row) => row.key);
    expect(keys).toEqual(['map', 'bulletin', 'weather']);
  });

  it('lists no saved places row when the area has none', () => {
    const keys = inventory(record({}, { pins: [] })).map((row) => row.key);
    expect(keys).toContain('trips');
    expect(keys).not.toContain('places');
  });

  it('marks a row partial when one of its things is not held', () => {
    const r = record();
    const held = allHeld(r);
    held.delete(ORIGIN + TOUR_URLS[1]);
    const k = byKey(
      inventory(r, {
        held: held,
        overlayIds: { routes: new Set(), reports: new Set(['r1']) },
      }),
    );
    expect(k.tours.status).toBe('partial');
    expect(k.routes.status).toBe('partial');
    expect(k.reports.status).toBe('partial');
    expect(k.trips.status).toBe('yes');
    expect(k.resorts.status).toBe('yes');
  });

  it('follows the stage where nothing was read, and never says No on its own', () => {
    const r = record({
      verified: { at: TODAY, map: 'yes', content: 'no', principal: 'acct-1' },
    });
    const k = byKey(inventory(r));
    // The things stage says No, so the reader's rows read partial…
    expect(k.trips.status).toBe('partial');
    expect(k.routes.status).toBe('partial');
    // …and the public ones follow today's conditions, which are a yes.
    expect(k.tours.status).toBe('yes');
    expect(k.reports.status).toBe('yes');
  });

  it('counts a resort with no sheet urls by its stage', () => {
    const r = record({}, { resorts: [{ slug: 'verbier', urls: [] }] });
    expect(byKey(inventory(r, { held: new Set() })).resorts.status).toBe('yes');
  });

  it('names each stamped url once, absolute', () => {
    const r = record();
    const urls = journey.inventoryUrls(r, ORIGIN);
    expect(urls).toContain(ORIGIN + TOUR_URLS[0]);
    expect(urls).toContain(ORIGIN + RESORT_URLS[0]);
    expect(urls).toContain(ORIGIN + '/trips/' + TRIP + '/');
    expect(new Set(urls).size).toBe(urls.length);
    expect(journey.inventoryUrls({}, ORIGIN)).toEqual([]);
  });
});
