/*
 * tests/js/test_meeting_place_core.js — the meeting point's sentence, in
 * the browser (SNOW-1214).
 *
 * Reads the same tests/fixtures/meeting_place_vectors.json as
 * tests/trips/test_meeting_place.py, so the Plan page's live sentence and
 * the trip page's rendered one cannot describe one pin two ways.
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import '../../static/js/meeting_place_core.js';

const core = self.pwaMeetingPlaceCore;
const VECTORS = JSON.parse(readFileSync('tests/fixtures/meeting_place_vectors.json', 'utf8'));

describe('describe', () => {
  for (const vector of VECTORS.vectors) {
    it(vector.name, () => {
      const [lon, lat] = vector.pin;
      expect(core.describe(VECTORS.tracks[vector.track], lat, lon)).toEqual(vector.expected);
    });
  }
});

describe('distanceLabel', () => {
  for (const [metres, label] of VECTORS.labels) {
    it(`words ${metres} m as ${label}`, () => {
      expect(core.distanceLabel(metres)).toBe(label);
    });
  }
});

describe('sentence', () => {
  const strings = {
    start: "At the route's start",
    on_route: 'On the route, {distance} in',
    from_start: "{distance} {direction} of the route's start",
    'octant-NE': 'north-east',
  };

  it('fills the distance and the direction', () => {
    expect(core.sentence({ kind: 'from_start', distance: '2 km', octant: 'NE' }, strings))
      .toBe("2 km north-east of the route's start");
    expect(core.sentence({ kind: 'on_route', distance: '9 km', octant: '' }, strings))
      .toBe('On the route, 9 km in');
  });

  it('says nothing for no place', () => {
    expect(core.sentence(null, strings)).toBe('');
  });
});
