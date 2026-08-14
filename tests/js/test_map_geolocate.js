/*
 * tests/js/test_map_geolocate.js — Vitest unit tests for static/js/map_geolocate.js
 * (SNOW-682).
 *
 * The module had no unit coverage at all, and the behaviour SNOW-682 added is
 * exactly the kind Playwright cannot reach: a headless browser either grants a
 * fix or refuses one, and the case that matters here — a phone that answers
 * neither way — is a request left hanging past a timer. Fake timers make it a
 * millisecond; a browser makes it five seconds of real waiting, per case, for
 * an assertion about a data attribute. That is the "answering the right
 * question in the wrong layer" this repo's e2e cap exists to stop.
 *
 * The module is loaded ONCE, at file scope, rather than re-booted per test.
 * Its IIFE binds document-level listeners (`snowdesk:locate-request`,
 * `overlay:dismissed`), and jsdom's document outlives a test — a per-test boot
 * would leave every previous boot's listener attached, so the third test would
 * see three getCurrentPosition calls for one dispatch. Per-test state is reset
 * in beforeEach/afterEach instead, the same shape test_report_location_flow.js
 * uses for report.js's module-private `locating` flag.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { loadMapBundle } from './_load_map_bundle.js';

/** The module's acquisition budget — static/js/map_geolocate.js. */
const FIX_TIMEOUT_MS = 5000;
/** The failure banner's auto-dismiss, deliberately longer than the off-map one. */
const BANNER_TIMEOUT_MS = 12000;

document.body.innerHTML = `
  <div id="offmap-banner" class="hidden" data-overlay data-overlay-hide="class">
    <button type="button" data-action="dismiss">&times;</button>
  </div>
  <div id="locate-failed-banner" class="hidden" data-overlay data-overlay-hide="class">
    <button type="button" id="locate-retry">Try again</button>
    <button type="button" data-action="dismiss">&times;</button>
  </div>
  <div class="map-utility-pill map-utility-pill--locate">
    <button id="locate-toggle" type="button" aria-label="Locate me">
      <svg viewBox="0 0 24 24"><path d="M12 8c-2.21 0-4 1.79-4 4z"/></svg>
    </button>
  </div>
`;

/** Whether the next fix is inside the map's maxBounds. Flipped per test. */
let insideBounds = true;
/** The GeolocateControl instance map_geolocate.js constructs, captured below. */
let control;

window.MAP = {
  addControl: vi.fn(),
  getMaxBounds: () => ({ contains: () => insideBounds }),
};

window.maplibregl = {
  GeolocateControl: class {
    constructor() {
      this.trigger = vi.fn();
      /** Handlers registered via control.on(name, fn), by name. */
      this.handlers = {};
      this.on = vi.fn((name, fn) => {
        this.handlers[name] = fn;
      });
      control = this;
    }
  },
};

const getCurrentPosition = vi.fn();
Object.defineProperty(navigator, 'geolocation', {
  value: { getCurrentPosition },
  configurable: true,
  writable: true,
});

loadMapBundle(['map_geolocate.js']);

const btn = document.getElementById('locate-toggle');
const failedBanner = document.getElementById('locate-failed-banner');
const offMapBanner = document.getElementById('offmap-banner');

/** The success/error pair of the most recent getCurrentPosition call. */
function pending() {
  const call = getCurrentPosition.mock.calls.at(-1);
  return { success: call[0], error: call[1], options: call[2] };
}

/** Answer the in-flight request with a position at the given coordinates. */
function answerWithFix(lat = 46.5, lon = 8.0) {
  pending().success({ coords: { latitude: lat, longitude: lon, accuracy: 12 } });
}

/** Whether the roundel is showing its "looking for you" state. */
function isBusy() {
  return btn.dataset.locating === 'true' && btn.getAttribute('aria-busy') === 'true';
}

beforeEach(() => {
  vi.useFakeTimers();
  insideBounds = true;
  getCurrentPosition.mockClear();
  control.trigger.mockClear();
  failedBanner.classList.add('hidden');
  offMapBanner.classList.add('hidden');
});

