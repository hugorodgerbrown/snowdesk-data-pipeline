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

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

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
          // SNOW-912: a body as well as headers. The collector reads the
          // map page's HTML to learn which modules that page boots from,
          // and a stub with no body would make every page unreadable.
          const response = {
            headers: { get: (name2) => (found.headers || {})[name2] || null },
            text: async () => {
              if (found.onText) found.onText();
              return found.body || '';
            },
            clone: () => response,
          };
          return response;
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
  // SNOW-913: the basemap choice is per-device state that would otherwise
  // leak from one test into the next.
  window.localStorage.removeItem('snowdesk.map.basemap');
  // No controller in jsdom, so `liveShellCacheName` resolves null at once
  // and the collector falls back to reading every `snowdesk-shell-*`
  // bucket — the documented degraded path, and the one every test here
  // exercises.
  installCachesStub({});
  const db = window.pwaDb;
  for (const key of ['basemap.regions', 'basemap.customAreas', 'basemap.baseLayers']) {
    await db.delete('meta:app', key);
  }
  // SNOW-914: the overlay rows are read for their contents now, so a row
  // left behind by one test is a reading the next one did not ask for.
  for (const key of ['favourites', 'community_reports', 'weather', 'routes']) {
    await db.delete('data:map_overlays', key);
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

  it('reads the map page’s own modules out of its cached HTML', async () => {
    // What makes "The app opens" an answer about the page rather than
    // about the cache in general. Seeded at the document's own origin,
    // which is what a shell cache holds — the page's relative hrefs
    // resolve against it, and in a browser the two always coincide.
    const origin = window.location.origin;
    installCachesStub({
      'snowdesk-shell-abc': [
        {
          url: `${origin}/`,
          headers: { 'X-SW-Principal': 'anonymous' },
          body: '<link rel="stylesheet" href="/static/css/o.css"><script src="/static/js/map.js"></script>',
        },
      ],
    });

    const readings = await audit.collect();

    expect(readings.mapDependencies).toEqual([
      `${origin}/static/css/o.css`,
      `${origin}/static/js/map.js`,
    ]);
  });

  it('reads no body but the map page’s', async () => {
    // One extra body read, on one entry. A device holding hundreds of
    // cached pages must not pay a read for each.
    const read = [];
    installCachesStub({
      'snowdesk-shell-abc': [
        { url: 'https://snowdesk.info/', headers: {}, body: '', onText: () => read.push('/') },
        {
          url: 'https://snowdesk.info/ch-4115/verbier/2026-02-16/',
          headers: {},
          body: '<script src="/static/js/bulletin.js"></script>',
          onText: () => read.push('bulletin'),
        },
      ],
    });

    await audit.collect();

    expect(read).toEqual(['/']);
  });

  it('keeps the stamp when the body cannot be read', async () => {
    // Two reads off one response, and a failure of the second says
    // nothing about the first. Folded together, an unreadable body would
    // report a perfectly good page as unstamped — which the worker treats
    // as "never serve this".
    installCachesStub({
      'snowdesk-shell-abc': [
        {
          url: 'https://snowdesk.info/',
          headers: { 'X-SW-Principal': 'acct-1' },
          onText: () => {
            throw new Error('unreadable');
          },
        },
      ],
    });

    const readings = await audit.collect();

    expect(readings.shellEntries[0].principal).toBe('acct-1');
    expect(readings.mapDependencies).toBeNull();
  });

  it('reads the basemap the reader is looking at', async () => {
    // SNOW-913: the stored choice wins. A reader on Swisstopo must not be
    // answered about OpenFreeMap.
    window.localStorage.setItem('snowdesk.map.basemap', 'swisstopo_winter');
    installCachesStub({});

    const readings = await audit.collect();

    expect(readings.selectedBasemap).toBe('swisstopo_winter');
  });

  it('falls back to the deployed default when nobody has chosen', async () => {
    // localStorage is written only when someone opens the picker and picks,
    // so an untouched device is looking at settings.BASEMAP with nothing
    // stored to say so. The panel carries it.
    window.localStorage.removeItem('snowdesk.map.basemap');
    const root = document.createElement('div');
    root.setAttribute('data-offline-audit', '');
    root.setAttribute('data-default-basemap-key', 'ign_plan');
    installCachesStub({});

    const readings = await audit.collect(root);

    expect(readings.selectedBasemap).toBe('ign_plan');
  });

  it('ignores a stored basemap the cached page no longer offers', async () => {
    // SNOW-913, from the review: map.js validates the stored key against
    // the catalogue it renders and falls back to the deployed default, so
    // a retired style left behind in localStorage is not what the reader
    // is looking at.
    window.localStorage.setItem('snowdesk.map.basemap', 'retired_style');
    const origin = window.location.origin;
    installCachesStub({
      'snowdesk-shell-abc': [
        {
          url: `${origin}/`,
          headers: { 'X-SW-Principal': 'anonymous' },
          body:
            '<div id="map" data-default-basemap-key="openfreemap_liberty"></div>' +
            '<button data-basemap-key="openfreemap_liberty"></button>' +
            '<button data-basemap-key="swisstopo_winter"></button>',
        },
      ],
    });

    const readings = await audit.collect();

    expect(readings.selectedBasemap).toBe('openfreemap_liberty');
  });

  it('names no basemap where neither is knowable', async () => {
    // static/offline.html, on a device that has never opened the picker.
    window.localStorage.removeItem('snowdesk.map.basemap');
    installCachesStub({});

    const readings = await audit.collect(document.createElement('div'));

    expect(readings.selectedBasemap).toBeNull();
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

  it('reads what each overlay holds, not just that a row exists', async () => {
    // SNOW-914: the key used to be the answer, and it is not one. A row
    // with an empty FeatureCollection draws nothing, and a row stamped for
    // another account is refused by the reader — both read as Yes.
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await window.pwaDb.put('data:map_overlays', {
      key: 'weather',
      geojson: { type: 'FeatureCollection', features: [{}, {}] },
      cached_at: '2026-09-11T08:00:00Z',
    });
    await window.pwaDb.put('data:map_overlays', {
      key: 'routes',
      geojson: { type: 'FeatureCollection', features: [] },
      principal: 'acct-1',
      cached_at: '2026-09-11T08:00:00Z',
    });

    const readings = await audit.collect();

    expect(readings.overlays.weather.features).toBe(2);
    expect(readings.overlays.routes.features).toBe(0);
    expect(readings.overlays.routes.principal).toBe('acct-1');
  });

  it('reads a payload with no feature array as unreadable, not as empty', async () => {
    // Null rather than 0: "there is nothing in it" and "this is not a
    // FeatureCollection" are different answers, and only the first is
    // something to tell the user about their own content.
    installCachesStub({ 'snowdesk-shell-abc': [] });
    await window.pwaDb.put('data:map_overlays', {
      key: 'weather',
      geojson: {},
      cached_at: '2026-09-11T08:00:00Z',
    });

    const readings = await audit.collect();

    expect(readings.overlays.weather.features).toBeNull();
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
    // A readings set with no blocking failure, so the ordinary tally line
    // is the one used — the blocked form is asserted below.
    const report = window.pwaOfflineAuditCore.buildReport(
      {
        dbAvailable: true,
        serviceWorker: { supported: true, registered: true, controlled: true },
        shellEntries: [
          { url: 'https://x/', isPage: true, principal: 'anonymous' },
          { url: 'https://x/a.js', isPage: false },
          { url: 'https://x/a.css', isPage: false },
        ],
        // SNOW-912: the page's own modules, both held here — without them
        // the app-opens row blocks and the tally takes its blocked form.
        mapDependencies: ['https://x/a.js', 'https://x/a.css'],
      },
      t,
    );

    audit.render(target, report, t);

    expect(target.querySelector('[data-audit-verdict]').textContent).toBe(
      report.verdict.text,
    );
    expect(target.querySelector('[data-audit-counts]').textContent).toBe(
      `${report.counts.yes} of ${report.counts.total} available offline`,
    );
  });

  it('refuses to claim availability under a blocking failure', async () => {
    // "11 of 12 available offline" under "this device is not set up for
    // offline use" is the tally contradicting the verdict directly above
    // it: every one of those eleven is conditional on the thing that
    // just failed.
    const target = document.createElement('div');
    const t = {
      'counts-line': '%(yes)s of %(total)s available offline',
      'counts-line-blocked': '%(yes)s of %(total)s saved, none of it reachable yet',
    };
    const report = window.pwaOfflineAuditCore.buildReport(
      { dbAvailable: true, serviceWorker: { supported: true, registered: false } },
      t,
    );

    audit.render(target, report, t);

    expect(report.verdict.status).toBe('fail');
    expect(target.querySelector('[data-audit-counts]').textContent).toContain(
      'none of it reachable yet',
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

describe('the Save control (SNOW-912)', () => {
  // The panel's one action, and for the whole of SNOW-907 it could not be
  // reached: the gate looked up a check id (`map-page`) and a status
  // (`ok`) that the core has never produced, so `pageCheck` was null on
  // every device and the button stayed hidden — including on the device
  // whose verdict was telling its owner, in red, to go and open the map.
  const SHELL = 'snowdesk-shell-abc';
  // Seeded at the document's own origin, which is what a shell cache holds:
  // the page's relative hrefs resolve against it, and a cross-origin
  // fixture made "the app opens" read `scripts` — so this block once
  // asserted the button's state in a case it was not aiming at.
  const ORIGIN = window.location.origin;
  const SCRIPT = { url: `${ORIGIN}/static/js/map.abc.js`, headers: {} };
  const STYLE = { url: `${ORIGIN}/static/css/output.abc.css`, headers: {} };
  // A map page that boots from exactly the two entries above, so a device
  // holding both is a device whose page opens.
  const MAP_HTML =
    '<link rel="stylesheet" href="/static/css/output.abc.css">' +
    '<script src="/static/js/map.abc.js"></script>';
  const mapPage = (principal) => ({
    url: `${ORIGIN}/`,
    headers: { 'X-SW-Principal': principal },
    body: MAP_HTML,
  });

  /**
   * A controlling worker that answers the version probe at once.
   *
   * Without the reply `liveShellCacheName` waits out its 1.5s timeout
   * before falling back, which is a real 1.5s in a test.
   */
  function installController(version) {
    const listeners = new Set();
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        controller: {
          postMessage: (message) => {
            if (message !== 'version') return;
            listeners.forEach((fn) => fn({ data: { type: 'version', version } }));
          },
        },
        addEventListener: (type, fn) => {
          if (type === 'message') listeners.add(fn);
        },
        removeEventListener: (_type, fn) => listeners.delete(fn),
        getRegistration: async () => ({ waiting: null }),
      },
    });
  }

  beforeEach(() => {
    // The reveal is a fixed 70ms per row over a report that is complete
    // before it starts, so the tests take the reduced-motion path and
    // read the finished thing.
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({ matches: true }),
    });
  });

  afterEach(() => {
    delete navigator.serviceWorker;
    document.body.innerHTML = '';
  });

  /** The panel's markup contract, bound and run. */
  async function runPanel() {
    document.body.innerHTML = `
      <div data-offline-audit>
        <button data-offline-audit-run></button>
        <div data-offline-audit-output hidden></div>
        <button data-offline-audit-copy hidden></button>
        <button data-offline-audit-save hidden></button>
        <p data-offline-audit-status></p>
      </div>
    `;
    audit.init();
    document.querySelector('[data-offline-audit-run]').click();
    // The reveal is a fixed cadence per row; let it finish before asking
    // what the panel decided.
    await vi.waitUntil(
      () => document.querySelector('[data-offline-audit-output] [data-audit-summary]'),
      { timeout: 5000 },
    );
    return document.querySelector('[data-offline-audit-save]');
  }

  it('is offered when the map page is missing and there is a connection to fetch it on', async () => {
    installController(SHELL);
    installCachesStub({ [SHELL]: [SCRIPT, STYLE] });

    const save = await runPanel();

    expect(save.hidden).toBe(false);
  });

  it('is offered when the saved copy belongs to another account', async () => {
    // Re-fetching restamps it for whoever is signed in now, so the button
    // is the remedy here too.
    installController(SHELL);
    installCachesStub({
      [SHELL]: [mapPage('acct-someone-else'), SCRIPT, STYLE],
    });

    const save = await runPanel();

    expect(save.hidden).toBe(false);
  });

  it('stays hidden once the map page is saved for this account', async () => {
    installController(SHELL);
    installCachesStub({
      [SHELL]: [mapPage('anonymous'), SCRIPT, STYLE],
    });

    const save = await runPanel();

    expect(save.hidden).toBe(true);
  });

  it('is offered when the page is saved but its scripts are not', async () => {
    // The state the repair was built for. `_warmCache(['/'])` re-fetches
    // the page and `_warmShellSubresources` then fetches the modules it
    // names and the cache is missing — so hiding the control here left the
    // one failure warming can definitely fix with no way to reach it.
    installController(SHELL);
    installCachesStub({ [SHELL]: [mapPage('anonymous'), STYLE] });

    const save = await runPanel();

    expect(save.hidden).toBe(false);
  });

  it('stays hidden with no worker to warm through', async () => {
    // Deleted rather than set to undefined: a browser without service
    // workers has no such property at all, and `'serviceWorker' in
    // navigator` is what the collector asks.
    delete navigator.serviceWorker;
    installCachesStub({ [SHELL]: [SCRIPT, STYLE] });

    const save = await runPanel();

    expect(save.hidden).toBe(true);
  });
});

describe('a device whose storage stops answering', () => {
  /*
   * The bug this block exists for: the panel was reported reading
   * "Checking…" for ever, on an iPad, in the same screenshot as the reset
   * panel stuck on "Loading…" — two surfaces, one unbounded `await` each.
   *
   * Storage in trouble HANGS; it does not reject. Every `try`/`catch` in
   * the collector is written against a rejection, so none of them runs.
   * These tests drive the failure the device actually has — a promise
   * that never settles — which no existing test did, because every stub
   * in this file resolves.
   *
   * Fake timers throughout: the budgets are seconds, and a suite that
   * waits them out in real time is a suite nobody runs.
   */

  /** A Cache Storage whose every call hangs for ever. */
  function installHangingCaches() {
    const never = () => new Promise(() => {});
    const stub = { keys: vi.fn(never), has: vi.fn(never), open: vi.fn(never) };
    Object.defineProperty(window, 'caches', {
      value: stub,
      configurable: true,
      writable: true,
    });
    return stub;
  }

  /** An IndexedDB whose `open` request never fires an event. */
  function installHangingDb() {
    Object.defineProperty(window, 'indexedDB', {
      configurable: true,
      writable: true,
      value: {
        databases: () => new Promise(() => {}),
        open: () => ({ onsuccess: null, onerror: null, onblocked: null }),
      },
    });
  }

  const realIndexedDb = window.indexedDB;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(window, 'indexedDB', {
      configurable: true,
      writable: true,
      value: realIndexedDb,
    });
    document.body.innerHTML = '';
  });

  /** Run `collect()` to completion against a clock we drive ourselves. */
  async function collectUnderFakeClock() {
    const pending = audit.collect();
    // Comfortably past COLLECT_BUDGET_MS. The assertion is that the
    // promise settles at all — the failing version never did, at any
    // point on any clock.
    await vi.advanceTimersByTimeAsync(120000);
    return pending;
  }

  it('finishes even when every cache read hangs', async () => {
    installHangingCaches();

    const readings = await collectUnderFakeClock();

    expect(readings.degraded).not.toBeNull();
    expect(readings.degraded.timedOut).toContain('caches.keys');
  });

  it('reports an unlistable Cache Storage as unreadable, not as empty', async () => {
    // The distinction the whole fix turns on. `[]` would answer "The app
    // opens: No" — telling someone their app will not open when it opens
    // perfectly well, which is the one failure this panel cannot survive.
    installHangingCaches();

    const readings = await collectUnderFakeClock();

    expect(readings.cachesReadable).toBe(false);
    const report = window.pwaOfflineAuditCore.buildReport(readings, {});
    const row = report.sections
      .flatMap((section) => section.checks)
      .filter((check) => check.id === 'app-opens')[0];
    expect(row.status).toBe('unknown');
  });

  it('does not read a page whose stamp did not come back as unstamped', async () => {
    // An entry with no `X-SW-Principal` is one the worker REFUSES to
    // serve, so a timed-out `match` read as an unstamped page tells the
    // user "the saved copy belongs to another account" about a page that
    // is stamped for them and will serve perfectly.
    const never = () => new Promise(() => {});
    Object.defineProperty(window, 'caches', {
      configurable: true,
      writable: true,
      value: {
        keys: async () => ['snowdesk-shell-abc'],
        has: async () => false,
        open: async () => ({
          keys: async () => [{ url: `${window.location.origin}/` }],
          match: never,
        }),
      },
    });

    const readings = await collectUnderFakeClock();

    expect(readings.shellPartial).toBe(true);
    const report = window.pwaOfflineAuditCore.buildReport(readings, {});
    const row = report.sections
      .flatMap((section) => section.checks)
      .filter((check) => check.id === 'app-opens')[0];
    expect(row.status).toBe('unknown');
  });

  it('finishes even when the database never opens', async () => {
    installCachesStub({});
    installHangingDb();

    const readings = await collectUnderFakeClock();

    expect(readings.dbAvailable).toBe(false);
    expect(readings.degraded.timedOut).toContain('indexeddb.open');
  });

  it('stops asking once storage has proved it is not answering', async () => {
    // The latch, and the reason it is not just a per-read timeout: a
    // device with a wedged store and a dozen downloads would otherwise pay
    // the budget once per read and take the whole collection budget to
    // say what it knew after three.
    installHangingCaches();
    installHangingDb();

    const readings = await collectUnderFakeClock();

    expect(readings.degraded.latched).toBe(true);
  });

  it('never leaves the panel saying "Checking…"', async () => {
    // The symptom exactly as reported: skeleton rows, a status line
    // reading "Checking…", and no way forward.
    installHangingCaches();
    installHangingDb();
    document.body.innerHTML = `
      <div data-offline-audit>
        <button data-offline-audit-run></button>
        <div data-offline-audit-output hidden></div>
        <button data-offline-audit-copy hidden></button>
        <p data-offline-audit-status></p>
      </div>
    `;
    audit.init();

    document.querySelector('[data-offline-audit-run]').click();
    await vi.advanceTimersByTimeAsync(120000);

    const status = document.querySelector('[data-offline-audit-status]');
    expect(status.textContent).not.toBe('Checking…');
    // And a report, not an empty box: the rows it could not take are
    // dashes, which is a finding rather than a blank.
    expect(document.querySelector('[data-audit-summary]')).not.toBeNull();
  });
});

