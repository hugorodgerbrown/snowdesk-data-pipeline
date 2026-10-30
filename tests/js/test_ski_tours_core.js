/*
 * tests/js/test_ski_tours_core.js — choosing a SAC ski tour from the map
 * (static/js/ski_tours_core.js, SNOW-1278).
 *
 * Which tour a tap opens, the rectangle a view loads, and the URL the tour
 * panel is fetched from.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/ski_tours_core.js';

const core = self.pwaSkiToursCore;

// A tour line in screen px already: the tests project with the identity.
const tour = (uuid, coords) => ({
  geometry: { type: 'LineString', coordinates: coords },
  properties: { kind: 'tour', uuid },
});
const project = (c) => ({ x: c[0], y: c[1] });

describe('loadMode', () => {
  it('loads nothing out past z9, every tour thinned to z12, the detail from it', () => {
    expect(core.MINZOOM).toBe(9);
    expect(core.loadMode(8.99)).toBe('none');
    expect(core.loadMode(9)).toBe('overview');
    expect(core.loadMode(11.99)).toBe('overview');
    expect(core.loadMode(core.DETAIL_MINZOOM)).toBe('detail');
    expect(core.DETAIL_MINZOOM).toBe(12);
    expect(core.LABEL_MINZOOM).toBe(10);
  });
});

describe('nearestTour', () => {
  it('answers nothing for nothing', () => {
    expect(core.nearestTour([], { x: 0, y: 0 }, project)).toBeNull();
    expect(core.nearestTour([{ properties: { kind: 'summit' } }], { x: 0, y: 0 }, project)).toBeNull();
  });

  it('opens the line nearest the tap, not the first hit', () => {
    const far = tour('far', [[0, 10], [100, 10]]);
    const near = tour('near', [[0, 3], [100, 3]]);
    expect(core.nearestTour([far, near], { x: 50, y: 0 }, project)).toBe('near');
  });

  it('measures to the segment, not only to its points', () => {
    // 'span' passes 1 px from the tap between two far points; 'dot' has a
    // point 4 px away.
    const span = tour('span', [[-100, 1], [100, 1]]);
    const dot = tour('dot', [[4, 0], [40, 40]]);
    expect(core.nearestTour([dot, span], { x: 0, y: 0 }, project)).toBe('span');
  });

  it('reads a tour clipped into parts across tiles', () => {
    const parts = {
      geometry: { type: 'MultiLineString', coordinates: [[[0, 50], [10, 50]], [[20, 2], [30, 2]]] },
      properties: { kind: 'tour', uuid: 'm' },
    };
    const other = tour('o', [[0, 10], [40, 10]]);
    expect(core.nearestTour([other, parts], { x: 25, y: 0 }, project)).toBe('m');
  });

  it('opens a one-point part', () => {
    expect(core.nearestTour([tour('p', [[1, 1]])], { x: 0, y: 0 }, project)).toBe('p');
  });
});

describe('loadBox and covers', () => {
  const view = [10.07, 46.83, 10.13, 46.87];

  it('pads the view and rounds it outward to the pad', () => {
    const box = core.loadBox(view);
    expect(box).toEqual([10, 46.75, 10.2, 46.95]);
    expect(core.covers(box, view)).toBe(true);
  });

  it('asks the same rectangle after a small pan', () => {
    expect(core.loadBox([10.071, 46.831, 10.131, 46.871])).toEqual(core.loadBox(view));
  });

  it('loads nothing for a view wider than the endpoint answers', () => {
    expect(core.loadBox([9, 46, 10, 47])).toBeNull();
  });

  it('drops the padding before it drops a wide screen at z12', () => {
    // 2560 px at z12 spans about 0.44°: too wide once padded, not on its own.
    const wide = /** @type {[number, number, number, number]} */ ([9.8, 46.7, 10.24, 46.9]);
    const box = core.loadBox(wide);
    expect(box).not.toBeNull();
    expect(core.covers(box, wide)).toBe(true);
    expect(box[2] - box[0]).toBeLessThanOrEqual(0.5);
    // A view just under the limit still loads, to the metre.
    const edge = /** @type {[number, number, number, number]} */ ([9.8012, 46.7, 10.2995, 46.9]);
    expect(core.covers(core.loadBox(edge), edge)).toBe(true);
  });

  it('does not cover a view past its edge, or with nothing loaded', () => {
    expect(core.covers([10, 46.75, 10.2, 46.95], [10.15, 46.8, 10.25, 46.9])).toBe(false);
    expect(core.covers(null, view)).toBe(false);
  });
});

describe('URLs', () => {
  it('builds the feature URL, sampling only when asked', () => {
    const template = '/routes/ski-tours/__UUID__/feature.json';
    expect(core.featureUrl(template, 'a', false)).toBe('/routes/ski-tours/a/feature.json');
    expect(core.featureUrl(template, 'a', true)).toBe('/routes/ski-tours/a/feature.json?sample=1');
  });
});

describe('fitsView', () => {
  const size = { width: 400, height: 800 };
  const padding = { top: 60, right: 40, bottom: 350, left: 40 };
  /** The four corners of a screen box, as map.js projects them. */
  const box = (l, t, r, b) => [{ x: l, y: t }, { x: r, y: t }, { x: r, y: b }, { x: l, y: b }];

  it('says a tour clear of the panels already shows (SNOW-1284)', () => {
    expect(core.fitsView(box(50, 100, 300, 400), size, padding)).toBe(true);
  });

  it('says a tour under the sheet, or off an edge, does not', () => {
    expect(core.fitsView(box(50, 100, 300, 500), size, padding)).toBe(false);
    expect(core.fitsView(box(20, 100, 300, 400), size, padding)).toBe(false);
    expect(core.fitsView(box(50, 40, 300, 400), size, padding)).toBe(false);
    expect(core.fitsView(box(50, 100, 380, 400), size, padding)).toBe(false);
  });

  it('reads every corner, so a rotated map cannot hide one', () => {
    // North-west, north-east and south-west clear; south-east under the sheet.
    const rotated = [{ x: 200, y: 100 }, { x: 350, y: 250 }, { x: 200, y: 500 }, { x: 60, y: 300 }];
    expect(core.fitsView(rotated, size, padding)).toBe(false);
  });
});
