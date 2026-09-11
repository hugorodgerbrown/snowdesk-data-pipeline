/*
 * tests/js/test_offline_audit_core.js — Vitest unit tests for
 * static/js/offline_audit_core.js (SNOW-907).
 *
 * The report exists because a user was told, by every surface that had
 * an opinion, that their device was ready for a journey it was not. So
 * the cases here are almost entirely the ones where a naive reading says
 * yes: a map page cached under another account, an area whose tiles are
 * all present and whose TileJSON is not, a record with no bucket, a
 * bucket with no record, and every reading the browser can simply refuse
 * to take.
 *
 * The one property worth stating up front: this module must never
 * upgrade an absence into a pass. `unknown` is a state, and the tests
 * below assert it is reached rather than rounded to `ok`.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

/** A readings object with every field in its healthiest state. */
function healthy(overrides) {
  return Object.assign(
    {
      now: '2026-09-11T08:00:00.000Z',
      online: true,
      networkMode: 'auto',
      serviceWorker: {
        supported: true,
        registered: true,
        controlled: true,
        waiting: false,
      },
      storage: {
        usage: 50 * 1024 * 1024,
        quota: 500 * 1024 * 1024,
        persisted: true,
      },
      shellEntries: [
        { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
        {
          url: 'https://snowdesk.info/static/js/map.abc.js',
          isPage: false,
          principal: null,
        },
        {
          url: 'https://snowdesk.info/static/css/output.abc.css',
          isPage: false,
          principal: null,
        },
        {
          url: 'https://snowdesk.info/api/ratings/?c=CH',
          isPage: false,
          principal: null,
        },
      ],
      currentPrincipal: null,
      mapPath: '/',
      areas: [
        {
          id: 'region-ch-4115',
          name: 'Martigny',
          bytes: 12 * 1024 * 1024,
          deps: ['https://tiles.example/style.json', 'https://tiles.example/source.json'],
          bucketPresent: true,
          entries: [
            'https://tiles.example/style.json',
            'https://tiles.example/source.json',
            'https://tiles.example/12/1/1.pbf',
            'https://tiles.example/12/1/2.pbf',
          ],
        },
      ],
      orphanBuckets: [],
      stores: {
        'data:favourites': 3,
        'data:map_overlays': 2,
        'data:panel_rows': 1,
      },
      mutations: { count: 0 },
      dbAvailable: true,
    },
    overrides || {},
  );
}

/** One check from a built report, by id. */
function check(report, id) {
  let found = null;
  report.sections.forEach((section) => {
    section.checks.forEach((entry) => {
      if (entry.id === id) found = entry;
    });
  });
  return found;
}

beforeAll(async () => {
  await import('../../static/js/offline_audit_core.js');
  core = window.pwaOfflineAuditCore;
});

describe('the verdict', () => {
  it('passes a device that holds the page, the scripts and a complete area', () => {
    const report = core.buildReport(healthy(), {});
    expect(report.verdict.status).toBe('ok');
  });

  it('fails when the map page was never cached', () => {
    const report = core.buildReport(healthy({ shellEntries: [] }), {});
    expect(report.verdict.status).toBe('fail');
    expect(check(report, 'map-page').reason).toBe('absent');
  });

  it('fails DIFFERENTLY when the cached page belongs to another account', () => {
    // The reported journey. Every other surface says the region is
    // downloaded and it is; the page the region would be drawn on is in
    // the cache and the worker will refuse to serve it, silently. The
    // two failures need different copy because they need different
    // actions, so the verdict reads a machine-readable reason rather
    // than the translated value.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          {
            url: 'https://snowdesk.info/',
            isPage: true,
            principal: 'acct-aaa',
          },
        ],
        currentPrincipal: 'acct-bbb',
      }),
      {},
    );
    expect(report.verdict.status).toBe('fail');
    expect(check(report, 'map-page').reason).toBe('principal');
    expect(report.verdict.text).toBe('verdict-other-account');
  });

  it('fails when there is no worker, ahead of everything below it', () => {
    const report = core.buildReport(
      healthy({
        serviceWorker: {
          supported: true,
          registered: false,
          controlled: false,
          waiting: false,
        },
        shellEntries: [],
      }),
      {},
    );
    expect(report.verdict.text).toBe('verdict-no-worker');
  });

  it('warns rather than fails when the page is saved but no area is', () => {
    const report = core.buildReport(healthy({ areas: [] }), {});
    expect(report.verdict.status).toBe('warn');
  });
});