describe('a run that throws outright', () => {
  /*
   * `run()` is called unawaited from a click handler, so a throw anywhere
   * in the collection became an unhandled rejection — invisible on a
   * phone — and left the skeleton and "Checking…" on screen for ever.
   * A report that cannot be taken is itself a finding, and gets painted.
   */
  afterEach(() => {
    vi.restoreAllMocks();
    delete navigator.onLine;
    document.body.innerHTML = '';
  });

  async function runPanelWithBrokenCollect() {
    installCachesStub({});
    // A throw from outside any bounded read — `navigator.onLine` is read
    // when the readings are assembled, after the last of them. That is
    // what makes this the complement of the block above: `bounded` covers
    // the hang, this covers everything else that can go wrong in a
    // collector nobody is awaiting.
    Object.defineProperty(navigator, 'onLine', {
      configurable: true,
      get() {
        throw new TypeError('boom');
      },
    });
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: () => ({ matches: true }),
    });
    document.body.innerHTML = `
      <div data-offline-audit>
        <button data-offline-audit-run></button>
        <div data-offline-audit-output hidden></div>
        <button data-offline-audit-copy hidden></button>
        <p data-offline-audit-status></p>
      </div>
    `;
    audit.init();
    document.querySelector('[data-offline-audit-run]').click();
    await vi.waitUntil(() => document.querySelector('[data-audit-summary]'), {
      timeout: 5000,
    });
  }

  it('paints a report that says it could not run, and offers Copy', async () => {
    await runPanelWithBrokenCollect();

    const status = document.querySelector('[data-offline-audit-status]');
    expect(status.textContent).not.toBe('Checking…');
    // Copy is the only route this evidence has off a phone with no
    // devtools, so the run that went wrong is the one that must offer it.
    expect(document.querySelector('[data-offline-audit-copy]').hidden).toBe(false);
  });
});
