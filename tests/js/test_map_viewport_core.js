/*
 * tests/js/test_map_viewport_core.js — the stored map camera and its guard.
 *
 * The assertion this file exists for is REJECTION. A stored viewport is not
 * data this build produced: it was written by whatever version the visitor
 * last ran, and the camera limits it was written against have moved before —
 * SNOW-442 took `maxZoom` from 12 to 18, and `maxBounds` east from 17° to
 * 23°. MapLibre absorbs a stale camera silently (it clamps the centre to
 * `maxBounds` and pins the zoom), so the failure mode is not a crash but a
 * map that opens somewhere the visitor has never been, with nothing anywhere
 * to explain it. Every `restore` returning null below is a case where that
 * silent clamp is what we are refusing.
 *
 * The round-trip is asserted too, but it is the cheap half — the interesting
 * behaviour is everything this rejects.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

// The map's real limits, as map.js declares them for the constructor. Copied
// deliberately rather than imported: this file is asserting that `restore`
// honours whatever limits it is handed, and reaching into map.js for them
// would couple the test to the boot IIFE it is specifically avoiding.
const LIMITS = {
  minZoom: 4,
  maxZoom: 18,
  maxBounds: [[0.9482, 41.9952], [19.6674, 49.9983]],
};

/** A camera inside every limit — Verbier, mid-zoom, unrotated. */
const VALID = { lng: 7.2286, lat: 46.0961, zoom: 11.5, bearing: 0, pitch: 0 };

beforeAll(async () => {
  await import('../../static/js/map_viewport_core.js');
  core = globalThis.pwaViewportCore;
});

describe('serialise / restore round trip', () => {
  it('returns the camera it was given, in MapLibre camera shape', () => {
    const restored = core.restore(core.serialise(VALID), LIMITS);
    expect(restored).toEqual({
      center: [VALID.lng, VALID.lat],
      zoom: VALID.zoom,
      bearing: VALID.bearing,
      pitch: VALID.pitch,
    });
  });

  it('keeps a non-zero bearing and pitch', () => {
    const rotated = { ...VALID, bearing: 47.5, pitch: 55 };
    const restored = core.restore(core.serialise(rotated), LIMITS);
    expect(restored.bearing).toBe(47.5);
    expect(restored.pitch).toBe(55);
  });

  it('serialises only the five camera fields', () => {
    // A caller passing a whole MapLibre camera object must not smuggle extra
    // state into the stored blob — it is read back by future builds.
    const parsed = JSON.parse(core.serialise({ ...VALID, extra: 'nope' }));
    expect(Object.keys(parsed).sort()).toEqual(
      ['bearing', 'lat', 'lng', 'pitch', 'zoom'],
    );
  });
});

describe('restore rejects a camera the current limits disallow', () => {
  it('rejects a zoom above maxZoom', () => {
    const raw = core.serialise({ ...VALID, zoom: 18.1 });
    expect(core.restore(raw, LIMITS)).toBeNull();
  });

  it('rejects a zoom below minZoom', () => {
    const raw = core.serialise({ ...VALID, zoom: 3.9 });
    expect(core.restore(raw, LIMITS)).toBeNull();
  });

  it('accepts a zoom exactly on each limit', () => {
    // The bound is inclusive: a camera sitting precisely at maxZoom is one
    // the map will accept, so rejecting it would discard a usable view.
    expect(core.restore(core.serialise({ ...VALID, zoom: 4 }), LIMITS))
      .not.toBeNull();
    expect(core.restore(core.serialise({ ...VALID, zoom: 18 }), LIMITS))
      .not.toBeNull();
  });

  it.each([
    ['west of maxBounds', { lng: 0.9, lat: 46.0961 }],
    ['east of maxBounds', { lng: 19.7, lat: 46.0961 }],
    ['south of maxBounds', { lng: 7.2286, lat: 41.9 }],
    ['north of maxBounds', { lng: 7.2286, lat: 50.1 }],
  ])('rejects a centre %s', (_label, centre) => {
    const raw = core.serialise({ ...VALID, ...centre });
    expect(core.restore(raw, LIMITS)).toBeNull();
  });

  it('honours widened limits rather than its own copy of them', () => {
    // The point of taking `limits` as an argument: a camera rejected under
    // today's numbers must be accepted if the map's numbers change.
    const raw = core.serialise({ ...VALID, zoom: 19.5 });
    expect(core.restore(raw, LIMITS)).toBeNull();
    expect(core.restore(raw, { ...LIMITS, maxZoom: 20 })).not.toBeNull();
  });
});

describe('restore rejects anything malformed', () => {
  it('returns null when the key is absent', () => {
    // readStorage hands back null for a missing key, and for a storage
    // access that threw (private mode) — both mean "no stored viewport".
    expect(core.restore(null, LIMITS)).toBeNull();
  });

  it('returns null for an empty string', () => {
    expect(core.restore('', LIMITS)).toBeNull();
  });

  it('returns null for malformed JSON', () => {
    expect(core.restore('{"lng": 7.2,', LIMITS)).toBeNull();
  });

  it.each([
    ['a JSON null', 'null'],
    ['a bare number', '42'],
    ['a bare string', '"somewhere"'],
    ['an array', '[7.2286, 46.0961]'],
  ])('returns null for %s', (_label, raw) => {
    expect(core.restore(raw, LIMITS)).toBeNull();
  });

  it.each(['lng', 'lat', 'zoom', 'bearing', 'pitch'])(
    'returns null when %s is missing',
    (field) => {
      const partial = { ...VALID };
      delete partial[field];
      expect(core.restore(JSON.stringify(partial), LIMITS)).toBeNull();
    },
  );

  it.each([
    ['NaN', JSON.stringify({ ...VALID, lng: 'NaN' })],
    ['a string', JSON.stringify({ ...VALID, zoom: '11.5' })],
    ['null', JSON.stringify({ ...VALID, lat: null })],
    ['Infinity', JSON.stringify({ ...VALID, pitch: null }).replace('null', '1e999')],
  ])('returns null when a field is %s', (_label, raw) => {
    expect(core.restore(raw, LIMITS)).toBeNull();
  });
});
