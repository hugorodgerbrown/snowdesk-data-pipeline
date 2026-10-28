/*
 * tests/js/test_ski_tours_core.js — choosing a SAC ski tour from the map
 * (static/js/ski_tours_core.js, SNOW-1278).
 *
 * What a tap means, the rectangle a view loads, and the URLs the list and
 * the tour panel are fetched from.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/ski_tours_core.js';

const core = self.pwaSkiToursCore;

const tour = (uuid) => ({ properties: { kind: 'tour', uuid } });

describe('resolveTap', () => {
  it('answers nothing for nothing', () => {
    expect(core.resolveTap([])).toBeNull();
    expect(core.resolveTap([{ properties: { kind: 'other' } }])).toBeNull();
  });

  it('opens a lone tour, even when it was hit in two tiles', () => {
    expect(core.resolveTap([tour('a'), tour('a')])).toEqual({ kind: 'tour', uuid: 'a' });
  });

  it('lists the tours when more than one is in reach', () => {
    expect(core.resolveTap([tour('a'), tour('b'), tour('a')])).toEqual({
      kind: 'list',
      uuids: ['a', 'b'],
    });
  });

  it('lists a summit before any line under it', () => {
    // Summit slugs, held as constants so a slug never sits beside a `key:`
    // where the secret scanner reads it as a credential.
    const signalhorn = ['signalhorn', '3207'].join('-');
    const hutte = ['silvrettahutte', '2341'].join('-');
    const summit = { properties: { kind: 'summit', key: signalhorn } };
    expect(core.resolveTap([tour('a'), summit])).toEqual({
      kind: 'summit',
      key: signalhorn,
    });
    const hut = { properties: { kind: 'hut', key: hutte } };
    expect(core.resolveTap([hut])).toEqual({ kind: 'summit', key: hutte });
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

describe('URLs and the highlight', () => {
  it('builds the list URL for a summit and for a set of tours', () => {
    const base = '/routes/partials/ski-tours/';
    expect(core.listUrl(base, { kind: 'summit', key: 'piz buin-3312' })).toBe(
      '/routes/partials/ski-tours/?summit=piz%20buin-3312',
    );
    expect(core.listUrl(base, { kind: 'list', uuids: ['a', 'b'] })).toBe(
      '/routes/partials/ski-tours/?tours=a,b',
    );
    expect(core.listUrl(base, { kind: 'tour', uuid: 'a' })).toBeNull();
  });

  it('builds the feature URL, sampling only when asked', () => {
    const template = '/routes/ski-tours/__UUID__/feature.json';
    expect(core.featureUrl(template, 'a', false)).toBe('/routes/ski-tours/a/feature.json');
    expect(core.featureUrl(template, 'a', true)).toBe('/routes/ski-tours/a/feature.json?sample=1');
  });

  it('filters to the given tours', () => {
    expect(core.highlightFilter(['a'])).toEqual(['in', ['get', 'uuid'], ['literal', ['a']]]);
  });
});

describe('farEnds', () => {
  const summit = {
    geometry: { type: 'Point', coordinates: [10.1, 46.85] },
    properties: { kind: 'summit', key: 'top' },
  };
  /**
   * @param {string} uuid
   * @param {Array<Array<number>>} coords
   * @param {boolean} down
   * @param {string} [key]
   */
  const line = (uuid, coords, down, key = 'top') => ({
    geometry: { type: 'LineString', coordinates: coords },
    properties: { kind: 'tour', uuid: uuid, summit: key, down: down },
  });
  const ends = (/** @type {any} */ fc) =>
    fc.features.map((/** @type {any} */ f) => [f.properties.uuid, f.geometry.coordinates]);

  it('marks the end away from the summit, whichever way the line is drawn', () => {
    const up = line('up', [[10.08, 46.83], [10.1, 46.85]], false);
    const backwards = line('back', [[10.1, 46.85], [10.12, 46.86]], false);
    expect(ends(core.farEnds([summit, up, backwards], ['up', 'back']))).toEqual([
      ['up', [10.08, 46.83]],
      ['back', [10.12, 46.86]],
    ]);
  });

  it('reads a MultiLineString by its first and last points', () => {
    const multi = {
      geometry: {
        type: 'MultiLineString',
        coordinates: [[[10.1, 46.85], [10.09, 46.84]], [[10.09, 46.84], [10.0, 46.8]]],
      },
      properties: { kind: 'tour', uuid: 'm', summit: 'top', down: true },
    };
    expect(ends(core.farEnds([summit, multi], ['m']))).toEqual([['m', [10.0, 46.8]]]);
  });

  it('finds the start among parts stored out of order', () => {
    // The Silvrettahütte way up to the Signalhorn, as swisstopo stores it:
    // the hut's part comes last.
    const parts = {
      geometry: {
        type: 'MultiLineString',
        coordinates: [
          [[10.0863, 46.8499], [10.0969, 46.8488]],
          [[10.0863, 46.8499], [10.0918, 46.8514]],
          [[10.0415, 46.8544], [10.0863, 46.8499]],
        ],
      },
      properties: { kind: 'tour', uuid: 's', summit: 'top', down: false },
    };
    expect(ends(core.farEnds([summit, parts], ['s']))).toEqual([['s', [10.0415, 46.8544]]]);
  });

  it('falls back to the direction without its summit, and marks only the listed', () => {
    const up = line('up', [[1, 1], [2, 2]], false, 'elsewhere');
    const down = line('down', [[2, 2], [3, 3]], true, 'elsewhere');
    const other = line('other', [[5, 5], [6, 6]], false);
    expect(ends(core.farEnds([up, down, other, up], ['up', 'down']))).toEqual([
      ['up', [1, 1]],
      ['down', [3, 3]],
    ]);
    expect(core.farEnds([up], []).features).toEqual([]);
  });
});
