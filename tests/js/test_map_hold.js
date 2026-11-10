/*
 * tests/js/test_map_hold.js — Vitest DOM tests for static/js/map_hold.js,
 * the binding behind "hold to place" (SNOW-1485).
 *
 * static/js/map_hold_core.js decides whether a hold happened and has its own
 * file (test_map_hold_core.js). This file drives the binding through a fake
 * map, with the real modules it leans on: map_sheet.js (the bar's open and
 * its three ways to close), map_overlay_exclusivity.js (another overlay
 * opening over it), overlays.js (the × as every sheet's ×) and
 * place_picker.js (the placing itself). PlacementFocus is a stub that counts
 * real transitions: place_picker.js calls its idempotent exit() on every
 * deactivate, wanted or not, so the count worth pinning is how many times
 * the map was actually cleared down and given back.
 *
 * The fake map exposes what the three modules touch, over a flat north-up
 * projection (SCALE pixels per degree, the container's centre at the map's
 * centre) so a held point's lng/lat can be worked out by hand, and an
 * emit() so a test can fire the touches and gestures MapLibre would.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the held point is unprojected and handed to PlacePicker as
 *     recenterTo, with the bar as occludedBy;
 *   - a hold works on an object layer, and the click the lifted finger
 *     sends is swallowed once, so the object's sheet does not open; a plain
 *     tap still reaches the map;
 *   - ×, Escape, a click outside and another overlay opening each restore
 *     the camera and leave the placement once;
 *   - Save pin and Report hand the picker's latest lat/lon, in that order,
 *     to the right bridge, without restoring the camera, and the map is
 *     cleared down and given back exactly once across the hand-off;
 *   - a right-click is a hold, and the contextmenu a long touch sends is
 *     not a second one;
 *   - a hold while a pin is already being placed is ignored.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/** Pixels per degree in the fake projection. */
const SCALE = 100;
const WIDTH = 400;
const HEIGHT = 800;
/** The camera every test starts from. */
const START = { center: { lng: 7, lat: 46 }, zoom: 12, bearing: 10, pitch: 20 };

document.body.innerHTML = `
  <div id="map"><div id="canvas-container"><canvas id="canvas"></canvas></div></div>
  <div id="map-place-pin" hidden></div>
  <div id="map-hold-bar" hidden tabindex="-1" data-overlay></div>
  <template id="map-hold-bar-template">
    <div>
      <span>Move the map to fine-tune.</span>
      <button type="button" data-action="dismiss" aria-label="Close">x</button>
      <button type="button" data-hold-action="save-pin">Save pin</button>
      <button type="button" data-hold-action="report">Report</button>
    </div>
  </template>
  <div id="other-sheet" hidden tabindex="-1" data-overlay></div>
  <button type="button" id="outside">Somewhere else on the page</button>
`;

const container = document.getElementById('map');
// jsdom lays nothing out: give the map's container the fake map's size.
Object.defineProperty(container, 'clientWidth', { configurable: true, value: WIDTH });
Object.defineProperty(container, 'clientHeight', { configurable: true, value: HEIGHT });
const canvas = document.getElementById('canvas');
const bar = document.getElementById('map-hold-bar');

