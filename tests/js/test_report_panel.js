/*
 * tests/js/test_report_panel.js — Vitest DOM tests for SNOW-658's
 * field-observation panel (static/js/report.js).
 *
 * The roundel used to ask the map for a GPS fix on the first tap, and the
 * "Community reports" map layer was toggled from a row in the layers menu.
 * This ticket collapses the two: the roundel opens a PANEL listing the
 * user's own reports, filing one is an action inside it, and the panel owns
 * the overlay switch. tests/js/test_report_location_flow.js still covers the
 * location state machine that CTA starts; this file covers the panel around
 * it, and mirrors tests/js/test_favourites_panel.js, which is the same
 * treatment applied to the other roundel in the same ticket.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the two ineligible
 * states keep their own files (test_report_gate_anonymous.js,
 * test_report_gate_unverified.js).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_focus.js';
import '../../static/js/map_sheet.js';

const LIST_URL = '/partials/report/list/';
const FORM_URL = '/partials/report/form/';

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

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()) };

await import('../../static/js/report.js');

const btn = document.getElementById('report-btn');
const sheet = document.getElementById('report-sheet');

/** The switch inside the currently-rendered panel body. */
function overlaySwitch() {
  return sheet.querySelector('#map-community-reports-overlay-toggle');
}

/**
 * Close the panel and open it again.
 *
 * SNOW-658: the roundel toggles, so a re-open is two taps — the first
 * closes what is on screen. It used to be one, because a second tap simply
 * re-opened an already-open sheet.
 *
 * @returns {void}
 */
function reopen() {
  btn.click();
  btn.click();
}

/** The rows container inside the currently-rendered panel body. */
function rows() {
  return sheet.querySelector('[data-report-rows]');
}

let overlay;

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  overlay = { show: vi.fn(), hide: vi.fn(), isEnabled: vi.fn(() => false) };
  window.pwaCommunityReportsOverlay = overlay;
  window.PlacePicker = { activate: vi.fn(), deactivate: vi.fn() };
});

afterEach(() => {
  // `locating` is module-private and one-way until the control answers —
  // settle any pending fix so a later test is not wedged. Harmless when
  // nothing is pending: no listener is attached.
  document.dispatchEvent(
    new CustomEvent('snowdesk:geolocate', { detail: { lat: 0, lon: 0 } }),
  );
  delete window.pwaCommunityReportsOverlay;
  delete window.PlacePicker;
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('tapping the roundel opens the panel, not the location flow', () => {
  it('opens the sheet on the list body', () => {
    btn.click();

    expect(sheet.hidden).toBe(false);
    expect(rows()).not.toBeNull();
    expect(sheet.textContent).not.toContain('Finding your location');
  });

  it('asks the map for no fix at all until the CTA is tapped', () => {
    const seen = [];
    const handler = () => seen.push(true);
    document.addEventListener('snowdesk:locate-request', handler);

    btn.click();
    expect(seen).toHaveLength(0);

    sheet.querySelector('[data-panel-add]').click();
    expect(seen).toHaveLength(1);

    document.removeEventListener('snowdesk:locate-request', handler);
  });

  it('loads the rows from observations:list, into the rows container', () => {
    btn.click();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    const [method, url, opts] = globalThis.htmx.ajax.mock.calls[0];
    expect(method).toBe('GET');
    expect(url).toBe(LIST_URL);
    expect(opts.target).toBe(rows());
    expect(opts.swap).toBe('innerHTML');
  });

  it('drops the gate slot for an eligible user', () => {
    btn.click();
    expect(sheet.querySelector('[data-report-gate]')).toBeNull();
  });

  it('re-clones the body on every open, so a stale row cannot survive', () => {
    btn.click();
    rows().innerHTML = '<div id="stale-row"></div>';

    reopen();

    expect(document.getElementById('stale-row')).toBeNull();
    expect(rows()).not.toBeNull();
  });
});

describe('the overlay switch', () => {
  it('opens reflecting the overlay itself, not a flag of its own', () => {
    overlay.isEnabled.mockReturnValue(true);
    btn.click();
    expect(overlaySwitch().checked).toBe(true);

    overlay.isEnabled.mockReturnValue(false);
    reopen();
    expect(overlaySwitch().checked).toBe(false);
  });

  it('drives show()/hide() on the bridge', () => {
    btn.click();
    const toggle = overlaySwitch();

    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(overlay.show).toHaveBeenCalledTimes(1);

    toggle.checked = false;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));
    expect(overlay.hide).toHaveBeenCalledTimes(1);
  });

  it('still works after a re-open, because the listener is on the sheet', () => {
    btn.click();
    reopen();

    const toggle = overlaySwitch();
    toggle.checked = true;
    toggle.dispatchEvent(new Event('change', { bubbles: true }));

    expect(overlay.show).toHaveBeenCalledTimes(1);
  });
});

describe('a list load that fails', () => {
  it.each(['htmx:responseError', 'htmx:sendError'])(
    'replaces the loading line with a failure line on %s',
    (eventName) => {
      btn.click();
      const container = rows();

      document.dispatchEvent(
        new CustomEvent(eventName, { detail: { target: container } }),
      );

      // Never observations:list's "You haven't reported any field observations
      // yet." — the request failed, which says nothing about how many exist.
      expect(container.textContent).toContain("couldn't be loaded");
      expect(container.textContent).not.toContain('Loading your reports');
    },
  );

  it('leaves the form-load error path alone', () => {
    // report.js's own htmx:responseError handler keys off `target === sheet`
    // (the form load) and closes the sheet. The two must not both fire for
    // one request, and this one must not swallow that one.
    btn.click();
    document.dispatchEvent(
      new CustomEvent('htmx:responseError', { detail: { target: sheet } }),
    );

    expect(sheet.hidden).toBe(true);
  });
});

describe('framing a report from its row', () => {
  // This panel's rows are the only ones with nothing else to press — an
  // observation cannot be renamed and has no page of its own — so the
  // label going from inert text to a control is the biggest of the three
  // changes, and it is the same control. window.pwaRowFocus owns the
  // behaviour (test_row_focus.js); what is asserted here is the wire.

  /** Put one row carrying `target` into the open panel. */
  function renderRow(target) {
    btn.click();
    rows().innerHTML = `
      <ul><li>
        <button data-row-label data-row-focus="${target}">Whumpfing</button>
      </li></ul>`;
    return sheet.querySelector('[data-row-focus]');
  }

  beforeEach(() => {
    window.pwaMapFocus = { point: vi.fn(), bounds: vi.fn() };
  });

  afterEach(() => {
    delete window.pwaMapFocus;
  });

  it('flies to the report and closes the panel', () => {
    renderRow('7.5,46.1').click();

    expect(window.pwaMapFocus.point).toHaveBeenCalledWith(7.5, 46.1);
    expect(sheet.hidden).toBe(true);
  });

  it('switches the community-reports overlay on first', () => {
    renderRow('7.5,46.1').click();

    expect(overlay.show).toHaveBeenCalledOnce();
  });

  it('does not start the report flow', () => {
    // The label and the add CTA share one delegated handler, so the label
    // has to stop the click before the CTA test.
    renderRow('7.5,46.1').click();

    expect(window.PlacePicker.activate).not.toHaveBeenCalled();
  });
});
