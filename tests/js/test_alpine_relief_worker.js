/*
 * tests/js/test_alpine_relief_worker.js — the `alpinerelief://` worker's
 * message handler (SNOW-1460, static/js/alpine_relief_worker.js).
 *
 * The worker runs on jsdom's `self`, with `importScripts` stubbed (the core
 * is imported up front), `fetch` stubbed, and `createImageBitmap`,
 * `OffscreenCanvas` and `ImageData` stubbed to hand pixels straight
 * through: the canvas's "PNG" is the painted RGBA, so the reply can be
 * checked against the core's own paint.
 *
 * What is pinned: a tile comes back as the PNG bytes of the core's paint,
 * fetched from the elevation template without credentials; a failed fetch
 * replies with an error; a cancelled id gets no reply and its fetch is
 * aborted.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/swisstopo_extent.js';
import '../../static/js/alpine_relief_core.js';

const core = window.pwaAlpineReliefCore;
const { DETAILED_RELIEF } = window.pwaSwisstopoExtent;
const TEMPLATE = 'https://dem.example.test/{z}/{x}/{y}.webp';
const SIZE = 8;

/** A steep DEM: heights rising fast to the east, as Terrarium RGBA. */
const DEM = (() => {
  const out = new Uint8ClampedArray(SIZE * SIZE * 4);
  for (let row = 0; row < SIZE; row += 1) {
    for (let col = 0; col < SIZE; col += 1) {
      const v = 32768 + 1000 + col * 4000;
      out.set([Math.floor(v / 256), v % 256, 0, 255], (row * SIZE + col) * 4);
    }
  }
  return out;
})();

const replies = [];
let demResponse = () => Promise.resolve(new Response('dem'));
const fetchMock = vi.fn((url, init) => demResponse(url, init));

/** A canvas whose pixels are whatever was last drawn or put. */
class FakeCanvas {
  constructor(width, height) {
    this.width = width;
    this.height = height;
    this.pixels = new Uint8ClampedArray(width * height * 4);
  }

  getContext() {
    return {
      drawImage: () => { this.pixels = DEM.slice(); },
      getImageData: () => ({ data: this.pixels }),
      clearRect: () => { this.pixels = new Uint8ClampedArray(this.pixels.length); },
      putImageData: (image) => { this.pixels = image.data; },
    };
  }

  convertToBlob() {
    return Promise.resolve({ arrayBuffer: () => Promise.resolve(this.pixels.slice().buffer) });
  }
}

const send = (data) => window.dispatchEvent(new MessageEvent('message', { data }));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

beforeAll(async () => {
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('createImageBitmap', () => Promise.resolve({ width: SIZE, height: SIZE, close: () => {} }));
  vi.stubGlobal('OffscreenCanvas', FakeCanvas);
  vi.stubGlobal('ImageData', class { constructor(data, w, h) { Object.assign(this, { data, width: w, height: h }); } });
  self.importScripts = vi.fn();
  self.postMessage = (message) => replies.push(message);
  await import('../../static/js/alpine_relief_worker.js');
  send({ type: 'init', coreUrl: '/static/js/alpine_relief_core.js', demTileUrl: TEMPLATE, relief: DETAILED_RELIEF });
});

afterAll(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  replies.length = 0;
  demResponse = () => Promise.resolve(new Response('dem'));
});

describe('the alpinerelief worker', () => {
  it('loads the core on init', () => {
    expect(self.importScripts).toHaveBeenCalledWith('/static/js/alpine_relief_core.js');
  });

  it('answers a tile with the PNG bytes of the core\'s paint', async () => {
    send({ id: 1, url: 'alpinerelief://13/4355/2869' });
    await settle();
    expect(fetchMock).toHaveBeenCalledWith(
      'https://dem.example.test/13/4355/2869.webp',
      expect.objectContaining({ credentials: 'omit' }),
    );
    expect(replies).toHaveLength(1);
    const [{ id, data, error }] = replies;
    expect(id).toBe(1);
    expect(error).toBeUndefined();
    const expected = core.paint(DEM, SIZE, { z: 13, x: 4355, y: 2869 }, DETAILED_RELIEF);
    expect(new Uint8ClampedArray(data)).toEqual(expected);
    expect(expected.some((v) => v > 0)).toBe(true);
  });

  it('replies with an error when the fetch fails', async () => {
    demResponse = () => Promise.resolve(new Response('', { status: 404 }));
    send({ id: 2, url: 'alpinerelief://13/4355/2869' });
    await settle();
    expect(replies).toEqual([{ id: 2, error: 'alpine_relief: elevation tile 404' }]);
  });

  it('drops a cancelled id and aborts its fetch', async () => {
    let signal = null;
    demResponse = (_url, init) => {
      signal = init.signal;
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    };
    send({ id: 3, url: 'alpinerelief://13/4355/2869' });
    await settle();
    send({ type: 'cancel', id: 3 });
    await settle();
    expect(signal.aborted).toBe(true);
    expect(replies).toEqual([]);
  });
});
