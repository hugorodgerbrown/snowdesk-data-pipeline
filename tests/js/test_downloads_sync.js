/*
 * tests/js/test_downloads_sync.js — Vitest unit tests for
 * static/js/downloads_sync.js (SNOW-749).
 *
 * The account half of offline downloads. Three things here are
 * load-bearing rather than incidental:
 *
 *   - **The enqueued shape.** Every endpoint is ``@require_htmx`` and the
 *     mutation queue replays with a plain fetch, so an operation missing
 *     ``HX-Request`` 400s — days later, on a device the developer is not
 *     holding, with nothing on screen to say why. That header is asserted
 *     directly.
 *   - **``accountAreas()`` never rejects and never waits forever.** It is
 *     read on the path that opens the Manage downloads sheet, which must
 *     work with no signal at all. Every unhappy path — 403, refusal,
 *     offline, unparseable body — has to resolve ``[]``, because
 *     ``reconcileAreas`` treats that as "no account rows" and produces
 *     exactly the pre-SNOW-749 list.
 *   - **``adopt()`` is a no-op once everything is synced.** It runs on
 *     every authenticated load; a version that re-pushed each time would
 *     write the whole list to the queue on every page view.
 *   - **Only the signed-in account's own areas are recorded
 *     (SNOW-1142).** The bytes are device-wide and the browser can be
 *     shared, so a held area carries the principal it was downloaded
 *     under; another account's is never pushed, and a legacy or anonymous
 *     one is claimed once — stamped first — by the first account to see
 *     it.
 *
 * The module is a plain IIFE assigning a frozen global, and it reads its
 * configuration off ``#map-custom-download-control`` at CALL time rather
 * than at parse time — which is what lets these tests re-point it between
 * cases without re-importing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SYNC_URL = '/downloads/partials/sync/';
const AREAS_URL = '/downloads/areas.json';
const RENAME_URL = '/downloads/partials/__AREA_ID__/rename/';
const FORGET_URL = '/downloads/partials/__AREA_ID__/forget/';
const CSRF = 'test-csrf-token';
// SNOW-1142: the signed-in account, as `<meta name="pwa-user-id">` says.
const ME = 'u-1';
const OTHER = 'u-2';

/**
 * Render the config carrier and the page's CSRF input.
 *
 * ``#map-custom-download-control`` is the roundel every download surface
 * reads its wiring off; the token comes from the sign-out form the nav
 * renders for a signed-in user, which is the same read
 * ``account_favourites.js`` makes.
 *
 * SNOW-1142: and the ``pwa-user-id`` meta tag ``public/base.html`` renders,
 * naming the signed-in account ('' — no tag content — for nobody).
 *
 * @param {{eligible?: boolean, account?: string}} [options]
 */
function buildFixture(options) {
  const opts = Object.assign({ eligible: true, account: ME }, options || {});
  setAccount(opts.account);
  document.body.innerHTML = `
    <form><input type="hidden" name="csrfmiddlewaretoken" value="${CSRF}"></form>
    <button id="map-custom-download-control" type="button"
            data-downloads-eligible="${opts.eligible}"
            data-signin-url="/accounts/sign-in/"
            data-download-sync-url="${SYNC_URL}"
            data-download-areas-url="${AREAS_URL}"
            data-download-rename-url-template="${RENAME_URL}"
            data-download-forget-url-template="${FORGET_URL}"></button>`;
}

/** Point the page's `pwa-user-id` meta tag at `id` (SNOW-1142). */
function setAccount(id) {
  document.head.querySelectorAll('meta[name="pwa-user-id"]').forEach((el) => el.remove());
  const meta = document.createElement('meta');
  meta.name = 'pwa-user-id';
  meta.content = id || '';
  document.head.appendChild(meta);
}

/** In-memory `meta:app`, so the synced-id marker survives within a test. */
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
  window.pwaDb = {
    rows,
    get: vi.fn(async (_store, key) => (rows.has(key) ? { key, value: rows.get(key) } : undefined)),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
      return row.key;
    }),
  };
  return rows;
}

/** The mutation queue, recording rather than delivering. */
function installQueue() {
  const enqueue = vi.fn(async () => {});
  window.pwaMutationQueue = { enqueue: enqueue };
  return enqueue;
}