/** Build the fake MapLibre map over a flat, north-up projection. */
function makeMap() {
  const map = {
    camera: { ...START, center: { ...START.center } },
    padding: { top: 0, right: 0, bottom: 0, left: 0 },
    listeners: {},
    /** Where the padded viewport's middle sits from the container's, in px. */
    paddedOffset: () => ({
      x: ((map.padding.left || 0) - (map.padding.right || 0)) / 2,
      y: ((map.padding.top || 0) - (map.padding.bottom || 0)) / 2,
    }),
    /** Put (lng, lat) in the middle of the padded viewport, as MapLibre does. */
    centreOnPadded: (lng, lat) => {
      const d = map.paddedOffset();
      map.camera.center = { lng: lng - d.x / SCALE, lat: lat + d.y / SCALE };
    },
    jumpTo: vi.fn((options) => {
      if (options.center) {
        const [lng, lat] = options.center;
        map.centreOnPadded(lng, lat);
      }
      for (const key of ['zoom', 'bearing', 'pitch']) {
        if (options[key] != null) map.camera[key] = options[key];
      }
    }),
    getContainer: () => container,
    getCenter: () => {
      const d = map.paddedOffset();
      return map.unproject([WIDTH / 2 + d.x, HEIGHT / 2 + d.y]);
    },
    setCenter: (coord) => {
      map.centreOnPadded(coord[0], coord[1]);
      map.emit('moveend');
    },
    panBy: vi.fn(([x, y]) => {
      map.camera.center = {
        lng: map.camera.center.lng + x / SCALE,
        lat: map.camera.center.lat - y / SCALE,
      };
    }),
    getZoom: () => map.camera.zoom,
    getBearing: () => map.camera.bearing,
    getPitch: () => map.camera.pitch,
    getPadding: () => ({ ...map.padding }),
    setPadding: (padding) => {
      map.padding = { ...padding };
    },
    unproject: ([x, y]) => ({
      lng: map.camera.center.lng + (x - WIDTH / 2) / SCALE,
      lat: map.camera.center.lat - (y - HEIGHT / 2) / SCALE,
    }),
    /** Pan so the centre moves by (dLng, dLat), as a drag would. */
    pan: (dLng, dLat) => {
      map.camera.center = {
        lng: map.camera.center.lng + dLng,
        lat: map.camera.center.lat + dLat,
      };
      map.emit('moveend');
    },
    on: (event, handler) => {
      (map.listeners[event] = map.listeners[event] || []).push(handler);
    },
    off: (event, handler) => {
      const handlers = map.listeners[event] || [];
      const index = handlers.indexOf(handler);
      if (index !== -1) handlers.splice(index, 1);
    },
    emit: (event, payload) => {
      (map.listeners[event] || []).slice().forEach((handler) => handler(payload));
    },
  };
  return map;
}

vi.useFakeTimers();

const map = makeMap();
// place_picker.js and map_sheet.js read the bare global; map_hold.js reads
// the named channel.
globalThis.MAP = map;
window.snowdeskMapState = { map };

const focus = { enters: 0, exits: 0, active: false };
window.PlacementFocus = {
  enter: () => {
    if (focus.active) return;
    focus.active = true;
    focus.enters += 1;
  },
  exit: () => {
    if (!focus.active) return;
    focus.active = false;
    focus.exits += 1;
  },
  isActive: () => focus.active,
};

await import('../../static/js/overlays.js');
await import('../../static/js/map_overlay_exclusivity.js');
await import('../../static/js/map_sheet.js');
await import('../../static/js/place_picker.js');
await import('../../static/js/map_hold_core.js');
await import('../../static/js/map_hold.js');

const other = window.MapSheet.attach(document.getElementById('other-sheet'), {});

/** A MapLibre-shaped touch event with the given points. */
function touch(points) {
  return { points, originalEvent: {} };
}

/** Hold a finger still at (x, y) for the hold's half second. */
function hold(x, y) {
  map.emit('touchstart', touch([{ x, y }]));
  vi.advanceTimersByTime(500);
}

/** Lift the finger, and send the click a lifted finger sends. */
function lift() {
  map.emit('touchend', touch([]));
  canvas.click();
}

let objectClicks;
const onCanvasClick = () => objectClicks.push(true);

beforeEach(() => {
  map.camera = { ...START, center: { ...START.center } };
  map.padding = { top: 0, right: 0, bottom: 0, left: 0 };
  map.jumpTo.mockClear();
  map.panBy.mockClear();
  focus.enters = 0;
  focus.exits = 0;
  objectClicks = [];
  // Stands in for MapLibre's own click handler on the canvas container,
  // which is what opens an object's sheet.
  canvas.addEventListener('click', onCanvasClick);
  window.pwaFavouritesSheet = { createAt: vi.fn() };
  window.pwaReportSheet = { reportAt: vi.fn() };
});

afterEach(() => {
  // Leave no hold open for the next test, through the bar's own exit.
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  other.close();
  canvas.removeEventListener('click', onCanvasClick);
  // Past the swallow and the synthetic-contextmenu windows. One fake clock
  // for the whole file: a fresh one per test would start behind the last
  // test's advanced time, and the core's windows would read it as recent.
  vi.advanceTimersByTime(2000);
  delete window.pwaFavouritesSheet;
  delete window.pwaReportSheet;
});

