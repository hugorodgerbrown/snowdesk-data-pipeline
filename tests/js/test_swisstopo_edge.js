/*
 * tests/js/test_swisstopo_edge.js — the swisstopoedge:// tile protocol
 * (SNOW-1457), against a stubbed fetch and canvas.
 *
 * What is pinned:
 *   - registration: the protocol is added once, under its name;
 *   - a tile wholly inside Switzerland makes no WMTS request;
 *   - a blank z16 tile in the eastern band (11.0°E) falls back through
 *     z15 and z14 to z13, cuts its square from it, and stops there;
 *   - a sibling under the same ancestors reuses them;
 *   - a tile blank all the way down to z13 is transparent;
 *   - a tile on the border has Switzerland erased; one clear of it does
 *     not;
 *   - a failed request, its own or an ancestor's, or a mask that cannot
 *     load, rejects; a failed ancestor is asked for again.
 *
 * jsdom has no canvas, so a small fake stands in. It records what is drawn,
 * and answers getImageData from it: a drawn bitmap is white or not as the
 * fake WMTS said, and a filled mask covers the tile when all its corners
 * and its centre fall inside the traced rings.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/swisstopo_edge_core.js';

const core = window.pwaSwisstopoEdgeCore;
const MASK_URL = '/api/major-regions.geojson';

/** Switzerland, for the test: a box. */
const MASK = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    properties: {},
    geometry: { type: 'Polygon', coordinates: [[[6, 45.8], [10.5, 45.8], [10.5, 47.8], [6, 47.8], [6, 45.8]]] },
  }],
};

/** Where the fake WMTS has detail: to 11.3°E at z13 and below, 10.5°E at z14–15. */
const hasDetail = (z, lng) => (z <= 13 ? lng < 11.3 : z <= 15 ? lng < 10.5 : lng < 10.6);

const tileAt = (lng, lat, z) => {
  const n = 2 ** z;
  const rad = (lat * Math.PI) / 180;
  return {
    z,
    x: Math.floor(((lng + 180) / 360) * n),
    y: Math.floor(((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2) * n),
  };
};
const url = (t) => `swisstopoedge://${t.z}/${t.x}/${t.y}`;

let wmtsRequests;
let failWmts;
/** Zooms whose WMTS requests fail. */
let failZooms;
let failMask;
let contexts;

/** Even-odd test of a pixel point against the traced subpaths. */
const inside = (paths, px, py) => {
  let hit = false;
  for (const ring of paths) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) hit = !hit;
    }
  }
  return hit;
};

class FakeContext {
  constructor(size) {
    this.size = size;
    this.paths = [];
    this.filled = false;
    this.drawn = null;
    this.drawArgs = null;
    this.erased = false;
    this.globalCompositeOperation = 'source-over';
    contexts.push(this);
  }

  beginPath() { this.paths = []; }

  moveTo(x, y) { this.paths.push([[x, y]]); }

  lineTo(x, y) { this.paths.at(-1).push([x, y]); }

  closePath() {}

  fill() {
    if (this.globalCompositeOperation === 'destination-out') this.erased = true;
    else this.filled = true;
  }

  drawImage(image, ...args) {
    this.drawn = image;
    this.drawArgs = args;
  }

  getImageData(_x, _y, w, h) {
    const data = new Uint8ClampedArray(w * h * 4);
    if (this.filled) {
      const s = this.size;
      const corners = [[0.5, 0.5], [s - 0.5, 0.5], [0.5, s - 0.5], [s - 0.5, s - 0.5], [s / 2, s / 2]];
      const covered = corners.every(([px, py]) => inside(this.paths, px, py));
      for (let i = 3; i < data.length; i += 4) data[i] = covered ? 255 : 0;
      return { data };
    }
    const white = this.drawn && !this.drawn.detail;
    for (let i = 0; i < data.length; i += 4) data.set(white ? [255, 255, 255, 255] : [200, 210, 220, 255], i);
    return { data };
  }
}

class FakeCanvas {
  constructor(w) {
    this.ctx = new FakeContext(w);
  }

  getContext() { return this.ctx; }
}

