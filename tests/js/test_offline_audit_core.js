/*
 * tests/js/test_offline_audit_core.js — Vitest unit tests for
 * static/js/offline_audit_core.js (SNOW-907).
 *
 * The report exists because a user was told, by every surface that had an
 * opinion, that their device was ready for a journey it was not. So the
 * cases here are almost entirely the ones where a naive reading says yes:
 * a map page cached under another account, an area whose tiles are all
 * present and whose TileJSON is not, a record with no bucket, and every
 * reading the browser can simply refuse to take.
 *
 * Two properties worth stating up front, because most of the file is
 * checking them:
 *
 *   - the row list is FIXED and answers are Yes/No, so a device with
 *     nothing stored produces the same rows as a device with everything;
 *   - an absence is never upgraded into a Yes. `unknown` is a state, and
 *     it counts towards neither figure in the tally.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

/** Readings with every capability in its healthiest state. */
function healthy(overrides) {
  return Object.assign(
    {
      now: '2026-09-11T08:00:00.000Z',
      online: true,
      serviceWorker: { supported: true, registered: true, controlled: true },
      storage: { usage: 50 * 1024 * 1024, quota: 500 * 1024 * 1024, persisted: true },
      shellEntries: [
        { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
        {
          url: 'https://snowdesk.info/ch-4115/verbier/2026-02-16/',
          isPage: true,
          principal: 'anonymous',
        },
        { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
        { url: 'https://snowdesk.info/static/css/output.abc.css', isPage: false },
        // SNOW-914: the URLs the map's COLD OPEN asks for, which is what
        // the rows are answered against — any other day's ratings, or any
        // other country's outlines, are a cache miss and a blank map.
        {
          url: 'https://snowdesk.info/api/ratings/?d=2026-09-11&country=ch',
          isPage: false,
        },
        {
          url: 'https://snowdesk.info/api/regions.geojson?country=ch',
          isPage: false,
        },
      ],
      currentPrincipal: null,
      mapPath: '/',
      // SNOW-912: what the cached map page boots from. A healthy device
      // holds every one of them; the row is answered against this list,
      // not against "is any script cached".
      mapDependencies: [
        'https://snowdesk.info/static/js/map.abc.js',
        'https://snowdesk.info/static/css/output.abc.css',
      ],
      // SNOW-914: the day the cached page will boot on, off its own
      // ``data-today``. The ratings entry above is that day's.
      mapDay: '2026-09-11',
      areas: [
        {
          id: 'base-openfreemap',
          kind: 'base',
          basemapKey: 'openfreemap',
          name: 'overview',
          deps: [],
          bucketPresent: true,
          entries: ['https://t/4/1/1.pbf'],
        },
        {
          id: 'region-ch-4115',
          kind: 'region',
          name: 'Martigny',
          basemapKey: 'openfreemap',
          bytes: 12 * 1024 * 1024,
          deps: ['https://t/style.json', 'https://t/source.json'],
          bucketPresent: true,
          entries: [
            'https://t/style.json',
            'https://t/source.json',
            'https://t/12/1/1.pbf',
          ],
        },
      ],
      stores: { 'data:favourites': 3 },
      // SNOW-914: rows, not keys. `favourites` and `routes` are
      // account-scoped, so their stamp has to match `currentPrincipal`
      // (null here) or the reader refuses them; an empty FeatureCollection
      // is readable and still draws nothing.
      overlays: {
        favourites: { features: 2, principal: null },
        community_reports: { features: 3 },
        weather: { features: 4 },
        routes: { features: 1, principal: null },
      },
      panelKeys: ['observations'],
      mutations: { count: 0 },
      dbAvailable: true,
    },
    overrides || {},
  );
}

/** One row from a built report, by id. */
function row(report, id) {
  let found = null;
  report.sections.forEach((section) => {
    section.checks.forEach((check) => {
      if (check.id === id) found = check;
    });
  });
  return found;
}

/** Every row id in paint order. */
function ids(report) {
  return report.sections.flatMap((section) => section.checks.map((check) => check.id));
}

beforeAll(async () => {
  await import('../../static/js/offline_audit_core.js');
  core = window.pwaOfflineAuditCore;
});

describe('the row list', () => {
  it('is the same fixed set whether the device holds everything or nothing', () => {
    // The property the whole readout rests on: a table that changes shape
    // with what happens to be stored cannot be scanned, cannot be compared
    // between runs, and cannot be painted before anything is read.
    const full = ids(core.buildReport(healthy({ areas: [] }), {}));
    const empty = ids(core.buildReport({}, {}));
    expect(empty).toEqual(full);
    core.ROW_IDS.forEach((id) => expect(empty).toContain(id));
  });

  it('answers every row Yes, No or a dash — never a count', () => {
    const report = core.buildReport(healthy(), {
      'answer-yes': 'Yes',
      'answer-no': 'No',
      'answer-unknown': '—',
    });
    report.sections.forEach((section) => {
      section.checks.forEach((check) => {
        expect(['Yes', 'No', '—']).toContain(check.value);
      });
    });
  });

  it('paints a waiting skeleton with the same rows and no answers', () => {
    const pending = core.pendingReport({ 'answer-pending': '…' });
    expect(pending.pending).toBe(true);
    expect(ids(pending)).toEqual(core.ROW_IDS.slice());
    pending.sections.forEach((section) => {
      section.checks.forEach((check) => {
        expect(check.status).toBe('pending');
        expect(check.value).toBe('…');
      });
    });
  });
});

describe('the downloads', () => {
  it('gets one named row per download, all under one heading', () => {
    // A single row saying the map draws is no use to someone whose
    // Verbier download is the broken one — but three headings for three
    // kinds put an empty-looking section between every pair of rows, so
    // the kind rides along in the label instead.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'Martigny',
            deps: [],
            bucketPresent: true,
            entries: ['a'],
          },
          {
            id: 'c1',
            kind: 'custom',
            type: 'dropzone',
            name: 'La Chaux',
            deps: [],
            bucketPresent: true,
            entries: ['a'],
          },
          {
            id: 'c2',
            kind: 'custom',
            name: 'Area 2',
            deps: [],
            bucketPresent: true,
            entries: ['a'],
          },
        ],
      }),
      {
        'label-dropzone': '%(name)s (drop zone)',
        'label-custom': '%(name)s (area you drew)',
      },
    );
    const downloads = report.sections.filter((section) => section.id === 'downloads')[0];
    // Regions first: a region is what most people download, and the ones
    // they drew themselves are the exceptions.
    expect(downloads.checks.map((check) => check.label)).toEqual([
      'Martigny',
      'La Chaux (drop zone)',
      'Area 2 (area you drew)',
    ]);
  });

  it('names a region by name alone, because a place reads as one', () => {
    const report = core.buildReport(healthy(), {});
    expect(row(report, 'area:region-ch-4115').label).toBe('Martigny');
  });

  it('says so on one row when there is nothing downloaded at all', () => {
    const report = core.buildReport(healthy({ areas: [] }), {});
    expect(row(report, 'no-downloads').status).toBe('no');
  });

  it('answers No for an area whose tiles are there and whose TileJSON is not', () => {
    // SNOW-843's whole bug class: without it MapLibre cannot learn a
    // single tile URL, so a perfect pinned tile set is unreachable and
    // the map draws nothing.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'Martigny',
            deps: ['https://t/style.json', 'https://t/source.json'],
            bucketPresent: true,
            entries: ['https://t/style.json', 'https://t/1/1/1.pbf'],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'area:r1').status).toBe('no');
    expect(row(report, 'area:r1').reason).toBe('incomplete');
  });

  it('answers No for a record whose bucket is gone', () => {
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            deps: [],
            bucketPresent: false,
            entries: [],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'area:r1').reason).toBe('missing');
  });

  it('leaves a pre-SNOW-844 record unknown rather than claiming either way', () => {
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            deps: [],
            bucketPresent: true,
            entries: ['a'],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'area:r1').status).toBe('unknown');
  });

  it('keeps the shared overview out of the downloads list entirely', () => {
    // Stored, real, and not a place the user chose: it is the zoomed-out
    // tiles the app fetched for itself, so it is answered as half of the
    // basemap's own row rather than as a download of its own.
    const report = core.buildReport(healthy(), {});
    expect(row(report, 'area:base-openfreemap')).toBeNull();
  });
});

