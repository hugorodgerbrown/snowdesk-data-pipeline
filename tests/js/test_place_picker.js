/*
 * tests/js/test_place_picker.js — Vitest unit tests for
 * static/js/place_picker.js, covering SNOW-538's pin lift.
 *
 * The interesting behaviour is entirely geometric: how far the pin is
 * raised above the map's centre so a bottom-docked sheet can't cover it,
 * and whether the coordinate handed to onChange is the point under the pin
 * rather than the point at the middle of the map. jsdom implements no
 * layout, so both rectangles are stubbed per-test via
 * getBoundingClientRect — which is the honest shape of the test anyway,
 * since the whole point is to model sheets of different heights against the
 * same map.
 *
 * The fake map exposes only what the module touches — getContainer /
 * getCenter / setCenter / getPadding / setPadding / on / off — plus an
 * emit() so a test can fire the 'moveend' a real pan would, and an
 * ``unproject``-alike (``latAt``) the tests use to check where the pin's
 * screen point actually lands. It models MapLibre's padding faithfully,
 * since that is the mechanism under test: the centre point is offset by
 * half the padding imbalance, ``center`` is the LngLat *at that offset
 * point*, and setting padding re-anchors it there rather than changing it.
 * The projection is flat (SCALE pixels per degree, north-up) so expected
 * latitudes can be worked out by hand.
 *
 * The module reads the bare global `MAP` (declared at the top of
 * static/js/map.js in production), which resolves through the global scope
 * chain to `globalThis.MAP` here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Map container geometry: a phone-shaped viewport with a header above it. */
const MAP_RECT = { top: 100, left: 0, width: 400, height: 800 };

/** Pixels per degree in the fake projection — deliberately round. */
const SCALE = 10;

/** Where the fake map starts every test. */
const START = { lat: 46, lng: 7 };

/**
 * Stub an element's box. jsdom reports every rect as zero, so each test
 * states the geometry it is actually about.
 * @param {HTMLElement} el
 * @param {{top: number, left: number, width: number, height: number}} box
 */
function setRect(el, box) {
  el.getBoundingClientRect = () => ({
    top: box.top,
    left: box.left,
    width: box.width,
    height: box.height,
    right: box.left + box.width,
    bottom: box.top + box.height,
    x: box.left,
    y: box.top,
  });
}

/** Build a fake MapLibre map over a flat, north-up linear projection. */
function makeMap(container) {
  const map = {
    centre: { ...START },
    padding: { top: 0, right: 0, bottom: 0, left: 0 },
    listeners: {},
    getContainer: () => container,
    getCenter: () => ({ ...map.centre }),
    setCenter: (coord) => {
      map.centre = Array.isArray(coord)
        ? { lng: coord[0], lat: coord[1] }
        : { lng: coord.lng, lat: coord.lat };
      map.emit('moveend');
    },
    getPadding: () => ({ ...map.padding }),
    // Padding shifts the centre point by half its imbalance; `centre` is
    // the location at that point, and stays put across the change.
    setPadding: (padding) => {
      map.padding = { ...padding };
      map.emit('moveend');
    },
    /** y of the centre point, i.e. where the pin should be. */
    centrePointY: () =>
      MAP_RECT.height / 2 + (map.padding.top - map.padding.bottom) / 2,
    /** The latitude at a given y in container coordinates. */
    latAt: (y) => map.centre.lat - (y - map.centrePointY()) / SCALE,
    on: (event, handler) => {
      (map.listeners[event] = map.listeners[event] || []).push(handler);
    },
    off: (event, handler) => {
      const handlers = map.listeners[event] || [];
      const index = handlers.indexOf(handler);
      if (index !== -1) handlers.splice(index, 1);
    },
    emit: (event) => {
      (map.listeners[event] || []).slice().forEach((handler) => handler());
    },
  };
  map.scrollZoom = makeZoomHandler({ ignoresOptionsWhenEnabled: true });
  map.touchZoomRotate = makeZoomHandler({ ignoresOptionsWhenEnabled: false });
  map.doubleClickZoom = makeZoomHandler({ ignoresOptionsWhenEnabled: false });
  return map;
}

/**
 * A stand-in for one of MapLibre's zoom handlers, tracking whether it is
 * enabled and whether it was armed to zoom about the map's centre.
 *
 * ``ignoresOptionsWhenEnabled`` models ScrollZoomHandler's quirk — its
 * ``enable()`` returns early when the handler is already enabled, so an
 * ``enable({around: 'center'})`` that isn't preceded by ``disable()``
 * silently does nothing. Modelling it is what makes these tests able to
 * catch that mistake.
 *
 * @param {{ignoresOptionsWhenEnabled: boolean}} behaviour
 */
