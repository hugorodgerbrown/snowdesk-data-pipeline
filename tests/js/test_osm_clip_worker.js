/*
 * tests/js/test_osm_clip_worker.js — the `osmclip://` worker's message
 * handler (SNOW-1460, static/js/osm_clip_worker.js).
 *
 * The worker runs here on jsdom's `self`, with `importScripts` stubbed (the
 * kit and the core are imported up front, as importScripts would load
 * them), `fetch` stubbed per URL and `postMessage` recording each reply.
 *
 * What is pinned: a border tile comes back re-encoded, and its decoded
 * features are the core's output; an outside tile comes back as fetched
 * and an inside one empty, without fetching it; a failed fetch replies with an error; a
 * cancelled id gets no reply and its fetch is aborted.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/vector_tile_kit.min.js';
import '../../static/js/osm_clip_core.js';

const { Pbf, VectorTile, vtpbf, polygonClipping } = window.VectorTileKit;
const core = window.pwaOsmClipCore;

const TILEJSON = 'https://tiles.example.test/planet';
const TEMPLATE = 'https://tiles.example.test/planet/v1/{z}/{x}/{y}.pbf';
// The western hemisphere: tile 1/0/0 is inside, 1/1/0 outside, 0/0/0 on
// the border with its left half inside.
const WEST = [[[[-180, -85.06], [0, -85.06], [0, 85.06], [-180, 85.06], [-180, -85.06]]]];

/** A closed rectangle ring. */
const rect = (x0, y0, x1, y1) => [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];

const LAYERS = {
  water: { extent: 4096, features: [{ type: 3, geometry: [rect(1024, 1024, 3072, 3072)], tags: { class: 'lake' } }] },
  transportation: { extent: 4096, features: [{ type: 2, geometry: [[[1000, 100], [3000, 100]]], tags: { class: 'path' } }] },
  place: { extent: 4096, features: [{ type: 1, geometry: [[100, 100], [3000, 3000]], tags: { name: 'A' } }] },
};
const TILE_BYTES = (() => {
  const encoded = vtpbf.fromGeojsonVt(LAYERS, { version: 2, extent: 4096 });
  return encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength);
})();

/** Decode bytes to {layer: [{type, geometry, tags}]}. */
function decode(bytes) {
  const tile = new VectorTile(new Pbf(new Uint8Array(bytes)));
  const out = {};
  for (const name of Object.keys(tile.layers)) {
    const layer = tile.layers[name];
    out[name] = [];
    for (let i = 0; i < layer.length; i += 1) {
      const f = layer.feature(i);
      out[name].push({ type: f.type, tags: f.properties, geometry: f.loadGeometry().map((r) => r.map((p) => [p.x, p.y])) });
    }
  }
  return out;
}

const replies = [];
let tileResponse = () => Promise.resolve(new Response(TILE_BYTES.slice(0)));
const fetchMock = vi.fn((url, init) => {
  if (url === TILEJSON) return Promise.resolve(new Response(JSON.stringify({ tiles: [TEMPLATE] })));
  return tileResponse(url, init);
});

const send = (data) => window.dispatchEvent(new MessageEvent('message', { data }));
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

beforeAll(async () => {
  vi.stubGlobal('fetch', fetchMock);
  self.importScripts = vi.fn();
  self.postMessage = (message) => replies.push(message);
  await import('../../static/js/osm_clip_worker.js');
  send({ type: 'init', kitUrl: '/static/js/vector_tile_kit.min.js', coreUrl: '/static/js/osm_clip_core.js', tilejsonUrl: TILEJSON, extent: WEST });
});

afterAll(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  replies.length = 0;
  tileResponse = () => Promise.resolve(new Response(TILE_BYTES.slice(0)));
});

describe('the osmclip worker', () => {
  it('loads the kit and the core on init', () => {
    expect(self.importScripts).toHaveBeenCalledWith('/static/js/vector_tile_kit.min.js', '/static/js/osm_clip_core.js');
  });

  it('answers a border tile with re-encoded bytes whose features are the core\'s output', async () => {
    send({ id: 1, url: 'osmclip://0/0/0' });
    await settle();
    expect(fetchMock).toHaveBeenCalledWith('https://tiles.example.test/planet/v1/0/0/0.pbf', expect.anything());
    expect(replies).toHaveLength(1);
    const [{ id, data, error }] = replies;
    expect(id).toBe(1);
    expect(error).toBeUndefined();
    const fetched = Object.fromEntries(
      Object.entries(decode(TILE_BYTES)).map(([name, features]) => [name, { extent: 4096, features }]),
    );
    const expected = core.clipLayers(fetched, WEST, { z: 0, x: 0, y: 0 }, polygonClipping).layers;
    const got = decode(data);
    expect(Object.keys(got).sort()).toEqual(Object.keys(expected).sort());
    for (const name of Object.keys(expected)) {
      expect(got[name].map((f) => f.tags)).toEqual(expected[name].features.map((f) => f.tags));
    }
    // The outside parts only: the lake from x 2048, the path from 2048, one place.
    expect(Math.min(...got.water[0].geometry[0].map(([x]) => x))).toBe(2048);
    expect(got.transportation[0].geometry).toEqual([[[2048, 100], [3000, 100]]]);
    expect(got.place[0].geometry).toEqual([[[3000, 3000]]]);
  });

  it('answers an outside tile with the bytes as fetched, and an inside one empty without a fetch', async () => {
    fetchMock.mockClear();
    send({ id: 2, url: 'osmclip://1/1/0' });
    send({ id: 3, url: 'osmclip://1/0/0' });
    await settle();
    const byId = Object.fromEntries(replies.map((r) => [r.id, r]));
    expect(new Uint8Array(byId[2].data)).toEqual(new Uint8Array(TILE_BYTES));
    expect(byId[3].data.byteLength).toBe(0);
    const fetched = fetchMock.mock.calls.map(([url]) => url);
    expect(fetched).toContain('https://tiles.example.test/planet/v1/1/1/0.pbf');
    expect(fetched).not.toContain('https://tiles.example.test/planet/v1/1/0/0.pbf');
  });

  it('replies with an error when the fetch fails', async () => {
    tileResponse = () => Promise.resolve(new Response('', { status: 503 }));
    send({ id: 4, url: 'osmclip://0/0/0' });
    await settle();
    expect(replies).toEqual([{ id: 4, error: 'osm_clip: tile 503' }]);
  });

  it('drops a cancelled id and aborts its fetch', async () => {
    let signal = null;
    tileResponse = (_url, init) => {
      signal = init.signal;
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    };
    send({ id: 5, url: 'osmclip://0/0/0' });
    await settle();
    send({ type: 'cancel', id: 5 });
    await settle();
    expect(signal.aborted).toBe(true);
    expect(replies).toEqual([]);
  });
});