describe('the basemap rows', () => {
  it('rolls the downloads up into one row per style, in THE MAP', () => {
    // The question the per-area rows cannot reach: WHICH map style will I
    // actually see. A device could hold a complete Swisstopo download and
    // be sitting on OpenFreeMap, and nothing said so.
    const report = core.buildReport(healthy(), {
      'basemap-openfreemap': 'OpenFreeMap',
      'row-basemap': '%(name)s basemap',
    });
    const map = report.sections.filter((section) => section.id === 'map')[0];
    expect(map.checks.map((check) => check.id)).toContain('basemap:openfreemap');
    expect(row(report, 'basemap:openfreemap').label).toBe('OpenFreeMap basemap');
    expect(row(report, 'basemap:openfreemap').status).toBe('yes');
  });

  it('answers No when the style itself is not saved, tiles or not', () => {
    // SNOW-843: without the style document and each source's TileJSON,
    // MapLibre cannot learn a single tile URL, so a perfect pinned tile
    // set renders nothing.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'base-x',
            kind: 'base',
            basemapKey: 'x',
            deps: [],
            bucketPresent: true,
            entries: ['https://t/4/1/1.pbf'],
          },
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            basemapKey: 'x',
            deps: ['https://t/style.json'],
            bucketPresent: true,
            entries: ['https://t/12/1/1.pbf'],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'basemap:x').reason).toBe('style');
  });

  it('answers Yes, with a caveat, when the style is saved but the base layer is not', () => {
    // A deliberate reversal of SNOW-856's answer, not a regression of it.
    //
    // The row used to read No here, and it was reported from staging over
    // a device drawing Martigny, Sion and Gstaad on screen at that moment:
    // a complete Martigny-Verbier download, no pinned base layer, and a
    // panel saying "Swisstopo (CH) basemap: No". A reader shown that
    // concludes the app has no map.
    //
    // The base layer still matters and is still said — as a note on a
    // Yes. What it cannot be is a row: every label for it either reaches
    // for zoom jargon or claims something ("the complete map") no device
    // ever has, since a basemap is never downloaded in full, and a row
    // whose answer can only be No is not a question worth asking.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            basemapKey: 'x',
            deps: ['https://t/style.json'],
            bucketPresent: true,
            entries: ['https://t/style.json', 'https://t/12/1/1.pbf'],
          },
        ],
      }),
      {
        'note-basemap-downloads-only': 'outside your downloads %(name)s is not saved',
        'notes-sentence': 'Also worth knowing: %(notes)s.',
      },
    );

    const basemap = row(report, 'basemap:x');
    expect(basemap.status).toBe('yes');
    expect(basemap.reason).toBe('downloads-only');
    // The caveat still reaches the reader, in the one place a row has no
    // room for: the summary.
    expect(report.summary).toContain('outside your downloads');
  });

  it('still answers No when the style itself is missing', () => {
    // The guard on the reversal above: the map genuinely does not draw
    // without its style document, and that No must survive.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'base-x',
            kind: 'base',
            name: 'overview',
            basemapKey: 'x',
            deps: [],
            bucketPresent: true,
            entries: ['https://t/4/1/1.pbf'],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'basemap:x').status).toBe('no');
    expect(row(report, 'basemap:x').reason).toBe('style');
  });

  it('does not list a basemap the device holds nothing for and is not using', () => {
    // The other half of the staging report. A base-layer record whose
    // bucket is empty — a warm started and never finished — earned a row
    // reading "OpenFreeMap basemap: No" beside the reader's own style,
    // about a map they were not looking at and had nothing stored for.
    // Two Nos under THE MAP is how the panel came to suggest there was no
    // basemap at all.
    const report = core.buildReport(
      healthy({
        selectedBasemap: 'x',
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            basemapKey: 'x',
            deps: ['https://t/style.json'],
            bucketPresent: true,
            entries: ['https://t/style.json', 'https://t/12/1/1.pbf'],
          },
          {
            id: 'base-ghost',
            kind: 'base',
            name: 'overview',
            basemapKey: 'ghost',
            deps: [],
            bucketPresent: false,
            bucketReadable: true,
            entries: [],
          },
        ],
      }),
      {},
    );

    expect(row(report, 'basemap:ghost')).toBeNull();
    expect(row(report, 'basemap:x')).not.toBeNull();
  });

  it('still lists the style on screen even when nothing is stored for it', () => {
    // The reverse guard: a reader looking at a style their device holds
    // nothing for is exactly who needs to be told (SNOW-913).
    const report = core.buildReport(
      healthy({ selectedBasemap: 'nothing-here', areas: [] }),
      {},
    );
    expect(row(report, 'basemap:nothing-here')).not.toBeNull();
  });

  it('answers unknown, not No, when the bucket did not come back', () => {
    const report = core.buildReport(
      healthy({
        selectedBasemap: 'x',
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            basemapKey: 'x',
            deps: ['https://t/style.json'],
            bucketPresent: false,
            bucketReadable: false,
            entries: [],
          },
        ],
      }),
      {},
    );
    expect(row(report, 'basemap:x').status).toBe('unknown');
  });
});

