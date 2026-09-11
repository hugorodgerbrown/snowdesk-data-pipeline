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
        { url: 'https://snowdesk.info/api/ratings/?c=CH', isPage: false },
        { url: 'https://snowdesk.info/api/regions.geojson', isPage: false },
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
      overlayKeys: ['favourites', 'community_reports', 'weather', 'routes'],
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

  it('answers No when the style is saved but the zoomed-out tiles are not', () => {
    // SNOW-856: without them the map falls off the edge of every
    // downloaded area the moment the camera pulls out past z10, which is
    // why the overview is a real question rather than a detail.
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
      {},
    );
    expect(row(report, 'basemap:x').reason).toBe('reach');
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
      healthy({ overlayKeys: ['favourites', 'community_reports', 'routes'] }),
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
      healthy({ overlayKeys: ['favourites', 'community_reports', 'routes'] }),
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