/** Parse the body of the nth enqueued operation into a plain object. */
function bodyOf(enqueue, index) {
  const params = new URLSearchParams(enqueue.mock.calls[index || 0][0].body);
  return Object.fromEntries(params.entries());
}

function setOnline(value) {
  Object.defineProperty(window.navigator, 'onLine', { value: value, configurable: true });
}

let sync;

beforeEach(async () => {
  buildFixture();
  installDbStub({});
  installQueue();
  setOnline(true);
  vi.resetModules();
  await import('../../static/js/downloads_sync.js');
  sync = self.pwaDownloadsSync;
});

afterEach(() => {
  vi.unstubAllGlobals();
  setOnline(true);
  delete window.pwaDb;
  delete window.pwaMutationQueue;
  delete window.pwaBasemapDownloads;
  document.head.querySelectorAll('meta[name="pwa-user-id"]').forEach((el) => el.remove());
});

describe('isEnabled', () => {
  it('needs the session and a wired endpoint', () => {
    // Two conditions since SNOW-749's `download_sync` flag was dropped on
    // query cost. The second is not redundant: a page carrying no
    // downloads surface supplies no URL, and must no-op rather than post
    // to undefined.
    expect(sync.isEnabled()).toBe(true);

    buildFixture({ eligible: false });
    expect(sync.isEnabled()).toBe(false);

    document.body.innerHTML = '';
    expect(sync.isEnabled()).toBe(false);
  });
});

describe('push', () => {
  it('enqueues a form-encoded POST carrying HX-Request', async () => {
    // The header is the assertion. Without it the replay — a plain fetch,
    // possibly days later — hits @require_htmx and 400s.
    const enqueue = window.pwaMutationQueue.enqueue;
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115', name: 'Verbier' });

    expect(enqueue).toHaveBeenCalledTimes(1);
    const op = enqueue.mock.calls[0][0];
    expect(op.method).toBe('POST');
    expect(op.url).toBe(SYNC_URL);
    expect(op.headers['Content-Type']).toBe('application/x-www-form-urlencoded');
    expect(op.headers['HX-Request']).toBe('true');
  });

  it('carries the CSRF token in the body', async () => {
    // In the body rather than a header, matching favourites.js: one token
    // per session, read off whatever form the page already renders.
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });

    expect(bodyOf(window.pwaMutationQueue.enqueue).csrfmiddlewaretoken).toBe(CSRF);
  });

  it('sends a region by its id and no box', async () => {
    // A region's tiles are computed server-side from its real boundary, so
    // a box would be a second, coarser answer to a question already
    // answered.
    await sync.push({
      areaId: 'region-CH-4115',
      regionId: 'CH-4115',
      basemapKey: 'openfreemap_liberty',
      name: 'Verbier',
    });

    const body = bodyOf(window.pwaMutationQueue.enqueue);
    expect(body.area_id).toBe('region-CH-4115');
    expect(body.region_id).toBe('CH-4115');
    expect(body.bbox).toBe('');
    expect(body.basemap_key).toBe('openfreemap_liberty');
    expect(body.name).toBe('Verbier');
  });

  it('sends a custom area\'s box as one JSON value', async () => {
    // One value, not four fields: the server parses it once and validates
    // it as four numbers, so a partially-posted box can never be read as a
    // whole one.
    await sync.push({ areaId: 'custom-a1', bbox: [7.9, 46.4, 8.1, 46.6] });

    const body = bodyOf(window.pwaMutationQueue.enqueue);
    expect(JSON.parse(body.bbox)).toEqual([7.9, 46.4, 8.1, 46.6]);
    expect(body.region_id).toBe('');
  });

  it('posts the terrain-data choice as "true" or "false" (SNOW-1072)', async () => {
    // The two spellings area_sync accepts — anything else is a 400, which
    // the mutation queue would classify as permanent.
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115', terrain: true });
    await sync.push({ areaId: 'region-CH-2101', regionId: 'CH-2101' });

    expect(bodyOf(window.pwaMutationQueue.enqueue, 0).terrain).toBe('true');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 1).terrain).toBe('false');
  });

  it('posts a drop zone as a circle and everything else as a box (SNOW-1136)', async () => {
    // The two values area_sync accepts. A drop zone's tiles are clipped to
    // its circle, so the account has to say so for another device to
    // restore it as a drop zone rather than as the rectangle around it.
    const bbox = [7.9, 46.4, 8.1, 46.6];
    await sync.push({ areaId: 'custom-d1', bbox: bbox, type: 'dropzone' });
    await sync.push({ areaId: 'custom-c1', bbox: bbox, type: 'custom' });
    await sync.push({ areaId: 'custom-c2', bbox: bbox });
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });

    expect(bodyOf(window.pwaMutationQueue.enqueue, 0).shape).toBe('CIRCLE');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 1).shape).toBe('BOX');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 2).shape).toBe('BOX');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 3).shape).toBe('BOX');
  });

  it('does nothing at all on a page with no downloads surface', async () => {
    // No config element means no sync URL to post to. The no-op has to be
    // immediate rather than a request that fails, because this runs inside
    // a completed download's finish handler.
    document.body.innerHTML = '';
    const ok = await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });

    expect(ok).toBe(false);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('does nothing for a signed-out visitor', async () => {
    buildFixture({ eligible: false });
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });

    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('never rejects when the queue is unavailable', async () => {
    // It runs inside a completed download's finish handler. The tiles are
    // already on disk and are what the user asked for; a failure to record
    // them elsewhere must not surface as a failed download.
    delete window.pwaMutationQueue;
    await expect(sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' })).resolves.toBe(
      false,
    );
  });
});