describe('the verdict', () => {
  it('passes a device that holds everything', () => {
    const report = core.buildReport(healthy(), {});
    expect(report.verdict.status).toBe('ok');
  });

  it('refuses the all-clear when a row could not be read at all', () => {
    // An unknown is not a Yes. A device whose IndexedDB would not open
    // has not been checked, whatever the rest of the table says.
    const report = core.buildReport(healthy({ dbAvailable: false }), {});
    expect(report.verdict.status).toBe('warn');
    expect(report.verdict.text).toBe('verdict-unchecked');
  });

  it('refuses the all-clear when anything at all answers No', () => {
    // "Everything you need is here" over a table with six Nos in it is
    // the exact species of reassurance this whole feature exists to stop
    // being given.
    const report = core.buildReport(
      healthy({
        overlays: {
          favourites: { features: 2, principal: null },
          community_reports: { features: 3 },
          routes: { features: 1, principal: null },
        },
      }),
      {},
    );
    expect(row(report, 'weather').status).toBe('no');
    expect(report.verdict.status).toBe('warn');
    expect(report.verdict.text).toBe('verdict-partial');
  });

  it('fails when the map page was never cached', () => {
    const report = core.buildReport(healthy({ shellEntries: [] }), {});
    expect(report.verdict.status).toBe('fail');
    expect(row(report, 'app-opens').reason).toBe('absent');
    expect(row(report, 'app-opens').status).toBe('blocked');
  });

  it('fails DIFFERENTLY when the cached page belongs to another account', () => {
    // The reported journey. Every other surface says the region is
    // downloaded and it is; the page the region would be drawn on is in
    // the cache and the worker will refuse to serve it, silently.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'acct-aaa' },
        ],
        currentPrincipal: 'acct-bbb',
      }),
      {},
    );
    expect(row(report, 'app-opens').reason).toBe('principal');
    expect(report.verdict.text).toBe('verdict-other-account');
  });

  it('fails when there is no worker, ahead of everything below it', () => {
    const report = core.buildReport(
      healthy({
        serviceWorker: { supported: true, registered: false, controlled: false },
        shellEntries: [],
      }),
      {},
    );
    expect(report.verdict.text).toBe('verdict-no-worker');
  });

  it('warns rather than fails when the app opens but nothing is downloaded', () => {
    const report = core.buildReport(healthy({ areas: [] }), {});
    expect(report.verdict.status).toBe('warn');
  });

  it('warns when every download is broken, which is not the same as none', () => {
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'r1',
            kind: 'region',
            name: 'M',
            deps: ['https://t/a.json'],
            bucketPresent: true,
            entries: ['https://t/1.pbf'],
          },
        ],
      }),
      {},
    );
    expect(report.verdict.text).toBe('verdict-downloads-broken');
  });
});

