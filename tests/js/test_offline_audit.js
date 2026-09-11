/*
 * tests/js/test_offline_audit.js — Vitest tests for the collector and DOM
 * half of static/js/offline_audit.js (SNOW-907).
 *
 * The core's tests cover the arithmetic. What is only observable here is
 * the half the core is deliberately kept away from: which stores are
 * read, which cache buckets are opened, and — the one that matters —
 * that a probe for a downloaded area never CREATES the bucket it is
 * probing for. `caches.open` creates on miss, so a report asking "is this
 * area still here" with `open` would answer "no, but it is now", and
 * every later run would then read the area as merely empty rather than
 * gone. The fixture asserts `caches.has` gates every bucket read.
 *
 * The database is the real one — `db.js` against `fake-indexeddb`, from
 * tests/js/setup.js — because the module opens it by name with no
 * version and reads stores it did not create, and a hand-rolled stub
 * would not exercise either.
 *
 * `caches` and `navigator.storage` do not exist in jsdom; both are
 * stubbed to the surface this module uses, the same trade-off
 * test_map_download_eviction.js makes.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/offline_audit_core.js';
import '../../static/js/offline_audit.js';

const MB = 1024 * 1024;

let audit;
let cachesStub;

/**
 * An in-memory Cache Storage holding `{name: [{url, headers}]}`.
 *
 * `open` records every name it is asked for, so a test can assert the
 * module never reached for a bucket without checking `has` first.
 */
