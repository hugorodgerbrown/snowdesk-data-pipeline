/*
 * tests/js/test_report_location_flow.js — Vitest unit tests for the
 * location-source state machine in static/js/report.js (SNOW-649).
 *
 * report.js's own header spells this machine out in prose — GPS path,
 * MANUAL path, 20-second safety-net timeout — and until now none of it had
 * a unit test. The only coverage was Playwright, which cannot reasonably
 * reach the interesting branches at all: a headless Chromium either grants
 * a geolocation fix or it does not, and nothing in an e2e run is going to
 * sit through a twenty-second timeout to prove the safety net routes into
 * MANUAL rather than leaving the sheet stuck on "Finding your location…".
 *
 * What makes this worth testing is that every branch ends in a POST that
 * records WHERE a field observation was made. A `location_source` of GPS on
 * a coordinate the user actually panned to by hand is not a cosmetic bug —
 * it is a wrong provenance stamp on safety data other people read.
 *
 * report.js does not call navigator.geolocation itself. It asks map.js's
 * GeolocateControl for a fix by dispatching `snowdesk:locate-request` and
 * waits for `snowdesk:geolocate` / `snowdesk:geolocate-error`, so the tests
 * drive it entirely with document-level events and never stub the
 * Geolocation API.
 *
 * This file covers the ELIGIBLE user. `IS_ELIGIBLE` is captured once when
 * the IIFE runs, so the two ineligible states need their own files — the
 * same per-module-instance-state reason test_telemetry*.js is split.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const FORM_URL = '/partials/report/form/';
const LIST_URL = '/partials/report/list/';

/**
 * SNOW-658: start the report flow the way a user now does — tap the roundel
 * to open the panel, then its "Report an observation" CTA. The roundel alone
 * no longer asks for a fix; it lists what the user has already filed.
 */
function startReport() {
  const btn = document.getElementById('report-btn');
  // SNOW-658: the roundel TOGGLES — a tap while the sheet is up closes it.
  // A flow started with the sheet already open (this suite's re-entrancy
  // test) therefore closes and re-opens, which is what a user does.
  if (!document.getElementById('report-sheet').hidden) btn.click();
  btn.click();
  const add = document.querySelector('[data-panel-add]');
  if (add) add.click();
}

/** Every htmx.ajax call that was a report-form load, not the panel's list. */
function formLoads() {
  return globalThis.htmx.ajax.mock.calls.filter((call) => String(call[1]).startsWith(FORM_URL));
}

document.body.innerHTML = `
  <button id="report-btn"
          data-report-eligible="true"
          data-report-unverified="false"
          data-signin-url="/sign-in/"
          data-report-list-url="${LIST_URL}"
          data-report-form-url="${FORM_URL}"></button>
  <div id="report-sheet" hidden></div>
  <template id="report-list-template">
    <div>
      <div data-report-gate></div>
      <div data-report-rows><p>Loading your reports…</p></div>
      <button type="button" data-panel-add>Report an observation</button>
      <input id="map-community-reports-overlay-toggle" type="checkbox" role="switch">
    </div>
  </template>
`;

/**
 * htmx is a bare global in report.js (`htmx.ajax(...)`), resolved at call
 * time, so this only has to exist before the first click — not before the
 * import. It resolves the promise loadForm's MANUAL branch chains off.
 */
globalThis.htmx = {
  ajax: vi.fn(() => Promise.resolve()),
};

await import('../../static/js/report.js');

const btn = document.getElementById('report-btn');
const sheet = document.getElementById('report-sheet');

/** The parsed query of the most recent htmx.ajax GET. */
function lastFormParams() {
  const url = formLoads().at(-1)[1];
  return new URLSearchParams(url.slice(url.indexOf('?') + 1));
}

/** Record `snowdesk:locate-request` dispatches for the enclosing test. */
let locateRequests;
let removeLocateListener;

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  locateRequests = [];
  const handler = () => locateRequests.push(true);
  document.addEventListener('snowdesk:locate-request', handler);
  removeLocateListener = () => document.removeEventListener('snowdesk:locate-request', handler);
  window.PlacePicker = { activate: vi.fn(), deactivate: vi.fn() };
});

afterEach(() => {
  // `locating` is module-private and one-way until the control answers, so
  // a test that clicks without settling its fix would wedge every later
  // test in the file. Settling it here is the module's own release path —
  // onLocate runs cleanup(), which clears the flag, the timer and both
  // listeners. Harmless when nothing is pending: no listener is attached.
  document.dispatchEvent(new CustomEvent('snowdesk:geolocate', { detail: { lat: 0, lon: 0 } }));
  removeLocateListener();
  vi.useRealTimers();
  delete window.PlacePicker;
  sheet.hidden = true;
});

/** Fire the GeolocateControl's success broadcast. */
function emitFix(detail) {
  document.dispatchEvent(new CustomEvent('snowdesk:geolocate', { detail }));
}

/** Fire the GeolocateControl's failure broadcast. */
function emitGeolocateError() {
  document.dispatchEvent(new CustomEvent('snowdesk:geolocate-error'));
}