describe('the rows that answer about what the user will see', () => {
  // SNOW-914/915. Each of these read Yes on a device that would have shown
  // the user nothing — the panel disagreeing with the screen, which is the
  // only way it loses its value.

  it('refuses ratings cached for a day other than the one the page opens on', () => {
    // The journey this app is for: open it at home on Tuesday, open it on
    // the mountain on Wednesday. The boot fetch asks for the cached page's
    // own `data-today` and `_staleWhileRevalidate` matches exact URLs, so
    // last week's feed paints nothing — under a green row.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
          { url: 'https://snowdesk.info/static/css/output.abc.css', isPage: false },
          {
            url: 'https://snowdesk.info/api/ratings/?d=2026-09-02&country=ch',
            isPage: false,
          },
        ],
      }),
      {},
    );

    expect(row(report, 'danger-ratings').status).toBe('no');
  });

  it('accepts the feed for the day the cached page will open on', () => {
    expect(row(core.buildReport(healthy(), {}), 'danger-ratings').status).toBe('yes');
  });

  it('answers unknown for ratings when the page names no day', () => {
    const report = core.buildReport(healthy({ mapDay: null }), {});

    expect(row(report, 'danger-ratings').status).toBe('unknown');
  });

  it('refuses region outlines cached for another country', () => {
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
          { url: 'https://snowdesk.info/static/css/output.abc.css', isPage: false },
          {
            url: 'https://snowdesk.info/api/regions.geojson?country=fr',
            isPage: false,
          },
        ],
      }),
      {},
    );

    expect(row(report, 'region-shapes').status).toBe('no');
  });

  it('does not count the Help page as a bulletin', () => {
    // SNOW-915: every public page is cached by the visit that renders it,
    // and the row counted all of them. Reading Help once told the user
    // their bulletins were saved.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/help/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/privacy/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/trips/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
        ],
      }),
      {},
    );

    expect(row(report, 'bulletins').status).toBe('no');
  });

  it('counts a bulletin in each of its three URL forms', () => {
    // /<region_id>/, /<region_id>/<slug>/ and /<region_id>/<slug>/<date>/,
    // all served by bulletin_detail.
    for (const path of ['/ch-4115/', '/ch-4115/verbier/', '/ch-4115/verbier/2026-02-16/']) {
      const report = core.buildReport(
        healthy({
          shellEntries: [
            { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
            { url: `https://snowdesk.info${path}`, isPage: true, principal: 'anonymous' },
            { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
          ],
        }),
        {},
      );

      expect(row(report, 'bulletins').status, path).toBe('yes');
    }
  });

  it('refuses an overlay stamped for another account', () => {
    // `getOverlay` returns null for it, so the row IS on the device and
    // invisible — the map draws nothing and the panel said Yes.
    const report = core.buildReport(
      healthy({
        currentPrincipal: 'acct-1',
        overlays: { routes: { features: 4, principal: 'acct-2' } },
      }),
      {},
    );

    expect(row(report, 'routes').status).toBe('no');
    expect(row(report, 'routes').reason).toBe('principal');
  });

  it('answers unknown for an overlay that is readable and empty', () => {
    // Nothing stored because there is nothing to store. Not Yes (nothing
    // will appear) and not No (nothing is broken).
    const report = core.buildReport(
      healthy({ overlays: { weather: { features: 0 } } }),
      {},
    );

    expect(row(report, 'weather').status).toBe('unknown');
    expect(row(report, 'weather').reason).toBe('empty');
  });

  it('counts an empty overlay towards neither side of the tally', () => {
    const report = core.buildReport(
      healthy({ overlays: { weather: { features: 0 } } }),
      {},
    );

    const weather = row(report, 'weather');
    expect(weather.status).not.toBe('yes');
    expect(weather.status).not.toBe('no');
  });

  it('asks the app-opens row for scripts and the looks-right row for styles', () => {
    // Two rows, two consequences: an app that opens unstyled is ugly and
    // usable, where one that does not open is neither. Folding them
    // together would block the verdict over a missing stylesheet.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
        ],
      }),
      {},
    );

    expect(row(report, 'app-opens').status).toBe('yes');
    expect(row(report, 'app-looks-right').status).toBe('no');
  });

  it('does not call the app styled because some other page’s CSS is cached', () => {
    // `fileCounts(r).style > 0` was "is any CSS cached", which the settings
    // page's own stylesheet makes true on the very device reading this.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/map.abc.js', isPage: false },
          { url: 'https://snowdesk.info/static/css/settings.abc.css', isPage: false },
        ],
      }),
      {},
    );

    expect(row(report, 'app-looks-right').status).toBe('no');
  });
});

