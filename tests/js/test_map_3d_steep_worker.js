/*
 * tests/js/test_map_3d_steep_worker.js — the 3-D map's Steep terrain worker.
 *
 * The worker runs here on jsdom's window, standing in for a worker's
 * global: importScripts, fetch, createImageBitmap, OffscreenCanvas and
 * postMessage are stubbed, and the real terrain cores are loaded.
 *
 * Pinned, because each is the way this goes wrong:
 *   - init builds the classifier from the scripts the page passes, and a
 *     tile before init is ignored rather than throwing;
 *   - a tile is fetched without credentials, painted, and posted back as a
 *     transferable bitmap under the request's id;
 *   - /map/'s cache rules hold across the message boundary: a 204 inside
 *     coverage is no data and is cached, and a failed request draws an
 *     empty tile and is not cached;
 *   - an abort aborts the request in flight.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/slope_classes_core.js';
import '../../static/js/terrain_filter_core.js';

const TEMPLATE = 'https://tiles.example/classes/{z}/{x}/{y}.png';
// Zermatt at z12: inside the survey's coverage.
const INSIDE = { z: 12, x: 2133, y: 1458 };
// The North Atlantic at z12: outside it.
const OUTSIDE = { z: 12, x: 1000, y: 1000 };

let handler = null;

/** Hand the worker one message and wait for it to finish. */
async function send(data) {
  await handler({ data });
}

beforeEach(async () => {
  const realAdd = window.addEventListener.bind(window);
  vi.spyOn(window, 'addEventListener').mockImplementation((type, fn, opts) => {
    if (type === 'message') handler = fn;
    else realAdd(type, fn, opts);
  });
  window.importScripts = vi.fn();
  window.postMessage = vi.fn();
  vi.stubGlobal('createImageBitmap', vi.fn(async () => ({ close: vi.fn() })));
  vi.stubGlobal(
    'OffscreenCanvas',
    class {
      constructor(w, h) {
        this.w = w;
        this.h = h;
      }
      getContext() {
        return {
          drawImage: () => {},
          getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        };
      }
    },
  );
  vi.stubGlobal(
    'ImageData',
    class {
      constructor(data, w, h) {
        this.data = data;
        this.width = w;
        this.height = h;
      }
    },
  );
  vi.resetModules();
  await import('../../static/js/map_3d_steep_worker.js');
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete window.importScripts;
});

/** Initialise the worker as map_3d.js does. */
async function init() {
  await send({ type: 'init', scripts: ['/static/a.js', '/static/b.js'], tileTemplate: TEMPLATE });
}

describe('map_3d_steep_worker', () => {
  it('imports the scripts the page passes on init', async () => {
    await init();
    expect(window.importScripts).toHaveBeenCalledWith('/static/a.js', '/static/b.js');
  });

  it('ignores a tile asked for before init', async () => {
    vi.stubGlobal('fetch', vi.fn());
    await send({ type: 'tile', id: 1, ...INSIDE });
    expect(fetch).not.toHaveBeenCalled();
    expect(window.postMessage).not.toHaveBeenCalled();
  });

  it('fetches without credentials and posts a transferable bitmap under the id', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, blob: async () => new Blob() })));
    await init();
    await send({ type: 'tile', id: 7, ...INSIDE });
    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe(`https://tiles.example/classes/12/${INSIDE.x}/${INSIDE.y}.png`);
    expect(options.credentials).toBe('omit');
    const [reply, transfer] = window.postMessage.mock.calls[0];
    expect(reply.id).toBe(7);
    expect(transfer).toEqual([reply.bitmap]);
  });

  it('caches a 204 inside coverage as no data', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 204 })));
    await init();
    await send({ type: 'tile', id: 1, ...INSIDE });
    await send({ type: 'tile', id: 2, ...INSIDE });
    expect(fetch).toHaveBeenCalledOnce();
    expect(window.postMessage.mock.calls.map(([m]) => m.id)).toEqual([1, 2]);
    // No data is painted: the tile handed to createImageBitmap is not blank.
    const painted = createImageBitmap.mock.calls.at(-1)[0].data;
    expect(painted.some((v) => v !== 0)).toBe(true);
  });

  it('draws an empty tile outside coverage', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 204 })));
    await init();
    await send({ type: 'tile', id: 1, ...OUTSIDE });
    const painted = createImageBitmap.mock.calls.at(-1)[0].data;
    expect(painted.every((v) => v === 0)).toBe(true);
    expect(window.postMessage.mock.calls[0][0].id).toBe(1);
  });

  it('draws an empty tile on a failed request and does not cache it', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 503 })));
    await init();
    await send({ type: 'tile', id: 1, ...INSIDE });
    await send({ type: 'tile', id: 2, ...INSIDE });
    expect(fetch).toHaveBeenCalledTimes(2);
    const painted = createImageBitmap.mock.calls.at(-1)[0].data;
    expect(painted.every((v) => v === 0)).toBe(true);
  });

  it('aborts the request in flight and reports it', async () => {
    let seen = null;
    vi.stubGlobal(
      'fetch',
      vi.fn(
        (url, { signal }) =>
          new Promise((resolve, reject) => {
            seen = signal;
            signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
          }),
      ),
    );
    await init();
    const running = send({ type: 'tile', id: 3, ...INSIDE });
    await Promise.resolve();
    await send({ type: 'abort', id: 3 });
    await running;
    expect(seen.aborted).toBe(true);
    expect(window.postMessage.mock.calls[0][0]).toMatchObject({ id: 3, error: expect.stringContaining('Abort') });
  });
});