describe('the map-page check', () => {
  it('ignores the query string, as the worker does', () => {
    // map.js writes ?d=YYYY-MM-DD with history.replaceState while the
    // user scrubs; those URLs are never fetched and never cached, and
    // sw.js's _networkFirstFallback matches with ignoreSearch before
    // giving up. An exact match here would report "not saved" for a
    // device that opens the page perfectly well.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          {
            url: 'https://snowdesk.info/?d=2026-02-16',
            isPage: true,
            principal: 'anonymous',
          },
        ],
      }),
      {},
    );
    expect(check(report, 'map-page').status).toBe('ok');
  });

  it('refuses an entry with no stamp at all', () => {
    // Written before SNOW-624, or by a path that did not stamp. The
    // worker will never serve it, so reporting it as saved would be the
    // same lie pointing the other way.
    const report = core.buildReport(
      healthy({
        shellEntries: [{ url: 'https://snowdesk.info/', isPage: true, principal: null }],
      }),
      {},
    );
    expect(check(report, 'map-page').status).toBe('fail');
  });

  it("treats a null current principal as 'anonymous'", () => {
    expect(core.principalMatches('anonymous', null)).toBe(true);
    expect(core.principalMatches('unknown', 'unknown')).toBe(false);
  });
});

describe('an area', () => {
  it('is incomplete when its tiles are all there and a dependency is not', () => {
    // SNOW-843's whole bug class: without the TileJSON, MapLibre cannot
    // learn a single tile URL, so a perfect pinned tile set is
    // unreachable and the map draws nothing.
    const state = core.areaState({
      id: 'region-a',
      deps: ['https://t/style.json', 'https://t/source.json'],
      bucketPresent: true,
      entries: ['https://t/style.json', 'https://t/1/1/1.pbf'],
    });
    expect(state.status).toBe('warn');
    expect(state.missingDeps).toEqual(['https://t/source.json']);
  });

  it('is unknown, not ok, when the record names no dependencies', () => {
    const state = core.areaState({
      id: 'region-a',
      deps: [],
      bucketPresent: true,
      entries: ['https://t/1/1/1.pbf'],
    });
    expect(state.status).toBe('unknown');
  });

  it('passes a shared base layer with no dependencies, unlike every other area', () => {
    // A base layer is tiles and nothing else — the area downloads that
    // read it carry the style, TileJSON and sprite between them
    // (SNOW-856). Its empty `deps` means "there is nothing to record",
    // not "nothing was recorded", and reporting it unverifiable would
    // send the user off to re-download something that is complete.
    const base = core.areaState({
      id: 'base-openfreemap_liberty',
      kind: 'base',
      deps: [],
      bucketPresent: true,
      entries: ['https://t/4/1/1.pbf'],
    });
    expect(base.status).toBe('ok');
  });

  it('names a base layer for what it does, not by its style key', () => {
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'base-openfreemap_liberty',
            kind: 'base',
            name: 'openfreemap_liberty',
            basemapKey: 'openfreemap_liberty',
            deps: [],
            bucketPresent: true,
            entries: ['https://t/4/1/1.pbf'],
          },
        ],
      }),
      { 'label-base-layer': 'Zoomed-out overview (%(basemap)s)' },
    );
    expect(check(report, 'area-base-openfreemap_liberty').label).toBe(
      'Zoomed-out overview (openfreemap_liberty)',
    );
  });

  it('fails when the record exists and the bucket does not', () => {
    const state = core.areaState({
      id: 'region-a',
      deps: [],
      bucketPresent: false,
      entries: [],
    });
    expect(state.status).toBe('fail');
  });

  it('counts tiles as the entries that are not declared dependencies', () => {
    const state = core.areaState({
      id: 'region-a',
      deps: ['https://t/style.json'],
      bucketPresent: true,
      entries: ['https://t/style.json', 'https://t/1/1/1.pbf', 'https://t/1/1/2.pbf'],
    });
    expect(state.tiles).toBe(2);
    expect(state.supporting).toBe(1);
  });

  it('surfaces a pinned bucket no record names', () => {
    const report = core.buildReport(healthy({ orphanBuckets: ['custom-abc'] }), {});
    expect(check(report, 'orphan-custom-abc').status).toBe('warn');
  });
});

