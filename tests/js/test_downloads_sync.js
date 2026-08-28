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

/**
 * Render the config carrier and the page's CSRF input.
 *
 * ``#map-custom-download-control`` is the roundel every download surface
 * reads its wiring off; the token comes from the sign-out form the nav
 * renders for a signed-in user, which is the same read
 * ``account_favourites.js`` makes.
 *
 * @param {{eligible?: boolean}} [options]
 */
function buildFixture(options) {
  const opts = Object.assign({ eligible: true }, options || {});
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
    },
    {
      id: 'custom-a1',
      name: 'Ridge',
      bytes: 12 * 1024 * 1024,
      bbox: [7.9, 46.4, 8.1, 46.6],
      onDevice: true,
      synced: false,
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