function installCachesStub(buckets) {
  const opened = [];
  const stub = {
    opened,
    keys: vi.fn(async () => Object.keys(buckets)),
    has: vi.fn(async (name) => Object.prototype.hasOwnProperty.call(buckets, name)),
    open: vi.fn(async (name) => {
      opened.push(name);
      const entries = buckets[name] || [];
      return {
        keys: async () => entries.map((entry) => ({ url: entry.url })),
        match: async (request) => {
          const url = typeof request === 'string' ? request : request.url;
          const found = entries.filter((entry) => entry.url === url)[0];
          if (!found) return undefined;
          return {
            headers: { get: (name2) => (found.headers || {})[name2] || null },
          };
        },
      };
    }),
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  cachesStub = stub;
  return stub;
}

/** Seed `meta:app` through the real db.js wrapper. */
async function seedMeta(rows) {
  const db = window.pwaDb;
  for (const [key, value] of Object.entries(rows)) {
    await db.put('meta:app', { key, value });
  }
}

beforeAll(async () => {
  await import('../../static/js/db.js');
  audit = window.pwaOfflineAudit;
  Object.defineProperty(navigator, 'storage', {
    value: {
      estimate: async () => ({ usage: 10 * MB, quota: 500 * MB }),
      persisted: async () => true,
    },
    configurable: true,
  });
});

beforeEach(async () => {
  // No controller in jsdom, so `liveShellCacheName` resolves null at once
  // and the collector falls back to reading every `snowdesk-shell-*`
  // bucket — the documented degraded path, and the one every test here
  // exercises.
  installCachesStub({});
  const db = window.pwaDb;
  for (const key of ['basemap.regions', 'basemap.customAreas', 'basemap.baseLayers']) {
    await db.delete('meta:app', key);
  }
});

describe('the shell-cache reading', () => {
  it('reads the principal stamp off pages and leaves assets alone', async () => {
    installCachesStub({
      'snowdesk-shell-abc': [
        {
          url: 'https://snowdesk.info/',
          headers: { 'X-SW-Principal': 'acct-1' },
        },
        { url: 'https://snowdesk.info/static/js/map.abc.js', headers: {} },
      ],
    });

    const readings = await audit.collect();

    const page = readings.shellEntries.filter((entry) => entry.isPage)[0];
    expect(page.principal).toBe('acct-1');
    // One `match` per page and none for the script: on a device holding
    // hundreds of hashed assets, opening every Response to read a header
    // would turn a listing into one round trip per entry.
    const asset = readings.shellEntries.filter((entry) => !entry.isPage)[0];
    expect(asset.principal).toBeNull();
  });

  it('reports an unstamped page as unstamped rather than guessing', async () => {
    installCachesStub({
      'snowdesk-shell-abc': [{ url: 'https://snowdesk.info/', headers: {} }],
    });

    const readings = await audit.collect();

    expect(readings.shellEntries[0].principal).toBeNull();
  });
});

describe('the pinned-bucket probe', () => {
  it('never opens a bucket it has not first confirmed exists', async () => {
    // The whole reason `readBucket` calls `caches.has` first. `open`
    // CREATES on miss, so probing with it would manufacture the bucket
    // and turn "this area is gone" into "this area is empty" for every
    // run after the first.
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await seedMeta({
      'basemap.regions': [{ region_id: 'CH-4115', name: 'Martigny', bytes: 1, deps: [] }],
    });

    const readings = await audit.collect();

    expect(readings.areas[0].bucketPresent).toBe(false);
    expect(cachesStub.opened).not.toContain('snowdesk-basemap-pinned-region-CH-4115');
  });

  it('reads the entries of a bucket that is there', async () => {
    installCachesStub({
      'snowdesk-shell-abc': [],
      'snowdesk-basemap-pinned-region-CH-4115': [
        { url: 'https://t/style.json' },
        { url: 'https://t/12/1/1.pbf' },
      ],
    });
    await seedMeta({
      'basemap.regions': [
        {
          region_id: 'CH-4115',
          name: 'Martigny',
          bytes: 5 * MB,
          deps: ['https://t/style.json'],
        },
      ],
    });

    const readings = await audit.collect();

    expect(readings.areas[0].bucketPresent).toBe(true);
    expect(readings.areas[0].entries).toHaveLength(2);
  });
});

describe('the area records', () => {
  it('normalises regions, custom areas and shared base layers into one list', async () => {
    // The base layers are included because they are stored, they take
    // space, and no other surface names them. A reader comparing this
    // against the Manage downloads sheet should not find tiles the sheet
    // never mentions and conclude the report is wrong.
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await seedMeta({
      'basemap.regions': [{ region_id: 'CH-4115', name: 'Martigny', deps: [] }],
      'basemap.customAreas': [{ id: 'custom-abc', name: 'Custom area', deps: [] }],
      'basemap.baseLayers': [
        { basemapKey: 'swisstopo_winter', name: 'Overview map', bytes: 1 },
      ],
    });

    const readings = await audit.collect();

    expect(readings.areas.map((area) => area.id).sort()).toEqual([
      'base-swisstopo_winter',
      'custom-abc',
      'region-CH-4115',
    ]);
  });

  it('reads WHICH overlays are cached, not how many rows there are', async () => {
    // The store holds one row per resource, so a count answers nothing a
    // user asked; the key is what makes "will the weather show" a Yes.
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await window.pwaDb.put('data:map_overlays', {
      key: 'weather',
      geojson: {},
      cached_at: '2026-09-11T08:00:00Z',
    });

    const readings = await audit.collect();

    expect(readings.overlayKeys).toContain('weather');
  });

  it('carries a drop zone’s type through, so the report can group by it', async () => {
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await seedMeta({
      'basemap.customAreas': [
        { id: 'custom-1', type: 'dropzone', name: 'La Chaux', deps: [] },
        { id: 'custom-2', name: 'Area 2', deps: [] },
      ],
    });

    const readings = await audit.collect();

    const byId = Object.fromEntries(readings.areas.map((area) => [area.id, area.type]));
    expect(byId['custom-1']).toBe('dropzone');
    expect(byId['custom-2']).toBe('custom');
  });

  it('names a pinned bucket no record accounts for', async () => {
    installCachesStub({
      'snowdesk-shell-abc': [],
      'snowdesk-basemap-pinned-region-CH-9999': [{ url: 'https://t/1.pbf' }],
    });

    const readings = await audit.collect();

    expect(readings.orphanBuckets).toEqual(['region-CH-9999']);
  });
});

describe('rendering', () => {
  it('emits status as a data attribute and no class strings at all', async () => {
    // The contract both hosts depend on: /account/settings/ styles this
    // with design tokens and static/offline.html with its own inline
    // rules, and neither can work if the module bakes in the other's.
    const target = document.createElement('div');
    const report = window.pwaOfflineAuditCore.buildReport({ shellEntries: [] }, {});

    audit.render(target, report, {});

    expect(target.querySelector('[data-audit-summary]').dataset.auditStatus).toBe('fail');
    expect(target.querySelectorAll('[data-audit-section]').length).toBeGreaterThan(0);
    expect(target.querySelectorAll('[class]')).toHaveLength(0);
  });

  it('gives every log row exactly two cells and no explanation', async () => {
    // The rule the redesign turned on: a row is label and answer. Per-row
    // helper text made the panel three times taller, turned scanning into
    // reading, and said one shared remedy once per row — all of which now
    // lives in the summary instead.
    const target = document.createElement('div');
    const report = window.pwaOfflineAuditCore.buildReport({ dbAvailable: true }, {});

    audit.render(target, report, {});

    const rows = target.querySelectorAll('[data-audit-check]');
    expect(rows.length).toBeGreaterThan(0);
    rows.forEach((row) => {
      expect(row.children).toHaveLength(2);
      expect(row.querySelector('[data-audit-label]')).not.toBeNull();
      expect(row.querySelector('[data-audit-value]')).not.toBeNull();
      // The one thing a row must never regrow.
      expect(row.querySelector('[data-audit-detail]')).toBeNull();
    });
  });

  it('paints the summary and the tally under the log', async () => {
    const target = document.createElement('div');
    const t = { 'counts-line': '%(yes)s of %(total)s available offline' };
    const report = window.pwaOfflineAuditCore.buildReport({ dbAvailable: true }, t);

    audit.render(target, report, t);

    expect(target.querySelector('[data-audit-verdict]').textContent).toBe(
      report.verdict.text,
    );
    expect(target.querySelector('[data-audit-counts]').textContent).toBe(
      `${report.counts.yes} of ${report.counts.total} available offline`,
    );
  });

  it('writes every value with textContent, never innerHTML', async () => {
    // An area's name comes from the regions GeoJSON or from whatever the
    // user typed when renaming it — neither is markup, and neither should
    // be able to become markup.
    const target = document.createElement('div');
    const report = window.pwaOfflineAuditCore.buildReport(
      {
        areas: [
          {
            id: 'x',
            kind: 'region',
            name: '<img src=x onerror=alert(1)>',
            deps: [],
            bucketPresent: true,
            entries: ['a'],
          },
        ],
      },
      {},
    );

    audit.render(target, report, {});

    expect(target.querySelector('img')).toBeNull();
    expect(target.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});

describe('the build', () => {
  it('paints every row unanswered first, then fills each answer in', async () => {
    // The confidence argument: the whole list of questions is on screen
    // before the first answer lands, so the reader watches each one get
    // settled rather than watching rows appear out of nothing.
    const target = document.createElement('div');
    const t = { 'answer-pending': '…', 'answer-yes': 'Yes', 'answer-no': 'No' };
    const report = window.pwaOfflineAuditCore.buildReport({ dbAvailable: true }, t);

    const building = audit.build(target, report, t);

    const rows = target.querySelectorAll('[data-audit-check]');
    expect(rows.length).toBe(report.counts.total);
    rows.forEach((row) => {
      expect(row.getAttribute('data-audit-status')).toBe('pending');
      expect(row.querySelector('[data-audit-value]').textContent).toBe('…');
    });
    // Nothing is concluded until every row has been answered.
    expect(target.querySelector('[data-audit-summary]')).toBeNull();

    await building;

    expect(
      target.querySelectorAll('[data-audit-check][data-audit-status="pending"]'),
    ).toHaveLength(0);
    expect(target.querySelector('[data-audit-summary]')).not.toBeNull();
  });

  it('finds a row whose id carries a colon', async () => {
    // Area rows are keyed `area:region-CH-4115`, and a colon in a
    // selector is a pseudo-class — an unescaped lookup throws and the
    // answer never lands.
    const target = document.createElement('div');
    const t = { 'answer-pending': '…', 'answer-yes': 'Yes' };
    const report = window.pwaOfflineAuditCore.buildReport(
      {
        dbAvailable: true,
        areas: [
          {
            id: 'region-CH-4115',
            kind: 'region',
            name: 'Martigny',
            deps: ['https://t/a.json'],
            bucketPresent: true,
            entries: ['https://t/a.json', 'https://t/1.pbf'],
          },
        ],
      },
      t,
    );

    await audit.build(target, report, t);

    const row = target.querySelector('[data-audit-row="area:region-CH-4115"]');
    expect(row.getAttribute('data-audit-status')).toBe('yes');
    expect(row.querySelector('[data-audit-value]').textContent).toBe('Yes');
  });
});