describe('degraded readings', () => {
  it('builds a report from nothing at all rather than throwing', () => {
    const report = core.buildReport(undefined, undefined);
    expect(report.sections).toHaveLength(5);
    expect(report.verdict.status).toBe('fail');
  });

  it('reports an unreadable database as unknown, never as empty', () => {
    const report = core.buildReport(healthy({ dbAvailable: false, stores: {} }), {});
    const section = report.sections.filter((entry) => entry.id === 'data')[0];
    expect(section.status).toBe('unknown');
  });

  it('reports a browser with no storage estimate as unknown', () => {
    const report = core.buildReport(healthy({ storage: null }), {});
    expect(check(report, 'storage').status).toBe('unknown');
  });

  it('warns when the origin is close to its quota', () => {
    const report = core.buildReport(
      healthy({ storage: { usage: 95, quota: 100, persisted: true } }),
      {},
    );
    expect(check(report, 'storage').status).toBe('warn');
  });

  it('warns when storage is not protected from eviction', () => {
    const report = core.buildReport(
      healthy({ storage: { usage: 1, quota: 100, persisted: false } }),
      {},
    );
    expect(check(report, 'persisted').status).toBe('warn');
  });
});

describe('statuses', () => {
  it('ranks unknown below ok, so an unreadable check never drags a section down', () => {
    expect(core.worst(['ok', 'unknown'])).toBe('ok');
    expect(core.worst(['ok', 'warn'])).toBe('warn');
    expect(core.worst(['warn', 'fail'])).toBe('fail');
    expect(core.worst([])).toBe('unknown');
  });
});

describe('missingFrom', () => {
  // The same contract as pwaBasemapDownloadCore.missingRenderDependencies,
  // which this deliberately restates rather than imports — see the
  // function's own docstring. These are the cases both must agree on.
  it('answers [] for an empty list, which means unknown and not a pass', () => {
    expect(core.missingFrom([], ['a'])).toEqual([]);
  });

  it('preserves order and deduplicates', () => {
    expect(core.missingFrom(['b', 'a', 'b'], new Set(['a']))).toEqual(['b']);
  });

  it('ignores non-string entries rather than reporting them missing', () => {
    expect(core.missingFrom(['a', null, ''], ['a'])).toEqual([]);
  });
});

describe('entry classification', () => {
  it.each([
    ['https://snowdesk.info/', 'page'],
    ['https://snowdesk.info/ch-4115/martigny/', 'page'],
    ['https://snowdesk.info/static/js/map.abc123.js', 'script'],
    ['https://snowdesk.info/static/css/output.abc.css', 'style'],
    ['https://snowdesk.info/api/ratings/?country=CH', 'feed'],
    ['https://snowdesk.info/static/fonts/x.woff2', 'font'],
    ['https://snowdesk.info/static/icons/pwa/icon-192.png', 'image'],
  ])('classifies %s as %s', (url, expected) => {
    expect(core.classifyEntry(url)).toBe(expected);
  });
});

describe('the text form', () => {
  it('carries the verdict, every section and every check', () => {
    const report = core.buildReport(healthy(), {});
    const text = core.reportText(report, {
      url: 'https://snowdesk.info/account/settings/',
    });
    expect(text).toContain('OK: verdict-ok');
    expect(text).toContain('## section-maps');
    expect(text).toContain('Martigny');
    // Spelled out rather than coloured: a pasted report has no CSS, and
    // this is the only way the data leaves the device.
    expect(text).toContain('[ok]');
  });
});

describe('byte formatting', () => {
  it.each([
    [0, '—'],
    [512, '512 B'],
    [2048, '2 KB'],
    [5 * 1024 * 1024, '5.0 MB'],
    [50 * 1024 * 1024, '50 MB'],
    [null, '—'],
  ])('formats %s as %s', (input, expected) => {
    expect(core.formatBytes(input)).toBe(expected);
  });
});
