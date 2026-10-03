/*
 * tests/js/test_route_point_card.js — the point card's DOM half
 * (static/js/route_point_card.js, SNOW-1064; the route panel's point
 * header since SNOW-1068).
 *
 * The header is hidden until the route cursor has an index, shows and
 * reads the point when it does, hides again when the index clears (the
 * panel's route header returns) and on detach, and its wheel's
 * accessible name is the two lines it shows, read with commas for the
 * bullets (SNOW-1069). Pressing the wheel clears the point (2026-10-02).
 *
 * The markup below is the hooks of templates/includes/_route_point_card.html;
 * tests/public/test_route_point_card.py holds the partial to them.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/aspect_wheel_core.js';
import '../../static/js/route_point_card_core.js';

document.body.innerHTML = `
  <div id="map">
    <div id="route-point-card" data-route-point-card hidden>
      <button type="button" data-route-point-card-clear aria-label="Clear the point">
        <span data-route-point-card-wheel></span>
      </button>
      <p data-route-point-card-headline></p>
      <p data-route-point-card-ground></p>
    </div>
  </div>
`;

await import('../../static/js/route_point_card.js');

const card = document.getElementById('route-point-card');
const headline = card.querySelector('[data-route-point-card-headline]');
const ground = card.querySelector('[data-route-point-card-ground]');

/**
 * A straight path heading `deg` from `start`.
 *
 * @param {Array<number>} start `[lon, lat]`.
 * @param {number} deg The bearing.
 * @returns {Array<number>} The point 25 m on.
 */
function step(start, deg) {
  const rad = (deg * Math.PI) / 180;
  return [
    start[0] + (25 * Math.sin(rad)) / (111320 * Math.cos((start[1] * Math.PI) / 180)),
    start[1] + (25 * Math.cos(rad)) / 111320,
  ];
}

// Three segments heading east, dropping 15 m each (about 31°), across
// ground that falls east at 42°: a steep fall line descent.
const P0 = [7.2, 46];
const P1 = step(P0, 90);
const P2 = step(P1, 90);
const P3 = step(P2, 90);
const coordinates = [
  [...P0, 2000],
  [...P1, 1985],
  [...P2, 1970],
  [...P3, 1955],
];
const slope = {
  points: [P0, P1, P2, P3],
  angles: [42, 42, 3],
  aspects: [2, 2, null],
};

/** @returns {object} A fresh cursor over the three segments. */
function attach() {
  const cursor = self.pwaRouteCursorCore.createRouteCursor(3);
  const profile = self.pwaElevationProfileCore.readProfile(coordinates);
  window.pwaRoutePointCard.attach({
    cursor,
    slope,
    coordinates,
    profile,
    legs: [{ from: 0, to: 2, i: 1, climbing: false }],
    sampleCount: 3,
    spanM: profile.distanceM,
  });
  return cursor;
}

/** @returns {string} The wheel's accessible name. */
function wheelLabel() {
  return card.querySelector('svg[role="img"]')?.getAttribute('aria-label') || '';
}

describe('route_point_card.js', () => {
  beforeEach(() => {
    window.pwaRoutePointCard.detach();
  });

  it('stays hidden when a route is attached with no point', () => {
    attach();
    expect(card.hidden).toBe(true);
    expect(headline.textContent).toBe('');
    expect(ground.textContent).toBe('');
  });

  it('shows and reads the point when the cursor moves', () => {
    const cursor = attach();
    cursor.setIndex(1);
    expect(card.hidden).toBe(false);
    expect(headline.textContent).toBe('E • Steep • fall line');
    expect(ground.textContent).toBe('Extremely steep slope');
    expect(card.querySelector('[data-lit="faces"]')).not.toBeNull();
    cursor.setIndex(2);
    expect(ground.textContent).toBe('Flat ground');
  });

  it('names the wheel with the words it shows, read aloud', () => {
    const cursor = attach();
    cursor.setIndex(1);
    expect(wheelLabel()).toBe(`E, Steep, fall line; ${ground.textContent}`);
  });

  it('hides and empties when the index clears, for the route header', () => {
    const cursor = attach();
    cursor.setIndex(1);
    cursor.setIndex(null);
    expect(card.hidden).toBe(true);
    expect(headline.textContent).toBe('');
    expect(card.querySelector('svg')).toBeNull();
  });

  it('hides on detach and stops following the old cursor', () => {
    const cursor = attach();
    cursor.setIndex(1);
    window.pwaRoutePointCard.detach();
    expect(card.hidden).toBe(true);
    cursor.setIndex(0);
    expect(card.hidden).toBe(true);
    expect(headline.textContent).toBe('');
  });

  it('clears the point when the wheel is pressed, and keeps following', () => {
    const cursor = attach();
    cursor.setIndex(1);

    card.querySelector('[data-route-point-card-clear]').click();

    expect(cursor.state().index).toBeNull();
    expect(card.hidden).toBe(true);
    cursor.setIndex(2);
    expect(card.hidden).toBe(false);
  });

  it('does nothing on a wheel press once detached', () => {
    const cursor = attach();
    cursor.setIndex(1);
    window.pwaRoutePointCard.detach();

    card.querySelector('[data-route-point-card-clear]').click();

    expect(cursor.state().index).toBe(1);
  });
});