describe('forget', () => {
  it('posts to the area\'s own forget URL', async () => {
    await sync.forget('custom-a1');

    const op = window.pwaMutationQueue.enqueue.mock.calls[0][0];
    expect(op.url).toBe('/downloads/partials/custom-a1/forget/');
    expect(op.headers['HX-Request']).toBe('true');
    // SNOW-1132: the row already gone is the outcome asked for.
    expect(op.doneStatuses).toEqual([404]);
  });

  it('lets a later download of the same area sync again', async () => {
    // The marker has to go with the row. Otherwise re-downloading an area
    // the user previously forgot would skip the push and leave the account
    // permanently out of step with the device.
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });
    await sync.forget('region-CH-4115');
    window.pwaMutationQueue.enqueue.mockClear();

    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });
    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
  });
});

describe('rename', () => {
  it('posts the new name to the area\'s own rename URL', async () => {
    await sync.rename('custom-a1', 'North ridge');

    const op = window.pwaMutationQueue.enqueue.mock.calls[0][0];
    expect(op.url).toBe('/downloads/partials/custom-a1/rename/');
    expect(bodyOf(window.pwaMutationQueue.enqueue).name).toBe('North ridge');
    // SNOW-1132: no account row means nothing left to rename.
    expect(op.doneStatuses).toEqual([404]);
  });

  it('declares no done status on a push', async () => {
    // A 404 on the upsert would be a real fault, not an applied write.
    await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });

    expect(window.pwaMutationQueue.enqueue.mock.calls[0][0].doneStatuses).toEqual([]);
  });
});

describe('accountAreas', () => {
  it('returns the account rows on a good response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({ areas: [{ area_id: 'region-CH-4115' }] }),
      })),
    );

    await expect(sync.accountAreas()).resolves.toEqual([{ area_id: 'region-CH-4115' }]);
  });

  it('resolves an empty list for an anonymous visitor (403)', async () => {
    // The gate is a product gate; the endpoint enforces it. Neither may
    // turn a read of local storage into an error the sheet has to handle.
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403 })));

    await expect(sync.accountAreas()).resolves.toEqual([]);
  });

  it('resolves an empty list when the request fails outright', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    await expect(sync.accountAreas()).resolves.toEqual([]);
  });

  it('resolves an empty list on a body it cannot read', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => {
          throw new SyntaxError('Unexpected token');
        },
      })),
    );

    await expect(sync.accountAreas()).resolves.toEqual([]);
  });

  it('does not call the network at all while offline', async () => {
    // The platform already knows the interface is down; that needs no
    // timeout to discover, and the sheet opens sooner for not asking.
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    setOnline(false);

    await expect(sync.accountAreas()).resolves.toEqual([]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('does not call the network for an anonymous visitor', async () => {
    // The endpoint would 403. Answering [] without asking is what keeps
    // the Manage downloads sheet's open path free of a round trip that
    // could only fail.
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    buildFixture({ eligible: false });

    await expect(sync.accountAreas()).resolves.toEqual([]);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('bounds the read with an abort signal', async () => {
    // A dead radio hangs rather than refusing
    // (docs/decisions/bounded-offline-read-paths.md). This read sits on
    // the path that opens the sheet, so an unbounded one would make a
    // panel that works offline appear not to.
    let seen = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url, init) => {
        seen = init;
        return { ok: true, json: async () => ({ areas: [] }) };
      }),
    );

    await sync.accountAreas();
    expect(seen.signal).toBeInstanceOf(AbortSignal);
    expect(seen.credentials).toBe('same-origin');
  });
});