describe('resolving which basemap is on screen (SNOW-913)', () => {
  // The map resolves a stored preference against the catalogue it actually
  // renders and falls back to the deployed default. The report has to do
  // the same, or it labels a style "on screen" that the map will not show —
  // which is the failure this whole ticket is about, one level down.
  const CATALOGUE = {
    keys: ['openfreemap_liberty', 'swisstopo_winter'],
    fallback: 'openfreemap_liberty',
  };

  it('reads the catalogue and the default out of a cached page', () => {
    const html = [
      '<div id="map" data-default-basemap-key="openfreemap_liberty"></div>',
      '<button class="basemap-menu-item" data-basemap-key="openfreemap_liberty"></button>',
      '<button class="basemap-menu-item" data-basemap-key="swisstopo_winter"></button>',
      '<button class="basemap-menu-item" data-basemap-key="swisstopo_winter"></button>',
    ].join('\n');

    expect(core.pageBasemaps(html)).toEqual(CATALOGUE);
  });

  it('keeps a stored choice the catalogue still offers', () => {
    expect(core.resolveBasemap(CATALOGUE, 'swisstopo_winter', null)).toBe(
      'swisstopo_winter',
    );
  });

  it('drops a stored choice the catalogue no longer offers', () => {
    // A style removed from the picker leaves its preference behind in
    // localStorage, and map.js quietly falls back to the default — so the
    // stale key is precisely NOT what the reader is looking at.
    expect(core.resolveBasemap(CATALOGUE, 'retired_style', null)).toBe(
      'openfreemap_liberty',
    );
  });

  it('falls back to the host page’s default when the catalogue has no own', () => {
    expect(core.resolveBasemap({ keys: ['a'], fallback: null }, 'gone', 'ign_plan')).toBe(
      'ign_plan',
    );
  });

  it('trusts the stored choice when there is no catalogue to check it against', () => {
    // No cached page means no catalogue — and the page being absent is
    // already the blocking row above.
    expect(core.resolveBasemap(null, 'swisstopo_winter', 'ign_plan')).toBe(
      'swisstopo_winter',
    );
  });

  it('names nothing when nothing is stored, offered or defaulted', () => {
    expect(core.resolveBasemap(null, null, null)).toBeNull();
  });
});

describe('the basemap rows (SNOW-913)', () => {
  // The report has to answer about the style the reader is LOOKING AT. It
  // used to roll up only what the device had stored, so someone who had
  // switched to Swisstopo was told about OpenFreeMap — a row naming a
  // basemap they were not using, and none for the one they were.
  const COPY = {
    'row-basemap': '%(name)s basemap',
    'row-basemap-current': '%(name)s basemap (on screen)',
    'basemap-swisstopo_winter': 'Swisstopo (CH)',
    'basemap-openfreemap': 'OpenFreeMap',
  };

  /** The basemap rows, in the order the report paints them. */
  function basemapRows(report) {
    const rows = [];
    report.sections.forEach((section) => {
      section.checks.forEach((check) => {
        if (check.id.indexOf('basemap:') === 0) rows.push(check);
      });
    });
    return rows;
  }

  it('names the basemap on screen even with nothing stored for it', () => {
    // The field report exactly: switched to Swisstopo, whose wide-band warm
    // never completed, so no record for it exists anywhere on the device.
    const report = core.buildReport(
      healthy({ selectedBasemap: 'swisstopo_winter' }),
      COPY,
    );

    const rows = basemapRows(report);
    expect(rows[0].label).toBe('Swisstopo (CH) basemap (on screen)');
    expect(rows[0].status).toBe('no');
  });

  it('puts it first, ahead of a basemap the device merely holds bytes for', () => {
    const report = core.buildReport(
      healthy({ selectedBasemap: 'swisstopo_winter' }),
      COPY,
    );

    expect(basemapRows(report).map((check) => check.id)).toEqual([
      'basemap:swisstopo_winter',
      'basemap:openfreemap',
    ]);
  });

  it('leaves the stored-but-unselected one plainly labelled', () => {
    // Both rows saying "X basemap" would give the reader no way to tell
    // which one is theirs, which is the whole failure being fixed.
    const report = core.buildReport(
      healthy({ selectedBasemap: 'swisstopo_winter' }),
      COPY,
    );

    expect(basemapRows(report)[1].label).toBe('OpenFreeMap basemap');
  });

  it('does not duplicate a selected basemap the device also holds', () => {
    const report = core.buildReport(
      healthy({ selectedBasemap: 'openfreemap' }),
      COPY,
    );

    const rows = basemapRows(report);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('OpenFreeMap basemap (on screen)');
  });

  it('claims no current basemap where neither choice nor default is known', () => {
    // static/offline.html: a static file, no server to name the deployed
    // default, and nobody has opened the picker. An omission, not a guess.
    const report = core.buildReport(healthy({ selectedBasemap: null }), COPY);

    const rows = basemapRows(report);
    expect(rows.map((check) => check.id)).toEqual(['basemap:openfreemap']);
    expect(rows[0].label).toBe('OpenFreeMap basemap');
  });
});

