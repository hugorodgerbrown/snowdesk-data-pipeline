/*
 * tests/js/test_map_compass.js — the map's compass (SNOW-1449).
 *
 * Pinned: hidden while the map is north-up, shown once it turns, the
 * needle turned against the bearing on every rotate, and a press easing
 * back to north without touching the pitch, flat and in 3-D alike.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

/** A map that turns and tilts, and calls its rotate listeners on a turn. */
function fakeMap(bearing = 0, pitch = 0) {
  const listeners = {};
  const map = {
    bearing,
    pitch,
    on: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    getBearing: () => map.bearing,
    getPitch: () => map.pitch,
    easeTo: vi.fn((options) => {
      if ('bearing' in options) map.turn(options.bearing);
      if ('pitch' in options) map.pitch = options.pitch;
    }),
    turn: (to) => {
      map.bearing = to;
      for (const fn of listeners.rotate || []) fn();
    },
  };
  return map;
}

async function boot(map) {
  document.body.innerHTML = `
    <button id="map-compass" type="button" hidden>
      <svg data-compass-needle></svg>
    </button>`;
  window.snowdeskMapState = { map };
  vi.resetModules();
  await import('../../static/js/map_compass.js');
  return {
    button: document.getElementById('map-compass'),
    needle: document.querySelector('[data-compass-needle]'),
  };
}

describe('map_compass', () => {
  beforeEach(() => { document.body.innerHTML = ''; });

  it('is hidden while the map is north-up', async () => {
    const { button } = await boot(fakeMap(0));
    expect(button.hidden).toBe(true);
  });

  it('shows once the map turns, its needle against the bearing', async () => {
    const map = fakeMap(0);
    const { button, needle } = await boot(map);
    map.turn(40);
    expect(button.hidden).toBe(false);
    expect(needle.style.transform).toBe('rotate(-40deg)');
    map.turn(-120);
    expect(needle.style.transform).toBe('rotate(120deg)');
  });

  it('shows at boot on a map the stored camera turned', async () => {
    const { button } = await boot(fakeMap(215));
    expect(button.hidden).toBe(false);
  });

  it('eases back to north on a press and hides again, flat', async () => {
    const map = fakeMap(30, 0);
    const { button } = await boot(map);
    button.click();
    expect(map.easeTo).toHaveBeenCalledWith(expect.objectContaining({ bearing: 0 }));
    expect(map.easeTo.mock.calls[0][0]).not.toHaveProperty('pitch');
    expect(button.hidden).toBe(true);
  });

  it('keeps the tilt in 3-D', async () => {
    const map = fakeMap(215, 64);
    const { button, needle } = await boot(map);
    expect(needle.style.transform).toBe('rotate(-215deg)');
    button.click();
    expect(map.bearing).toBe(0);
    expect(map.pitch).toBe(64);
  });

  it('counts a hair off north as north-up', async () => {
    const map = fakeMap(0);
    const { button } = await boot(map);
    map.turn(0.2);
    expect(button.hidden).toBe(true);
  });
});
