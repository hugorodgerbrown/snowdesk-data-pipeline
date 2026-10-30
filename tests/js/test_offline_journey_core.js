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