describe('the app-opens row', () => {
  it('ignores the query string, as the worker does', () => {
    // map.js writes ?d=YYYY-MM-DD with history.replaceState while the
    // user scrubs; those URLs are never fetched and never cached, and
    // _networkFirstFallback matches with ignoreSearch before giving up.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          {
            url: 'https://snowdesk.info/?d=2026-02-16',
            isPage: true,
            principal: 'anonymous',
          },
          { url: 'https://snowdesk.info/static/js/a.js', isPage: false },
          { url: 'https://snowdesk.info/static/css/a.css', isPage: false },
        ],
        mapDependencies: [
          'https://snowdesk.info/static/js/a.js',
          'https://snowdesk.info/static/css/a.css',
        ],
      }),
      {},
    );
    expect(row(report, 'app-opens').status).toBe('yes');
  });

  it('refuses a page whose own modules are not cached', () => {
    // The false green this row existed to prevent and did not. A device
    // holding the HTML and none of the JavaScript it boots from opens to
    // a blank frame, which to the person holding the phone is a page that
    // did not open — and the row used to pass on a count of ANY cached
    // script, which the two precached audit modules make true everywhere.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/static/js/offline_audit.js', isPage: false },
          { url: 'https://snowdesk.info/static/js/offline_audit_core.js', isPage: false },
        ],
        mapDependencies: ['https://snowdesk.info/static/js/map.abc.js'],
      }),
      {},
    );
    expect(row(report, 'app-opens').status).toBe('blocked');
    expect(row(report, 'app-opens').reason).toBe('scripts');
  });

  it('refuses a page whose body could not be read', () => {
    // Unreadable is not verified. No rather than unknown, because warming
    // overwrites the entry — so the remedy this report offers still
    // applies, and a reader told No and given a working button is better
    // served than one told Yes about a page nobody could check.
    const report = core.buildReport(healthy({ mapDependencies: null }), {});

    expect(row(report, 'app-opens').status).toBe('blocked');
    expect(row(report, 'app-opens').reason).toBe('unreadable');
  });

  it('answers unknown for a page that names nothing on a device holding nothing', () => {
    // `missingFrom`'s rule, applied to a page instead of an area: an empty
    // claim is not a pass. The same three-row resolution the download rows
    // answer to.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
        ],
        mapDependencies: [],
      }),
      {},
    );

    expect(row(report, 'app-opens').status).toBe('unknown');
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
    expect(row(report, 'app-opens').status).toBe('blocked');
  });

  it("treats a null current principal as 'anonymous'", () => {
    expect(core.principalMatches('anonymous', null)).toBe(true);
    expect(core.principalMatches('unknown', 'unknown')).toBe(false);
  });
});

describe('degraded readings', () => {
  it('builds a report from nothing at all rather than throwing', () => {
    const report = core.buildReport(undefined, undefined);
    expect(report.sections.length).toBeGreaterThan(0);
    expect(report.verdict.status).toBe('fail');
  });

  it('reports an unreadable database as unknown, never as No', () => {
    const report = core.buildReport(healthy({ dbAvailable: false }), {});
    ['saved-places', 'routes', 'reports', 'weather'].forEach((id) => {
      expect(row(report, id).status).toBe('unknown');
    });
  });
});

describe('the tally', () => {
  it('counts Yes and No, and leaves unknown out of both', () => {
    // A reading that could not be taken is not a capability this device
    // has or lacks, and counting it either way would be a claim the
    // report cannot support.
    const counts = core.countChecks([
      {
        id: 'a',
        title: 'a',
        checks: [
          { id: '1', label: '', value: '', status: 'yes' },
          { id: '2', label: '', value: '', status: 'no' },
          { id: '3', label: '', value: '', status: 'blocked' },
          { id: '4', label: '', value: '', status: 'unknown' },
        ],
      },
    ]);
    expect(counts).toEqual({ total: 4, yes: 1, no: 2 });
  });
});