afterEach(() => {
  // `locating` is module-private and only cleared when a request settles, so a
  // test that leaves one in flight would wedge every later test on locate()'s
  // early return. Running the watchdog out is the module's own release path.
  vi.advanceTimersByTime(FIX_TIMEOUT_MS);
  // …and then drop the auto-dismiss timer that failure just armed, so it can't
  // fire in the middle of the next test and hide a banner it didn't set.
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('locate roundel busy state', () => {
  it('marks the roundel busy while a fix is in flight', () => {
    btn.click();

    expect(isBusy()).toBe(true);
    expect(getCurrentPosition).toHaveBeenCalledTimes(1);
  });

  it('clears the busy state and flies to a fix that arrives in time', () => {
    btn.click();
    vi.advanceTimersByTime(FIX_TIMEOUT_MS - 1);
    expect(isBusy()).toBe(true);

    answerWithFix();

    expect(isBusy()).toBe(false);
    expect(btn.dataset.locating).toBeUndefined();
    expect(control.trigger).toHaveBeenCalledTimes(1);
    expect(failedBanner.classList.contains('hidden')).toBe(true);
  });

  it('ignores a second tap while a fix is pending', () => {
    btn.click();
    btn.click();

    expect(getCurrentPosition).toHaveBeenCalledTimes(1);
  });
});

describe('locate failure', () => {
  it('gives up after five seconds and offers a retry', () => {
    btn.click();

    vi.advanceTimersByTime(FIX_TIMEOUT_MS - 1);
    expect(failedBanner.classList.contains('hidden')).toBe(true);

    vi.advanceTimersByTime(1);
    expect(failedBanner.classList.contains('hidden')).toBe(false);
    expect(isBusy()).toBe(false);
    expect(control.trigger).not.toHaveBeenCalled();
  });

  it('shows the same banner when the device refuses outright', () => {
    btn.click();
    pending().error({ code: 1, message: 'User denied Geolocation' });

    expect(failedBanner.classList.contains('hidden')).toBe(false);
    expect(isBusy()).toBe(false);
  });

  it('does not act on a fix that arrives after the budget expired', () => {
    btn.click();
    vi.advanceTimersByTime(FIX_TIMEOUT_MS);

    answerWithFix();

    expect(control.trigger).not.toHaveBeenCalled();
    expect(failedBanner.classList.contains('hidden')).toBe(false);
  });

  it('auto-dismisses the banner after twelve seconds', () => {
    btn.click();
    vi.advanceTimersByTime(FIX_TIMEOUT_MS);
    expect(failedBanner.classList.contains('hidden')).toBe(false);

    vi.advanceTimersByTime(BANNER_TIMEOUT_MS);

    expect(failedBanner.classList.contains('hidden')).toBe(true);
  });

  it('retries from the banner CTA', () => {
    btn.click();
    vi.advanceTimersByTime(FIX_TIMEOUT_MS);

    document.getElementById('locate-retry').click();

    expect(getCurrentPosition).toHaveBeenCalledTimes(2);
    expect(isBusy()).toBe(true);
    expect(failedBanner.classList.contains('hidden')).toBe(true);
  });

  it('surfaces the banner when the control itself fails mid-flight', () => {
    btn.click();
    answerWithFix();

    control.handlers.error({ code: 2 });

    expect(failedBanner.classList.contains('hidden')).toBe(false);
    expect(isBusy()).toBe(false);
  });
});

describe('off-map fix', () => {
  it('shows the off-map banner, not the failure banner', () => {
    insideBounds = false;
    btn.click();

    answerWithFix(51.5, -0.12);

    expect(offMapBanner.classList.contains('hidden')).toBe(false);
    expect(failedBanner.classList.contains('hidden')).toBe(true);
    expect(control.trigger).not.toHaveBeenCalled();
    expect(isBusy()).toBe(false);
  });

  it('clears the previous answer when the roundel is tapped again', () => {
    insideBounds = false;
    btn.click();
    answerWithFix(51.5, -0.12);
    expect(offMapBanner.classList.contains('hidden')).toBe(false);

    insideBounds = true;
    btn.click();

    expect(offMapBanner.classList.contains('hidden')).toBe(true);
  });
});

describe('field-report locate requests', () => {
  it('broadcasts the fix on snowdesk:geolocate', () => {
    const fixes = [];
    const handler = (event) => fixes.push(event.detail);
    document.addEventListener('snowdesk:geolocate', handler);

    document.dispatchEvent(new CustomEvent('snowdesk:locate-request'));
    answerWithFix(46.5, 8.0);
    document.removeEventListener('snowdesk:geolocate', handler);

    expect(fixes).toEqual([{ lat: 46.5, lon: 8.0, accuracy: 12 }]);
  });

  it('errors within the same five-second budget so the sheet can fall back', () => {
    let errored = false;
    const handler = () => {
      errored = true;
    };
    document.addEventListener('snowdesk:geolocate-error', handler);

    document.dispatchEvent(new CustomEvent('snowdesk:locate-request'));
    vi.advanceTimersByTime(FIX_TIMEOUT_MS - 1);
    expect(errored).toBe(false);

    vi.advanceTimersByTime(1);
    document.removeEventListener('snowdesk:geolocate-error', handler);

    expect(errored).toBe(true);
    // The report sheet's own safety net is 20s; this has to beat it, or the
    // sheet sits on "Finding your location…" for the difference.
    expect(pending().options.timeout).toBe(FIX_TIMEOUT_MS);
  });

  it('does not put the roundel into its busy state', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:locate-request'));

    expect(isBusy()).toBe(false);
  });
});