function makeZoomHandler(behaviour) {
  const handler = {
    enabled: true,
    aroundCentre: false,
    isEnabled: () => handler.enabled,
    disable: () => {
      handler.enabled = false;
    },
    enable: (options) => {
      if (behaviour.ignoresOptionsWhenEnabled && handler.enabled) return;
      handler.enabled = true;
      handler.aroundCentre = !!options && options.around === 'center';
    },
  };
  return handler;
}

/** Re-run the module's IIFE against fresh state and return the fresh API. */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/place_picker.js');
  return window.PlacePicker;
}

/** The lift the module has written onto the pin, in pixels. */
function liftPx() {
  return parseFloat(pin.style.getPropertyValue('--map-place-pin-lift')) || 0;
}

let container;
let pin;
let sheet;
let map;
let picker;

beforeEach(async () => {
  document.body.innerHTML =
    '<div id="map"><div id="map-place-pin" hidden></div></div>' +
    '<div id="test-sheet"></div>';
  container = document.getElementById('map');
  pin = document.getElementById('map-place-pin');
  sheet = document.getElementById('test-sheet');
  setRect(container, MAP_RECT);
  map = makeMap(container);
  globalThis.MAP = map;
  picker = await loadModule();
});

afterEach(() => {
  // Leave no module instance armed — its still-registered window resize
  // listener would otherwise fire against the next test's map.
  picker.deactivate();
  delete globalThis.MAP;
});

/** Bottom-docked sheet covering the lower `height` px of the map — the
 * phone case, where the sheet spans the full width. */
function dockSheet(height) {
  const top = MAP_RECT.top + MAP_RECT.height - height;
  setRect(sheet, { top, left: MAP_RECT.left, width: MAP_RECT.width, height });
}

describe('activate() pin lift', () => {
  it('leaves the pin centred when no occluder is given', () => {
    picker.activate({ onChange: () => {} });

    expect(liftPx()).toBe(0);
  });

  it('leaves the pin centred when the sheet clears the map centre', () => {
    // 300px of an 800px map — its top edge (500) is below the centre (400).
    dockSheet(300);

    picker.activate({ onChange: () => {}, occludedBy: sheet });

    expect(liftPx()).toBe(0);
  });

  it('lifts the pin to the middle of the strip left above a tall sheet', () => {
    // 500px of an 800px map: 300px visible above it, so the pin belongs at
    // y=150 — 250px above the centre.
    dockSheet(500);

    picker.activate({ onChange: () => {}, occludedBy: sheet });

    expect(liftPx()).toBe(250);
  });

  it('leaves the pin centred for a sheet that does not span the centre', () => {
    // The wide-viewport case: a bottom-right card that never reaches the
    // horizontal middle of the map.
    setRect(sheet, { top: MAP_RECT.top + 300, left: 320, width: 80, height: 500 });

    picker.activate({ onChange: () => {}, occludedBy: sheet });

    expect(liftPx()).toBe(0);
  });

  it('ignores a hidden sheet', () => {
    dockSheet(500);
    sheet.setAttribute('hidden', '');

    picker.activate({ onChange: () => {}, occludedBy: sheet });

    expect(liftPx()).toBe(0);
  });
});

describe('reported coordinate', () => {
  it('is the map centre when the pin is not lifted', () => {
    const seen = [];

    picker.activate({ onChange: (lat, lon) => seen.push([lat, lon]) });

    expect(seen).toEqual([[START.lat, START.lng]]);
    expect(map.latAt(MAP_RECT.height / 2)).toBe(START.lat);
  });

  it('is the point under the lifted pin, not the middle of the map', () => {
    dockSheet(500);
    const seen = [];

    picker.activate({ onChange: (lat, lon) => seen.push([lat, lon]), occludedBy: sheet });

    // The pin sits 250px above the middle of the map; the coordinate it
    // reports is the one at *its* y, and the middle of the map is 25
    // degrees (250px at 10px/degree) south of it.
    const [lat] = seen[seen.length - 1];
    expect(lat).toBe(map.latAt(MAP_RECT.height / 2 - 250));
    expect(map.latAt(MAP_RECT.height / 2)).toBe(lat - 25);
  });

  it('is re-read on every moveend', () => {
    const seen = [];
    picker.activate({ onChange: (lat, lon) => seen.push([lat, lon]) });

    map.centre = { lat: 47, lng: 8 };
    map.emit('moveend');

    expect(seen).toEqual([
      [START.lat, START.lng],
      [47, 8],
    ]);
  });

  it('stops being re-read after deactivate()', () => {
    const seen = [];
    picker.activate({ onChange: (lat, lon) => seen.push([lat, lon]) });
    picker.deactivate();

    map.centre = { lat: 47, lng: 8 };
    map.emit('moveend');

    expect(seen).toHaveLength(1);
  });
});