describe('the summary paragraph', () => {
  const COPY = {
    'note-no-routes': 'none of your routes has been loaded here',
    'effect-ratings': 'will show no danger ratings',
    'effect-shapes': 'will draw no region outlines',
    'group-open-map-lead': 'Without a signal the app %(effects)s.',
    'group-open-map-remedy': 'Opening the map once while connected fixes %(count)s.',
    'note-not-persisted': 'downloads are not protected from browser cleanup',
    'note-no-weather': 'the weather overlay has not been opened here',
    'notes-sentence': 'Also worth knowing: %(notes)s.',
    'list-pair': '%(first)s and %(last)s',
    'list-join': '%(first)s, %(rest)s',
    'count-one': 'it',
    'count-two': 'both',
    'count-many': 'all of them',
  };

  it('says one shared remedy once, however many capabilities share it', () => {
    // The whole reason the rows lost their explanations. Two Nos with one
    // fix used to print that fix twice and leave the reader to notice it
    // was the same one.
    const report = core.buildReport(
      healthy({
        shellEntries: [
          { url: 'https://snowdesk.info/', isPage: true, principal: 'anonymous' },
          { url: 'https://snowdesk.info/a.js', isPage: false },
          { url: 'https://snowdesk.info/a.css', isPage: false },
        ],
        mapDependencies: ['https://snowdesk.info/a.js', 'https://snowdesk.info/a.css'],
      }),
      COPY,
    );

    expect(report.summary).toContain(
      'Without a signal the app will show no danger ratings and will draw no region outlines.',
    );
    expect(report.summary).toContain('Opening the map once while connected fixes both.');
  });

  it('says nothing at all under a verdict that names a blocking failure', () => {
    // Nothing below a blocked critical row is reachable, so nothing below
    // it is worth advising on. A device with no service worker was being
    // told to open the map once while connected to fix its styling — true
    // in the abstract, useless in the specific.
    const report = core.buildReport(
      healthy({
        serviceWorker: { supported: true, registered: false, controlled: false },
      }),
      COPY,
    );
    expect(report.verdict.status).toBe('fail');
    expect(report.summary).toBe('');
  });

  it('never repeats what the verdict already said', () => {
    // The verdict names the primary failure; saying it again three lines
    // later is how a summary starts reading like an error log.
    const report = core.buildReport(
      healthy({
        overlays: {
          favourites: { features: 2, principal: null },
          community_reports: { features: 3 },
          routes: { features: 1, principal: null },
        },
      }),
      Object.assign({}, COPY, { 'verdict-partial': 'Not everything is here.' }),
    );
    expect(report.verdict.text).toBe('Not everything is here.');
    expect(report.summary).not.toContain('Not everything is here');
    expect(report.summary).toContain('the weather overlay has not been opened here');
  });

  it('is empty when the verdict is the whole truth', () => {
    const report = core.buildReport(healthy(), COPY);
    expect(report.verdict.status).toBe('ok');
    expect(report.summary).toBe('');
  });

  it('caps the loose clauses, so the paragraph never becomes an inventory', () => {
    // Every No contributing a clause produced a six-line run-on — which
    // is what the table already is, and what this paragraph exists not to
    // be. The rest are one line up, spelled out, in the log.
    const report = core.buildReport({ dbAvailable: true, stores: {} }, COPY);
    const clauses = (report.summary.match(/,/g) || []).length;
    expect(clauses).toBeLessThanOrEqual(2);
  });
});

