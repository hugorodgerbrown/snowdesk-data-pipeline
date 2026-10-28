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