describe('adopt', () => {
  const AREAS = [
    {
      id: 'region-CH-4115',
      name: 'Verbier',
      bytes: 40 * 1024 * 1024,
      basemapKey: 'openfreemap_liberty',
      regionId: 'CH-4115',
      onDevice: true,
      synced: false,
      principal: ME,
    },
    {
      id: 'custom-a1',
      name: 'Ridge',
      bytes: 12 * 1024 * 1024,
      bbox: [7.9, 46.4, 8.1, 46.6],
      onDevice: true,
      synced: false,
      principal: ME,
    },
  ];

  function installDownloads(areas) {
    window.pwaBasemapDownloads = { areas: vi.fn(async () => areas) };
  }

  beforeEach(async () => {
    // adopt() asks the core whether an id is a custom area's — the same
    // predicate every other reader uses, rather than parsing the prefix.
    await import('../../static/js/basemap_download_core.js');
  });

  it('pushes every local area the account has not been told about', async () => {
    installDownloads(AREAS);

    await expect(sync.adopt()).resolves.toBe(2);
    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(2);
  });

  it('uses a list it is handed, without reading it again (SNOW-1140)', async () => {
    // The sheet reads the list once at boot for this and its restore
    // notice; a second read would be a second account request.
    installDownloads([]);

    await expect(sync.adopt(AREAS)).resolves.toBe(2);
    expect(window.pwaBasemapDownloads.areas).not.toHaveBeenCalled();
  });

  it('records the stored name, never the localised default (SNOW-1133)', async () => {
    // An unrenamed custom area's `name` is the reader's in-memory
    // "Custom area N"; posting it froze one language into the account.
    installDownloads([{ ...AREAS[1], name: 'Custom area 2', storedName: '' }]);

    await sync.adopt();
    expect(bodyOf(window.pwaMutationQueue.enqueue).name).toBe('');
  });

  it('names a region by its name, which has no stored form', async () => {
    expect(sync.accountName({ name: 'Verbier' })).toBe('Verbier');
    expect(sync.accountName({ name: 'Custom area 1', storedName: 'Ridge' })).toBe('Ridge');
  });

  it('is a no-op once everything is already synced', async () => {
    // It runs on every authenticated load. A version that re-pushed each
    // time would write the whole list to the queue on every page view.
    installDownloads(AREAS);
    await sync.adopt();
    window.pwaMutationQueue.enqueue.mockClear();

    await expect(sync.adopt()).resolves.toBe(0);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('skips an orphaned bucket', async () => {
    // A download that failed partway. There is no completed area to
    // describe, and syncing one would offer another device something that
    // never finished here.
    installDownloads([{ id: 'region-CH-9999', bytes: 0, orphaned: true, onDevice: true }]);

    await expect(sync.adopt()).resolves.toBe(0);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('skips a shared base layer, which the endpoint would 400', async () => {
    // SNOW-860. `basemapDownloadedAreas()` has listed base layers since
    // SNOW-856 — real bytes on the device, so the reader reports them —
    // and this loop was the one consumer that did not exclude them. It
    // posted `base-<basemapKey>` to an endpoint whose `_AREA_ID_RE` is
    // `^(region|custom)-…`, so the request 400d; the mutation queue
    // classifies a 400 as permanent and never retries, leaving every
    // signed-in user with a downloaded area holding a failed row and a
    // red sync badge for good.
    //
    // The id is what the assertion is about, so it is spelled out rather
    // than built: `areaIdForBaseLayer` producing a shape the server
    // accepts would make this test pass while the bug remained.
    installDownloads([
      { id: 'base-openfreemap_liberty', name: 'Overview map', bytes: 8 * 1024 * 1024, onDevice: true },
    ]);

    await expect(sync.adopt()).resolves.toBe(0);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('pushes the real areas alongside a base layer, and only those', async () => {
    // The base layer must not take the whole pass down with it.
    installDownloads([
      AREAS[0],
      { id: 'base-openfreemap_liberty', name: 'Overview map', bytes: 8 * 1024 * 1024, onDevice: true },
      AREAS[1],
    ]);

    await expect(sync.adopt()).resolves.toBe(2);
    const posted = window.pwaMutationQueue.enqueue.mock.calls.map(
      ([op]) => new URLSearchParams(op.body).get('area_id'),
    );
    expect(posted).toEqual(['region-CH-4115', 'custom-a1']);
  });

  it('skips a custom area with no stored box', async () => {
    // The server refuses it — there would be nothing another device could
    // download — so it is not posted to be rejected.
    installDownloads([{ id: 'custom-legacy', name: 'Old', bytes: 1, onDevice: true }]);

    await expect(sync.adopt()).resolves.toBe(0);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('derives a region\'s id from its area id', async () => {
    installDownloads([AREAS[0]]);
    await sync.adopt();

    expect(bodyOf(window.pwaMutationQueue.enqueue).region_id).toBe('CH-4115');
  });

  it('carries each area\'s terrain-data flag to the account (SNOW-1072)', async () => {
    installDownloads([{ ...AREAS[0], terrain: true }, AREAS[1]]);
    await sync.adopt();

    expect(bodyOf(window.pwaMutationQueue.enqueue, 0).terrain).toBe('true');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 1).terrain).toBe('false');
  });

  it('carries a drop zone\'s shape to the account (SNOW-1136)', async () => {
    installDownloads([AREAS[0], { ...AREAS[1], type: 'dropzone' }]);
    await sync.adopt();

    expect(bodyOf(window.pwaMutationQueue.enqueue, 0).shape).toBe('BOX');
    expect(bodyOf(window.pwaMutationQueue.enqueue, 1).shape).toBe('CIRCLE');
  });

  it('does nothing for an anonymous visitor', async () => {
    // There is no account to adopt onto, and every endpoint would 403.
    buildFixture({ eligible: false });
    installDownloads(AREAS);

    await expect(sync.adopt()).resolves.toBe(0);
    expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
  });

  it('never rejects when the downloads bridge is missing', async () => {
    delete window.pwaBasemapDownloads;
    await expect(sync.adopt()).resolves.toBe(0);
  });
});

describe('whose area it is (SNOW-1142)', () => {
  // A shared browser: the bytes are device-wide on purpose, so the device
  // holds areas downloaded under more than one account. Only the
  // signed-in account's own are recorded to it; a legacy record (no
  // stamp) or an anonymous download ('') is claimed once, stamped first.
  const BOX = [7.9, 46.4, 8.1, 46.6];

  beforeEach(async () => {
    await import('../../static/js/basemap_download_core.js');
  });

  /**
   * Seed the two records and a bridge that reads them back the way the
   * real reader does — `principal` passed through, null when absent — so
   * a stamp written by a claim is what the next account's `adopt` sees.
   */
  function seedHeld(regions, customAreas) {
    const rows = installDbStub({
      'basemap.regions': regions || [],
      'basemap.customAreas': customAreas || [],
    });
    const principalOf = (entry) => (typeof entry.principal === 'string' ? entry.principal : null);
    window.pwaBasemapDownloads = {
      areas: vi.fn(async () => [
        ...(rows.get('basemap.regions') || []).map((entry) => ({
          id: 'region-' + entry.region_id,
          name: entry.name,
          regionId: entry.region_id,
          onDevice: true,
          principal: principalOf(entry),
        })),
        ...(rows.get('basemap.customAreas') || []).map((entry) => ({
          id: entry.id,
          name: entry.name || 'Custom area 1',
          storedName: entry.name || '',
          bbox: entry.bbox,
          onDevice: true,
          principal: principalOf(entry),
        })),
      ]),
    };
    return rows;
  }

  function postedIds() {
    return window.pwaMutationQueue.enqueue.mock.calls.map(([op]) =>
      new URLSearchParams(op.body).get('area_id'),
    );
  }

  describe('ownership', () => {
    it('is the one rule both callers ask', () => {
      expect(sync.ownership(ME, ME)).toBe('own');
      expect(sync.ownership(OTHER, ME)).toBe('foreign');
      expect(sync.ownership(null, ME)).toBe('claim');
      expect(sync.ownership(undefined, ME)).toBe('claim');
      expect(sync.ownership('', ME)).toBe('claim');
      expect(sync.ownership(ME, '')).toBe('none');
      expect(sync.ownership(null, '')).toBe('none');
    });
  });

  describe('stampPrincipal', () => {
    it('stamps a new record with the signed-in account', () => {
      expect(sync.stampPrincipal({ id: 'custom-a1' }, undefined).principal).toBe(ME);
    });

    it('stamps an anonymous download with the empty string', () => {
      setAccount('');
      expect(sync.stampPrincipal({ id: 'custom-a1' }, undefined)).toHaveProperty('principal', '');
    });

    it('keeps the predecessor\'s stamp on a re-download', () => {
      const out = sync.stampPrincipal({ id: 'custom-a1' }, { id: 'custom-a1', principal: OTHER });
      expect(out.principal).toBe(OTHER);
    });

    it('adds no stamp where the predecessor was a legacy record', () => {
      // The claim rule, not a refresh, decides whose a legacy record is.
      const out = sync.stampPrincipal({ id: 'custom-a1', principal: ME }, { id: 'custom-a1' });
      expect(out).not.toHaveProperty('principal');
    });
  });

  describe('push', () => {
    it('declines a record stamped with another account', async () => {
      await expect(
        sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115', principal: OTHER }),
      ).resolves.toBe(false);
      expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
    });

    it('declines a legacy predecessor\'s record, leaving it to adopt\'s claim', async () => {
      await expect(
        sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115', principal: undefined }),
      ).resolves.toBe(false);
    });

    it('sends the signed-in account\'s own record', async () => {
      await expect(
        sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115', principal: ME }),
      ).resolves.toBe(true);
    });
  });

  describe('adopt', () => {
    it('never records another account\'s area', async () => {
      seedHeld([{ region_id: 'CH-4115', name: 'Verbier', principal: OTHER }], [
        { id: 'custom-a1', name: 'Ridge', bbox: BOX, principal: OTHER },
      ]);

      await expect(sync.adopt()).resolves.toBe(0);
      expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
    });

    it('records its own and skips the other account\'s, side by side', async () => {
      seedHeld([
        { region_id: 'CH-4115', name: 'Verbier', principal: ME },
        { region_id: 'CH-2101', name: 'Aletsch', principal: OTHER },
      ]);

      await expect(sync.adopt()).resolves.toBe(1);
      expect(postedIds()).toEqual(['region-CH-4115']);
    });

    it('claims a legacy record once and stamps it', async () => {
      const rows = seedHeld([{ region_id: 'CH-4115', name: 'Verbier' }]);

      await expect(sync.adopt()).resolves.toBe(1);
      expect(rows.get('basemap.regions')[0].principal).toBe(ME);
    });

    it('claims an anonymous download once and stamps it', async () => {
      const rows = seedHeld([], [{ id: 'custom-a1', bbox: BOX, principal: '' }]);

      await expect(sync.adopt()).resolves.toBe(1);
      expect(postedIds()).toEqual(['custom-a1']);
      expect(rows.get('basemap.customAreas')[0].principal).toBe(ME);
    });

    it('never re-pushes a claimed area for a second account', async () => {
      // The whole bug: a second person signing in on the same browser.
      seedHeld([{ region_id: 'CH-4115', name: 'Verbier' }], [
        { id: 'custom-a1', name: 'Ridge', bbox: BOX, principal: '' },
      ]);
      await sync.adopt();
      window.pwaMutationQueue.enqueue.mockClear();

      setAccount(OTHER);
      await expect(sync.adopt()).resolves.toBe(0);
      expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
    });

    it('does not push a claim whose stamp could not be written', async () => {
      // Pushing without the stamp would let the next account claim it too.
      seedHeld([{ region_id: 'CH-4115', name: 'Verbier' }]);
      window.pwaDb.put = vi.fn(async () => {
        throw new Error('quota');
      });

      await expect(sync.adopt()).resolves.toBe(0);
      expect(window.pwaMutationQueue.enqueue).not.toHaveBeenCalled();
    });

    it('claims nothing when the record is not held here', async () => {
      // A list handed in that names an area this device has no record of.
      installDbStub({});
      await expect(
        sync.adopt([{ id: 'region-CH-4115', regionId: 'CH-4115', onDevice: true, principal: null }]),
      ).resolves.toBe(0);
    });

    it('stamps on one readModifyWrite transaction where the store has one', async () => {
      const rows = seedHeld([{ region_id: 'CH-4115', name: 'Verbier' }]);
      window.pwaDb.readModifyWrite = vi.fn(async (_store, key, mutate) => {
        const current = rows.has(key) ? { key, value: rows.get(key) } : undefined;
        const next = mutate(current);
        if (next) rows.set(next.key, next.value);
        return next || current;
      });

      await sync.adopt();
      expect(window.pwaDb.readModifyWrite).toHaveBeenCalledWith(
        'meta:app',
        'basemap.regions',
        expect.any(Function),
      );
      expect(rows.get('basemap.regions')[0].principal).toBe(ME);
    });
  });

  describe('recordHeld', () => {
    it('skips an account-only row, which has no record to stamp', async () => {
      await expect(
        sync.recordHeld({ id: 'region-CH-4115', onDevice: false, principal: null }),
      ).resolves.toBe(false);
    });

    it('sends the stored name and the terrain opt-in (SNOW-1133, SNOW-1072)', async () => {
      await sync.recordHeld({
        id: 'custom-k1',
        name: 'Custom area 4',
        storedName: '',
        bbox: BOX,
        onDevice: true,
        terrain: true,
        principal: ME,
      });
      const body = bodyOf(window.pwaMutationQueue.enqueue);
      expect(body.name).toBe('');
      expect(body.terrain).toBe('true');
    });
  });

  describe('the synced marker', () => {
    it('is kept per principal', async () => {
      const rows = installDbStub({});
      await sync.push({ areaId: 'region-CH-4115', regionId: 'CH-4115' });
      setAccount(OTHER);
      await sync.push({ areaId: 'custom-a1', bbox: BOX });

      expect(rows.get('basemap.syncedAreaIds')).toEqual({
        [ME]: ['region-CH-4115'],
        [OTHER]: ['custom-a1'],
      });
    });

    it('reads the old flat array as nobody\'s, so the first adopt re-checks', async () => {
      // Every earlier release wrote a flat list that never said which
      // account it was for. Trusting it would skip this account's own
      // areas; reading it as nobody's re-checks each against ownership().
      const rows = seedHeld([
        { region_id: 'CH-4115', name: 'Verbier', principal: ME },
        { region_id: 'CH-2101', name: 'Aletsch', principal: OTHER },
      ]);
      rows.set('basemap.syncedAreaIds', ['region-CH-4115', 'region-CH-2101']);

      await expect(sync.adopt()).resolves.toBe(1);
      expect(postedIds()).toEqual(['region-CH-4115']);
      expect(rows.get('basemap.syncedAreaIds')).toEqual({ [ME]: ['region-CH-4115'] });
    });

    it('forget unmarks only the signed-in account\'s entry', async () => {
      const rows = installDbStub({
        'basemap.syncedAreaIds': { [ME]: ['custom-a1'], [OTHER]: ['custom-a1'] },
      });
      await sync.forget('custom-a1');

      expect(rows.get('basemap.syncedAreaIds')).toEqual({ [ME]: [], [OTHER]: ['custom-a1'] });
    });
  });
});