describe('prose helpers', () => {
  const T = { 'list-pair': '%(first)s and %(last)s', 'list-join': '%(first)s, %(rest)s' };

  it.each([
    [[], ''],
    [['a'], 'a'],
    [['a', 'b'], 'a and b'],
    [['a', 'b', 'c'], 'a, b and c'],
  ])('joins %s', (items, expected) => {
    expect(core.joinList(items, T)).toBe(expected);
  });

  it('keeps the space after the comma, which a bare separator could not', () => {
    // `pwaStrings.read` collapses and trims every value it reads back, so
    // a separator of ", " arrives as "," and the list runs together. The
    // space survives here because it is in the middle of a template.
    expect(core.joinList(['a', 'b', 'c'], T)).toContain(', ');
  });

  it('quantifies a shared remedy by how many things it fixes', () => {
    const t = {
      'count-one': 'it',
      'count-two': 'both',
      'count-many': 'all of them',
    };
    expect(core.quantify(1, t)).toBe('it');
    expect(core.quantify(2, t)).toBe('both');
    expect(core.quantify(4, t)).toBe('all of them');
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
  it('leads with the verdict and the tally, then the whole log', () => {
    const report = core.buildReport(healthy(), {});
    const text = core.reportText(report, {
      url: 'https://snowdesk.info/account/settings/',
    });
    expect(text).toContain(report.verdict.text);
    expect(text).toContain('available offline');
    expect(text).toContain('Martigny');
    // Spelled out rather than coloured: a pasted report has no CSS, and
    // this is the only way the data leaves the device.
    expect(text).toContain('[yes]');
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

describe('readings that could not be taken', () => {
  /*
   * The collector is time-bounded (see `bounded` in offline_audit.js),
   * which turns a device whose storage hangs into a device with missing
   * readings. The rule those readings meet here is the report's oldest
   * one: an absence of evidence is `unknown`, never a No.
   *
   * It matters more here than anywhere else in the file. "The app will
   * not open without a signal" is the loudest thing this panel can say,
   * and saying it over an app that opens perfectly well — because a read
   * overran on a busy phone — would cost the report every bit of the
   * credibility it exists to earn.
   */

  it('answers every cache-read row unknown when Cache Storage did not answer', () => {
    const report = core.buildReport(
      healthy({ cachesReadable: false, shellEntries: [], mapDependencies: null }),
      {},
    );

    [
      'app-opens',
      'app-looks-right',
      'danger-ratings',
      'region-shapes',
      'bulletins',
    ].forEach((id) => {
      expect(row(report, id).status).toBe('unknown');
    });
    // And it must not read as an all-clear either: unknowns downgrade the
    // verdict, they do not pass.
    expect(report.verdict.status).not.toBe('ok');
  });

  it('costs only the page rows when the listing was fine and a page was not', () => {
    // The narrower flag. A `match` or a body that did not come back says
    // nothing about the boot feeds, which are answered from the entry
    // URLs alone — blanking those too would turn one unread body into
    // five dashes.
    const report = core.buildReport(healthy({ shellPartial: true }), {});

    expect(row(report, 'app-opens').status).toBe('unknown');
    expect(row(report, 'bulletins').status).toBe('unknown');
    expect(row(report, 'danger-ratings').status).toBe('yes');
    expect(row(report, 'region-shapes').status).toBe('yes');
  });

  it('does not accuse a download of being gone when its bucket did not answer', () => {
    // The worst false negative available: "nothing is stored, download it
    // again" about 200 MB the user chose on purpose and still has.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'region-ch-4115',
            kind: 'region',
            name: 'Martigny',
            basemapKey: 'openfreemap',
            deps: ['https://t/style.json'],
            bucketPresent: false,
            bucketReadable: false,
            entries: [],
          },
        ],
      }),
      {},
    );

    const area = row(report, 'area:region-ch-4115');
    expect(area.status).toBe('unknown');
    expect(area.reason).toBe('unreadable');
  });

  it('still calls a genuinely empty bucket missing', () => {
    // The guard above must not swallow the finding the feature was built
    // for: a bucket that was read, and is empty, is a broken download.
    const report = core.buildReport(
      healthy({
        areas: [
          {
            id: 'region-ch-4115',
            kind: 'region',
            name: 'Martigny',
            basemapKey: 'openfreemap',
            deps: ['https://t/style.json'],
            bucketPresent: false,
            bucketReadable: true,
            entries: [],
          },
        ],
      }),
      {},
    );

    expect(row(report, 'area:region-ch-4115').reason).toBe('missing');
  });

  it('says in the summary why some rows are dashes', () => {
    const report = core.buildReport(
      healthy({
        cachesReadable: false,
        shellEntries: [],
        mapDependencies: null,
        degraded: { timedOut: ['caches.keys'], latched: true },
      }),
      { 'note-storage-slow': 'storage stopped answering' },
    );

    // Leading, not appended: a reader who does not know the report is
    // partial reads a partial report as a complete one.
    expect(report.summary.indexOf('storage stopped answering')).toBe(0);
  });

  it('carries what did not answer into the copied report', () => {
    // The only route this evidence has off a phone with no devtools.
    const report = core.buildReport(
      healthy({
        degraded: { timedOut: ['indexeddb.open', 'caches.keys'], latched: true },
      }),
      {},
    );

    const text = core.reportText(report, {});
    expect(text).toContain('indexeddb.open');
    expect(text).toContain('caches.keys');
  });
});

describe('a collection that threw', () => {
  /*
   * `run()` is called unawaited from a click handler, so a throw was an
   * unhandled rejection nobody on a phone could see, and the panel kept
   * its skeleton and its "Checking…" for ever. It now paints a report
   * about the failure — which means the report must not diagnose a device
   * it never read.
   */

  it('answers nothing at all rather than guessing', () => {
    const report = core.buildReport({ failure: 'TypeError: boom' }, {});

    report.sections.forEach((section) => {
      section.checks.forEach((check) => {
        expect(check.status).toBe('unknown');
      });
    });
  });

  it('does not tell a working device its offline mode is missing', () => {
    // The specific danger: with no readings, `serviceWorker` is absent and
    // the row would otherwise have read "offline mode has not been set up
    // on this device" — a confident, wrong diagnosis of a worker that is
    // running, produced by a check that never ran.
    const report = core.buildReport(
      { failure: 'TypeError: boom' },
      { 'verdict-failed': 'could not run' },
    );

    expect(row(report, 'offline-mode').status).toBe('unknown');
    expect(report.verdict.status).toBe('fail');
    expect(report.verdict.text).toBe('could not run');
  });

  it('names the failure in the copied report', () => {
    const report = core.buildReport({ failure: 'TypeError: boom' }, {});

    expect(core.reportText(report, {})).toContain('TypeError: boom');
  });
});
