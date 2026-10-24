/*
 * tests/js/test_route_profile.js — the route profile drawing shared by the
 * map's route panel, the Plan page and the trip page (SNOW-1215,
 * static/js/route_profile.js).
 *
 * The panel's own behaviour is tests/js/test_route_rail.js's; this file
 * holds the module to its contract on a page with no panel at all: read
 * a feature (JSON-encoded properties included), draw the lane and its
 * ticks, place and clear the cursor line — and draw the same thing
 * trip_route_profile.js draws from a page's payload.
 */

import { beforeAll, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/route_profile.js';

const STRINGS = {
  'unit-m': '%(value)s m',
  'unit-km': '%(value)s km',
  'lane-label': 'Elevation profile of %(name)s',
};

const MARKUP = `
  <div class="relative">
    <svg data-route-rail-lane></svg>
    <div data-route-rail-readout hidden>
      <span data-route-rail-dot></span>
      <span data-route-rail-elevation></span>
      <span data-route-rail-distance></span>
    </div>
  </div>
  <div data-route-rail-ticks></div>`;

/** @returns {object} A two-leg route, climbing then descending. */
function feature(overrides = {}) {
  const coordinates = Array.from({ length: 81 }, (_, i) => [
    7.4 + i / 10000,
    46.1,
    i <= 40 ? 1500 + i * 5 : 1700 - (i - 40) * 5,
  ]);
  return {
    type: 'Feature',
    geometry: { type: 'LineString', coordinates },
    properties: {
      distance_m: 620,
      slope: { points: [], angles: new Array(24).fill(20) },
      legs: [
        { i: 1, from: 0, to: 11, climbing: true },
        { i: 2, from: 12, to: 23, climbing: false },
      ],
      ...overrides,
    },
  };
}

/** @returns {HTMLElement} A fresh profile root. */
function root() {
  const el = document.createElement('div');
  el.innerHTML = MARKUP;
  document.body.appendChild(el);
  return el;
}

describe('read', () => {
  it('sizes the cursor from slope.angles, with the route length as the span', () => {
    const read = window.pwaRouteProfile.read(feature());
    expect(read.sampleCount).toBe(24);
    expect(read.legs).toHaveLength(2);
    expect(read.spanM).toBe(620);
  });

  it('reads JSON-encoded properties, as MapLibre hands them back', () => {
    const f = feature();
    f.properties.legs = JSON.stringify(f.properties.legs);
    f.properties.slope = JSON.stringify(f.properties.slope);
    expect(window.pwaRouteProfile.read(f).sampleCount).toBe(24);
  });

  it('sizes from the legs when the route was never sampled', () => {
    expect(window.pwaRouteProfile.read(feature({ slope: undefined })).sampleCount).toBe(24);
  });

  it('reads no legs at all as the outline alone', () => {
    const read = window.pwaRouteProfile.read(feature({ slope: undefined, legs: undefined }));
    expect(read.sampleCount).toBe(0);
    expect(read.legs).toEqual([]);
  });
});

describe('mount', () => {
  it('returns null for markup with no lane', () => {
    expect(window.pwaRouteProfile.mount(document.createElement('div'), STRINGS)).toBeNull();
  });

  it('draws one fill per leg, the outline, ticks and a label', () => {
    const el = root();
    const view = window.pwaRouteProfile.mount(el, STRINGS);
    const read = window.pwaRouteProfile.read(feature());
    view.draw(read.profile, read.legs, read.sampleCount, read.spanM, 'Mont Fort');

    const legs = el.querySelectorAll('.route-rail-leg');
    expect(Array.from(legs).map((leg) => leg.getAttribute('data-climbing')))
      .toEqual(['true', 'false']);
    expect(el.querySelector('path[fill="none"]')).not.toBeNull();
    expect(el.querySelector('[data-route-rail-ticks]').textContent).toContain('0 m');
    expect(view.lane.getAttribute('aria-label')).toBe('Elevation profile of Mont Fort');
  });

  it('places the cursor line and readout, and clears both', () => {
    const el = root();
    const view = window.pwaRouteProfile.mount(el, STRINGS);
    const read = window.pwaRouteProfile.read(feature());
    view.draw(read.profile, read.legs, read.sampleCount, read.spanM, 'Mont Fort');

    view.cursor(0);
    const line = el.querySelector('[data-route-rail-cursor]');
    expect(line).not.toBeNull();
    expect(Number(line.getAttribute('x1'))).toBeCloseTo((0.5 / 24) * 1000, 1);
    expect(el.querySelector('[data-route-rail-readout]').hidden).toBe(false);

    view.cursor(null);
    expect(el.querySelector('[data-route-rail-cursor]')).toBeNull();
    expect(el.querySelector('[data-route-rail-readout]').hidden).toBe(true);
  });

  it('draws no cursor on a route with no legs', () => {
    const el = root();
    const view = window.pwaRouteProfile.mount(el, STRINGS);
    const read = window.pwaRouteProfile.read(feature({ slope: undefined, legs: undefined }));
    view.draw(read.profile, read.legs, read.sampleCount, read.spanM, 'Flat');

    view.cursor(3);
    expect(el.querySelector('[data-route-rail-cursor]')).toBeNull();
    expect(el.querySelectorAll('.route-rail-leg')).toHaveLength(0);
  });
});

describe('the trip pages draw the same profile from a payload', () => {
  beforeAll(async () => {
    document.body.innerHTML = `
      <script type="application/json" id="payload">${JSON.stringify({ route: feature() })}</script>
      <div data-trip-route-profile data-payload="payload" data-name="Mont Fort">${MARKUP}</div>`;
    await import('../../static/js/trip_route_profile.js');
  });

  it('fills the lane named by data-payload', () => {
    const el = document.querySelector('[data-trip-route-profile]');
    expect(el.querySelectorAll('.route-rail-leg')).toHaveLength(2);
    expect(el.querySelector('svg').getAttribute('aria-label')).toBe('Elevation profile of Mont Fort');
  });
});