describe('a hold', () => {
  it('unprojects the held point and arms the picker on it, clear of the bar', () => {
    const activate = vi.spyOn(window.PlacePicker, 'activate');

    hold(300, 200);

    // 100 px right and 200 px up of centre, at 100 px a degree.
    expect(activate).toHaveBeenCalledOnce();
    const options = activate.mock.calls[0][0];
    expect(options.recenterTo).toEqual([8, 48]);
    expect(options.occludedBy).toBe(bar);
    expect(bar.hidden).toBe(false);
    expect(bar.textContent).toContain('Move the map to fine-tune.');
    expect(window.PlacePicker.isActive()).toBe(true);
    expect(map.camera.center).toEqual({ lng: 8, lat: 48 });
    activate.mockRestore();
  });

  it('does nothing before the half second', () => {
    map.emit('touchstart', touch([{ x: 300, y: 200 }]));
    vi.advanceTimersByTime(499);
    expect(bar.hidden).toBe(true);
    map.emit('touchend', touch([]));
  });

  it.each(['dragstart', 'zoomstart'])('gives way to a %s', (gesture) => {
    map.emit('touchstart', touch([{ x: 300, y: 200 }]));
    vi.advanceTimersByTime(200);
    map.emit(gesture);
    vi.advanceTimersByTime(500);
    expect(bar.hidden).toBe(true);
    map.emit('touchend', touch([]));
  });

  it('is ignored while a pin is already being placed', () => {
    window.PlacePicker.activate({ onChange: () => {} });
    const activate = vi.spyOn(window.PlacePicker, 'activate');

    hold(300, 200);

    expect(activate).not.toHaveBeenCalled();
    expect(bar.hidden).toBe(true);
    activate.mockRestore();
    window.PlacePicker.deactivate();
  });
});

describe('a hold on an object', () => {
  it('arms, and the click the lifted finger sends is swallowed once', () => {
    hold(300, 200);
    lift();

    expect(bar.hidden).toBe(false);
    expect(objectClicks).toHaveLength(0);
    // The next tap is the reader's own, and reaches the map.
    canvas.click();
    expect(objectClicks).toHaveLength(1);
  });

  it('leaves a plain tap alone', () => {
    map.emit('touchstart', touch([{ x: 300, y: 200 }]));
    vi.advanceTimersByTime(100);
    lift();

    expect(bar.hidden).toBe(true);
    expect(objectClicks).toHaveLength(1);
  });
});

describe('a right-click', () => {
  it('is a hold, at once', () => {
    map.emit('contextmenu', { point: { x: 100, y: 400 }, originalEvent: { preventDefault: vi.fn() } });

    expect(bar.hidden).toBe(false);
    expect(map.camera.center).toEqual({ lng: 6, lat: 46 });
  });

  it("is not a second hold when it is the long touch's own contextmenu", () => {
    const activate = vi.spyOn(window.PlacePicker, 'activate');
    hold(300, 200);
    map.emit('contextmenu', { point: { x: 300, y: 200 }, originalEvent: { preventDefault: vi.fn() } });
    map.emit('touchend', touch([]));
    map.emit('contextmenu', { point: { x: 300, y: 200 }, originalEvent: { preventDefault: vi.fn() } });

    expect(activate).toHaveBeenCalledOnce();
    activate.mockRestore();
  });
});