beforeEach(() => {
  wmtsRequests = [];
  failWmts = false;
  failZooms = new Set();
  failMask = false;
  contexts = [];
  document.body.innerHTML = `<div id="map" data-major-regions-url="${MASK_URL}"></div>`;
  globalThis.OffscreenCanvas = FakeCanvas;
  globalThis.ImageData = class { constructor(w, h) { this.width = w; this.height = h; this.empty = true; } };
  globalThis.createImageBitmap = vi.fn(async (source) => {
    if (source instanceof FakeCanvas) return { out: source.ctx, close() {} };
    if (source && source.empty) return { empty: true, close() {} };
    return { detail: source.detail, z: source.z, close() {} };
  });
  globalThis.fetch = vi.fn(async (requested) => {
    if (requested.startsWith(MASK_URL)) {
      if (failMask) return { ok: false, status: 500 };
      return { ok: true, status: 200, json: async () => MASK };
    }
    const [z, x, y] = requested.match(/3857\/(\d+)\/(\d+)\/(\d+)\.jpeg$/).slice(1).map(Number);
    wmtsRequests.push(z);
    if (failWmts || failZooms.has(z)) throw new TypeError('network');
    const lng = ((x + 0.5) / 2 ** z) * 360 - 180;
    return { ok: true, status: 200, blob: async () => ({ z, x, y, detail: hasDetail(z, lng) }) };
  });
  if (window.pwaSwisstopoEdge) window.pwaSwisstopoEdge.reset();
});

const protocols = {};

/**
 * The handler, from the protocol loaded once with addProtocol recorded.
 * It is an IIFE, so a second import would not run it again.
 */
const load = async () => {
  if (!window.pwaSwisstopoEdge) {
    globalThis.maplibregl = { addProtocol: (name, handler) => { protocols[name] = handler; } };
    await import('../../static/js/swisstopo_edge.js');
  }
  window.pwaSwisstopoEdge.reset();
  return window.pwaSwisstopoEdge.handle;
};

const run = (handler, tile) => handler({ url: url(tile) }, new AbortController());

describe('the swisstopoedge protocol', () => {
  it('registers under its name', async () => {
    const handler = await load();
    expect(protocols[core.PROTOCOL]).toBe(handler);
  });

  it('makes no request for a tile wholly inside Switzerland', async () => {
    const handler = await load();
    const result = await run(handler, tileAt(8, 46.8, 14));
    expect(wmtsRequests).toEqual([]);
    expect(result.data.empty).toBe(true);
  });

  it('falls back from a blank z16 tile at 11.0°E to z13, and stops there', async () => {
    const handler = await load();
    const tile = tileAt(11.0, 47.1, 16);
    const result = await run(handler, tile);
    expect(wmtsRequests).toEqual([16, 15, 14, 13]);
    const out = result.data.out;
    expect(out.drawn.z).toBe(13);
    const [sx, sy, size] = out.drawArgs;
    expect(size).toBe(32);
    expect(sx).toBe((tile.x % 8) * 32);
    expect(sy).toBe((tile.y % 8) * 32);
  });

  it('reuses the ancestors for a sibling tile', async () => {
    const handler = await load();
    const tile = tileAt(11.0, 47.1, 16);
    const even = { ...tile, x: tile.x - (tile.x % 2) };
    await run(handler, even);
    wmtsRequests = [];
    await run(handler, { ...even, x: even.x + 1 });
    expect(wmtsRequests).toEqual([16]);
  });

  it('draws nothing where there is no detail down to z13', async () => {
    const handler = await load();
    const result = await run(handler, tileAt(12.5, 47.3, 16));
    expect(wmtsRequests).toEqual([16, 15, 14, 13]);
    expect(result.data.empty).toBe(true);
  });

  it('erases Switzerland from a border tile, and leaves a tile clear of it alone', async () => {
    const handler = await load();
    const border = await run(handler, tileAt(10.5, 47, 13));
    expect(border.data.out.erased).toBe(true);
    const clear = await run(handler, tileAt(11.0, 47.1, 13));
    expect(clear.data.out.erased).toBe(false);
  });

  it('rejects a tile whose request failed, so MapLibre asks again', async () => {
    const handler = await load();
    failWmts = true;
    await expect(run(handler, tileAt(11.0, 47.1, 13))).rejects.toThrow(/failed/);
  });

  it('rejects a blank tile whose ancestor failed, and asks for that ancestor again', async () => {
    const handler = await load();
    failZooms = new Set([13]);
    const tile = tileAt(11.0, 47.1, 16);
    await expect(run(handler, tile)).rejects.toThrow(/failed/);
    failZooms = new Set();
    wmtsRequests = [];
    const result = await run(handler, tile);
    expect(wmtsRequests).toEqual([16, 13]);
    expect(result.data.out.drawn.z).toBe(13);
  });

  it('rejects every tile while the mask cannot load, then recovers', async () => {
    const handler = await load();
    failMask = true;
    await expect(run(handler, tileAt(11.0, 47.1, 13))).rejects.toThrow(/mask/);
    expect(wmtsRequests).toEqual([]);
    failMask = false;
    await expect(run(handler, tileAt(11.0, 47.1, 13))).resolves.toBeTruthy();
  });
});