describe('recenterTo', () => {
  it('puts the requested coordinate under the lifted pin', () => {
    dockSheet(500);
    const seen = [];

    picker.activate({
      recenterTo: [7.5, 46.5],
      occludedBy: sheet,
      onChange: (lat, lon) => seen.push([lat, lon]),
    });

    // The fix lands under the pin, not at the middle of the map — which is
    // now showing somewhere 25 degrees to its south.
    expect(seen[seen.length - 1]).toEqual([46.5, 7.5]);
    expect(map.latAt(MAP_RECT.height / 2 - 250)).toBe(46.5);
    expect(map.latAt(MAP_RECT.height / 2)).toBe(46.5 - 25);
  });

  it('centres on the coordinate when the pin is not lifted', () => {
    const seen = [];

    picker.activate({
      recenterTo: [7.5, 46.5],
      onChange: (lat, lon) => seen.push([lat, lon]),
    });

    expect(seen[seen.length - 1]).toEqual([46.5, 7.5]);
    expect(map.latAt(MAP_RECT.height / 2)).toBe(46.5);
  });
});

describe('layout changes', () => {
  it('re-lifts the pin and holds the chosen point when the sheet grows', () => {
    dockSheet(300);
    const seen = [];
    picker.activate({ onChange: (lat, lon) => seen.push([lat, lon]), occludedBy: sheet });
    expect(liftPx()).toBe(0);

    // The sheet swaps in taller content — e.g. the report form replacing
    // "Finding your location…".
    dockSheet(500);
    window.dispatchEvent(new Event('resize'));

    expect(liftPx()).toBe(250);
    // The point the user had already chosen is still under the pin — the
    // map slid down under it rather than the pin re-picking a new point.
    expect(seen[seen.length - 1]).toEqual([START.lat, START.lng]);
    expect(map.latAt(MAP_RECT.height / 2 - 250)).toBe(START.lat);
  });
});

describe('deactivate()', () => {
  it('clears the lift so the next placement starts centred', () => {
    dockSheet(500);
    picker.activate({ onChange: () => {}, occludedBy: sheet });

    picker.deactivate();

    expect(liftPx()).toBe(0);
    expect(pin.hasAttribute('hidden')).toBe(true);
  });

  it('restores the padding the map had before the placement', () => {
    const before = { top: 8, right: 8, bottom: 40, left: 8 };
    map.padding = { ...before };
    dockSheet(500);
    picker.activate({ onChange: () => {}, occludedBy: sheet });
    expect(map.getPadding().bottom).toBe(40 + 500);

    picker.deactivate();

    expect(map.getPadding()).toEqual(before);
  });

  it('is safe to call having never activated', () => {
    expect(() => picker.deactivate()).not.toThrow();
    expect(picker.isActive()).toBe(false);
  });
});

describe('zoom anchoring', () => {
  it('anchors wheel and pinch zoom on the map centre while armed', () => {
    picker.activate({ onChange: () => {} });

    // The centre is where the pin is drawn (padding included), so a zoom
    // anchored there cannot move the coordinate under it.
    expect(map.scrollZoom.aroundCentre).toBe(true);
    expect(map.touchZoomRotate.aroundCentre).toBe(true);
  });

  it('turns off double-click zoom, which cannot be anchored', () => {
    picker.activate({ onChange: () => {} });

    expect(map.doubleClickZoom.isEnabled()).toBe(false);
  });

  it('restores every zoom handler on deactivate', () => {
    picker.activate({ onChange: () => {} });

    picker.deactivate();

    expect(map.scrollZoom.isEnabled()).toBe(true);
    expect(map.scrollZoom.aroundCentre).toBe(false);
    expect(map.touchZoomRotate.isEnabled()).toBe(true);
    expect(map.touchZoomRotate.aroundCentre).toBe(false);
    expect(map.doubleClickZoom.isEnabled()).toBe(true);
  });

  it('leaves a handler the app had already disabled alone', () => {
    map.scrollZoom.disable();

    picker.activate({ onChange: () => {} });
    expect(map.scrollZoom.isEnabled()).toBe(false);

    picker.deactivate();
    expect(map.scrollZoom.isEnabled()).toBe(false);
  });

  it('tolerates a map with no zoom handlers at all', () => {
    delete map.scrollZoom;
    delete map.touchZoomRotate;
    delete map.doubleClickZoom;

    expect(() => picker.activate({ onChange: () => {} })).not.toThrow();
    expect(() => picker.deactivate()).not.toThrow();
  });
});