describe('cancelling puts the map back as it was', () => {
  /** Hold, fine-tune with a pan, and return the deactivate spy. */
  function holdAndPan() {
    const deactivate = vi.spyOn(window.PlacePicker, 'deactivate');
    hold(300, 200);
    map.pan(0.5, -0.25);
    deactivate.mockClear();
    return deactivate;
  }

  /** The camera restore every cancel makes. */
  function expectRestored(deactivate) {
    expect(bar.hidden).toBe(true);
    expect(window.PlacePicker.isActive()).toBe(false);
    expect(map.jumpTo).toHaveBeenCalledOnce();
    expect(map.jumpTo).toHaveBeenCalledWith({
      center: [7, 46],
      zoom: 12,
      bearing: 10,
      pitch: 20,
    });
    expect(map.camera.center).toEqual({ lng: 7, lat: 46 });
    expect(deactivate).toHaveBeenCalledOnce();
    expect(focus.enters).toBe(1);
    expect(focus.exits).toBe(1);
    deactivate.mockRestore();
  }

  it('on the ×', () => {
    const deactivate = holdAndPan();
    bar.querySelector('[data-action="dismiss"]').click();
    expectRestored(deactivate);
  });

  it('on Escape', () => {
    const deactivate = holdAndPan();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expectRestored(deactivate);
  });

  it('on a click outside the bar and the map', () => {
    const deactivate = holdAndPan();
    document.getElementById('outside').click();
    expectRestored(deactivate);
  });

  it('when another overlay opens over it', () => {
    const deactivate = holdAndPan();
    other.open();
    expectRestored(deactivate);
  });

  it('when a sheet that padded the map closed as the bar opened', () => {
    // A detent sheet padded the map's bottom; the bar's opening closes it,
    // and its teardown drops the padding before the picker arms.
    map.padding = { top: 0, right: 0, bottom: 300, left: 0 };
    const activate = window.PlacePicker.activate;
    const spy = vi.spyOn(window.PlacePicker, 'activate').mockImplementation((options) => {
      map.padding = { top: 0, right: 0, bottom: 0, left: 0 };
      return activate.call(window.PlacePicker, options);
    });
    hold(300, 200);
    map.pan(0.5, -0.25);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    spy.mockRestore();
    // The container's middle is back on the same ground, not half a sheet off.
    expect(map.camera.center.lng).toBeCloseTo(START.center.lng, 9);
    expect(map.camera.center.lat).toBeCloseTo(START.center.lat, 9);
  });

  it('under padding the map still carries, with an instant pan', () => {
    map.padding = { top: 0, right: 0, bottom: 300, left: 0 };
    hold(300, 200);
    map.pan(0.5, -0.25);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(map.panBy).toHaveBeenCalledWith([0, -150], { duration: 0 });
    expect(map.camera.center.lng).toBeCloseTo(START.center.lng, 9);
    expect(map.camera.center.lat).toBeCloseTo(START.center.lat, 9);
  });

  it('and a second close does nothing more', () => {
    const deactivate = holdAndPan();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    other.open();
    expectRestored(deactivate);
  });
});

describe('Save pin and Report hand over the fine-tuned point', () => {
  it.each([
    ['save-pin', () => window.pwaFavouritesSheet.createAt, () => window.pwaReportSheet.reportAt],
    ['report', () => window.pwaReportSheet.reportAt, () => window.pwaFavouritesSheet.createAt],
  ])('%s calls its bridge with lat then lon', (action, bridge, otherBridge) => {
    hold(300, 200);
    map.pan(0.5, -0.25);

    bar.querySelector(`[data-hold-action="${action}"]`).click();

    expect(bridge()).toHaveBeenCalledOnce();
    expect(bridge()).toHaveBeenCalledWith(47.75, 8.5);
    expect(otherBridge()).not.toHaveBeenCalled();
    expect(bar.hidden).toBe(true);
  });

  it.each(['save-pin', 'report'])('%s leaves the camera where the reader put it', (action) => {
    hold(300, 200);
    map.pan(0.5, -0.25);

    bar.querySelector(`[data-hold-action="${action}"]`).click();

    expect(map.jumpTo).not.toHaveBeenCalled();
    expect(map.camera.center).toEqual({ lng: 8.5, lat: 47.75 });
  });

  it.each(['save-pin', 'report'])('%s clears the map down and gives it back once', (action) => {
    const deactivate = vi.spyOn(window.PlacePicker, 'deactivate');
    hold(300, 200);
    deactivate.mockClear();

    bar.querySelector(`[data-hold-action="${action}"]`).click();
    // Closing again afterwards is not a second teardown.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(deactivate).toHaveBeenCalledOnce();
    expect(focus.enters).toBe(1);
    expect(focus.exits).toBe(1);
    deactivate.mockRestore();
  });

  it('keeps the click from reaching the sheet it opens', () => {
    const seen = [];
    const listener = () => seen.push(true);
    document.addEventListener('click', listener);
    hold(300, 200);

    bar.querySelector('[data-hold-action="save-pin"]').click();

    expect(seen).toHaveLength(0);
    document.removeEventListener('click', listener);
  });
});