describe('tapping Report asks the map for a fix', () => {
  it('dispatches snowdesk:locate-request rather than calling geolocation itself', () => {
    startReport();
    expect(locateRequests).toHaveLength(1);
  });

  it('opens the sheet immediately, before any fix arrives', () => {
    startReport();
    expect(sheet.hidden).toBe(false);
    expect(sheet.textContent).toContain('Finding your location');
  });

  it('does not load the form until the control answers', () => {
    startReport();
    expect(formLoads()).toHaveLength(0);
  });

  it('ignores a re-entrant tap while a fix is already pending', () => {
    startReport();
    startReport();
    startReport();
    expect(locateRequests).toHaveLength(1);
  });
});

describe('GPS path — a fix arrives', () => {
  it('stamps location_source=GPS', () => {
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    expect(lastFormParams().get('location_source')).toBe('GPS');
  });

  it('sends the fix as both the report coords and the raw gps_* pair', () => {
    // The two pairs diverge only after a Refine; on first load they are the
    // same point, and gps_lat/gps_lon must be populated so a later
    // GPS_REFINED submission still carries the original device fix.
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    const params = lastFormParams();
    expect(params.get('lat')).toBe('46.1');
    expect(params.get('lon')).toBe('7.2');
    expect(params.get('gps_lat')).toBe('46.1');
    expect(params.get('gps_lon')).toBe('7.2');
    expect(params.get('accuracy')).toBe('12');
  });

  it('requests the form from the configured endpoint', () => {
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    const [method, url] = formLoads().at(-1);
    expect(method).toBe('GET');
    expect(url.startsWith(FORM_URL + '?')).toBe(true);
  });

  it('does not arm the place-picker — the GPS point stands until Refine', () => {
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    expect(window.PlacePicker.activate).not.toHaveBeenCalled();
  });

  it('releases the re-entrancy guard so a second report can be started', () => {
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    startReport();
    expect(locateRequests).toHaveLength(2);
  });

  it('stops listening once a fix has landed, so a late second fix is ignored', () => {
    // The GeolocateControl can broadcast again as the device refines its
    // position. Without the cleanup, that would silently reload the form
    // underneath a user already filling it in.
    startReport();
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    emitFix({ lat: 46.9, lon: 7.9, accuracy: 3 });
    expect(formLoads()).toHaveLength(1);
  });
});

describe('MANUAL path — the control reports an error', () => {
  it('stamps location_source=MANUAL', async () => {
    startReport();
    emitGeolocateError();
    expect(lastFormParams().get('location_source')).toBe('MANUAL');
  });

  it('sends no coordinates at all — there is no fix to claim', async () => {
    const params = (startReport(), emitGeolocateError(), lastFormParams());
    expect(params.has('lat')).toBe(false);
    expect(params.has('lon')).toBe(false);
    expect(params.has('accuracy')).toBe(false);
    expect(params.has('gps_lat')).toBe(false);
    expect(params.has('gps_lon')).toBe(false);
  });

  it('keeps the sheet open rather than closing it on a denied fix', async () => {
    startReport();
    emitGeolocateError();
    await Promise.resolve();
    expect(sheet.hidden).toBe(false);
  });

  it('arms the place-picker once the form swap has settled', async () => {
    // Ordering is load-bearing: PlacePicker's immediate onChange writes into
    // the form's hidden inputs, so arming it before the swap would write
    // into a form that does not exist yet.
    startReport();
    emitGeolocateError();
    expect(window.PlacePicker.activate).not.toHaveBeenCalled();
    await Promise.resolve();
    await Promise.resolve();
    expect(window.PlacePicker.activate).toHaveBeenCalledTimes(1);
  });

  it('tells the place-picker the sheet occludes the pin', async () => {
    startReport();
    emitGeolocateError();
    await Promise.resolve();
    await Promise.resolve();
    expect(window.PlacePicker.activate.mock.calls[0][0].occludedBy).toBe(sheet);
  });

  it('writes MANUAL — never GPS — through the picker onChange', async () => {
    startReport();
    emitGeolocateError();
    await Promise.resolve();
    await Promise.resolve();

    sheet.innerHTML = `
      <form id="report-form">
        <input type="hidden" name="lat" value="">
        <input type="hidden" name="lon" value="">
        <input type="hidden" name="location_source" value="">
      </form>`;
    window.PlacePicker.activate.mock.calls[0][0].onChange(46.5, 7.5);

    const form = document.getElementById('report-form');
    expect(form.querySelector('[name="lat"]').value).toBe('46.5');
    expect(form.querySelector('[name="lon"]').value).toBe('7.5');
    expect(form.querySelector('[name="location_source"]').value).toBe('MANUAL');
  });
});

describe('safety-net timeout — the control never answers', () => {
  it('routes into the MANUAL path after 20 seconds', () => {
    vi.useFakeTimers();
    startReport();
    expect(formLoads()).toHaveLength(0);

    vi.advanceTimersByTime(20000);

    expect(formLoads()).toHaveLength(1);
    expect(lastFormParams().get('location_source')).toBe('MANUAL');
  });

  it('does not fire early — a fix at 19s still wins', () => {
    vi.useFakeTimers();
    startReport();
    vi.advanceTimersByTime(19000);
    emitFix({ lat: 46.1, lon: 7.2, accuracy: 12 });
    vi.advanceTimersByTime(5000);

    expect(formLoads()).toHaveLength(1);
    expect(lastFormParams().get('location_source')).toBe('GPS');
  });

  it('is cancelled by an error answer, so MANUAL is not loaded twice', () => {
    vi.useFakeTimers();
    startReport();
    emitGeolocateError();
    vi.advanceTimersByTime(30000);

    expect(formLoads()).toHaveLength(1);
  });
});
