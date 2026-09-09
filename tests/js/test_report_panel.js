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
 * SNOW-886 adds the create's own share of the same behaviour: filing a
 * report switches the community-reports layer on and puts the camera on
 * it, the two steps pressing an existing row already took. The case worth
 * the extra test is the one a browser click cannot easily reach — a MANUAL
 * report carries NO coordinates, and that create must still switch the
 * layer on while moving nothing, rather than flying the map to NaN.
 *
 * `IS_ELIGIBLE` is captured once when the IIFE runs, so the two ineligible
 * states keep their own files (test_report_gate_anonymous.js,
 * test_report_gate_unverified.js).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_focus.js';
import '../../static/js/row_removed.js';
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

// `process` is stubbed alongside `ajax` because SNOW-661's repaint from cache
// binds the rows it paints — nothing else in this file reaches it.
globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

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

/**
 * Let a failed list load's cache read settle (SNOW-661).
 *
 * The failure handler asks static/js/observations_offline.js before it draws
 * anything, so what the panel shows is one turn of the event loop behind the
 * event that failed it.
 *
 * @returns {Promise<void>}
 */
function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/**
 * Stand in for the offline row store, which is its own module and its own
 * test file (test_observations_offline.js) — what is asserted here is what
 * this panel does with the answer.
 *
 * @param {?object} record The cached row, or null for a cold device.
 * @returns {void}
 */
function stubCache(record) {
  window.pwaObservationsOffline = {
    read: () => Promise.resolve(record),
    write: () => Promise.resolve(),
  };
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
    async (eventName) => {
      btn.click();
      const container = rows();

      document.dispatchEvent(
        new CustomEvent(eventName, { detail: { target: container } }),
      );
      await settle();

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

describe('a failed list load with rows cached on the device (SNOW-661)', () => {
  // static/js/observations_offline.js holds the last good response body; this
  // module decides what to do with it. The panel used to say "check your
  // connection" for reports the map was already drawing as pins beside it.

  const CACHED_ROWS =
    '<ul>' +
    '<li id="observation-a1b2">' +
    '<button data-row-label data-row-focus="7.5,46.1">Whumpfing</button>' +
    '<time datetime="2026-09-09T06:00:00+00:00" data-relative-time>3 hours ago</time>' +
    '<form data-row-remove><button type="submit">Delete</button></form>' +
    '</li></ul>';

  /**
   * Fail the list request and settle the cache read behind it.
   *
   * @returns {Promise<Element>} The rows container, after the handler ran.
   */
  async function failListLoad() {
    btn.click();
    const container = rows();
    document.dispatchEvent(
      new CustomEvent('htmx:sendError', { detail: { target: container } }),
    );
    await settle();
    return container;
  }

  beforeEach(() => {
    window.pwaRelativeTime = { refresh: vi.fn(), format: vi.fn() };
    stubCache({
      body: CACHED_ROWS,
      cached_at: '2026-09-09T09:00:00+00:00',
    });
  });

  afterEach(() => {
    delete window.pwaRelativeTime;
    stubCache(null);
  });

  it('repaints the rows instead of the failure line', async () => {
    const container = await failListLoad();

    expect(container.textContent).toContain('Whumpfing');
    expect(container.textContent).not.toContain("couldn't be loaded");
  });

  it('says the rows came from the cache', async () => {
    const container = await failListLoad();

    expect(container.textContent).toContain('Showing your saved reports');
  });

  it('carries no Delete form, which offline would do nothing', async () => {
    // Deleting a report is an online-only hx-post — only SUBMISSION goes
    // through the mutation queue — and a control that silently does nothing
    // is worse than one that is not there.
    const container = await failListLoad();

    expect(container.querySelector('[data-row-remove]')).toBeNull();
    expect(container.textContent).toContain('Whumpfing');
  });

  it('refreshes every row age, which no swap event would', async () => {
    // Painting from cache fires no htmx:afterSwap, so relative_time.js's own
    // listener never runs — a row cached at dawn would still read "3 hours
    // ago" at dusk.
    const container = await failListLoad();

    expect(window.pwaRelativeTime.refresh).toHaveBeenCalledWith(container);
  });

  it('is still pressable, because the click handler is on the sheet', async () => {
    window.pwaMapFocus = { point: vi.fn(), bounds: vi.fn() };
    const container = await failListLoad();

    container.querySelector('[data-row-focus]').click();

    expect(window.pwaMapFocus.point).toHaveBeenCalledWith(7.5, 46.1);
    delete window.pwaMapFocus;
  });

  it('falls back to the failure line when the cache is empty', async () => {
    stubCache(null);

    const container = await failListLoad();

    expect(container.textContent).toContain("couldn't be loaded");
  });
});

describe('a row removed from the panel', () => {
  it('re-reads the list, so the empty state arrives with the last row', () => {
    // The row's own hx-swap empties one <li> and nothing else, so a panel
    // that has just lost its last report keeps rendering as a list of
    // none: observations:list's empty state is a server-side clause and
    // only a fresh response can carry it.
    btn.click();
    const container = rows();
    container.innerHTML =
      '<ul><li id="observation-a1b2"><form id="rm" data-row-remove></form></li></ul>';
    globalThis.htmx.ajax.mockClear();

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr, elt: container.querySelector('#rm') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(LIST_URL);
  });

  it('tells the map, so the flag goes with the row', () => {
    // The community-reports feed is anonymised and carries no uuid, so the
    // map cannot know which pin was this user's — only a refetch can tell
    // it, and nothing else asks for one.
    const changed = vi.fn();
    document.addEventListener('snowdesk:reports-changed', changed);
    btn.click();
    const container = rows();
    container.innerHTML =
      '<ul><li id="observation-a1b2"><form id="rm" data-row-remove></form></li></ul>';

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', {
        detail: { xhr, elt: container.querySelector('#rm') },
      }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:reports-changed', changed);
  });

  it('stays quiet for a request that came from outside the rows', () => {
    // The mark rides the request's own xhr, so this module's own form-load
    // htmx.ajax() — and the three sibling panels' requests — cannot be
    // mistaken for a row removal.
    const changed = vi.fn();
    document.addEventListener('snowdesk:reports-changed', changed);
    btn.click();

    const xhr = {};
    document.dispatchEvent(
      new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: document.body } }),
    );
    document.dispatchEvent(
      new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
    );

    expect(changed).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:reports-changed', changed);
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


describe('the sheet-level bridge (SNOW-803)', () => {
  it('exposes window.pwaReportSheet.open(), which opens the panel like a tap', () => {
    expect(Object.isFrozen(window.pwaReportSheet)).toBe(true);
    window.pwaReportSheet.open();
    expect(window.pwaReportSheet.isOpen()).toBe(true);
    expect(sheet.hidden).toBe(false);
    expect(rows()).not.toBeNull();
    window.pwaReportSheet.close();
    expect(window.pwaReportSheet.isOpen()).toBe(false);
  });
});

describe('filing a report shows it on the map (SNOW-886)', () => {
  /**
   * Submit the report form, exactly as a problem button does.
   *
   * The submitter carries the observation type — FormData does not capture
   * a submit button's value once the default submission is prevented, so
   * report.js sets it explicitly and this fixture has to press a real one.
   *
   * @param {?string} lat The form's latitude, or '' for a MANUAL report
   *   filed before the place-picker wrote one.
   * @param {?string} lon The form's longitude.
   * @returns {void}
   */
  function fileReport(lat, lon) {
    sheet.innerHTML = `
      <form id="report-form" action="/partials/report/submit/">
        <input type="hidden" name="csrfmiddlewaretoken" value="tok">
        <input type="hidden" name="observed_at">
        <input type="hidden" name="lat" value="${lat}">
        <input type="hidden" name="lon" value="${lon}">
        <button type="submit" name="observation_type" value="WHUMPFING">Whumpfing</button>
      </form>
      <template id="report-confirmation-template">
        <div><p>Report filed!</p></div>
      </template>`;
    const form = sheet.querySelector('#report-form');
    const submitter = form.querySelector('button[type="submit"]');
    const event = new Event('submit', { bubbles: true, cancelable: true });
    // jsdom does not populate `submitter` on a synthetic submit, and the
    // handler toasts without one — this is the tap it stands in for.
    Object.defineProperty(event, 'submitter', { value: submitter });
    form.dispatchEvent(event);
  }

  beforeEach(() => {
    window.pwaMutationQueue = { enqueue: vi.fn(() => Promise.resolve()) };
    window.pwaDb = { isResetRequired: () => false };
    window.pwaTelemetry = { emit: vi.fn() };
    window.pwaMapFocus = { point: vi.fn(), bounds: vi.fn() };
  });

  afterEach(() => {
    delete window.pwaMutationQueue;
    delete window.pwaDb;
    delete window.pwaTelemetry;
    delete window.pwaMapFocus;
  });

  it('flies to the report it just filed', () => {
    fileReport('46.1', '7.2');

    // [lon, lat] — the map's order, not the form's.
    expect(window.pwaMapFocus.point).toHaveBeenCalledWith(7.2, 46.1);
  });

  it('switches the community-reports layer on', () => {
    // isEnabled() is stubbed false in this file's own beforeEach, which is
    // this overlay's default — so this is the common case, not an edge:
    // the report landed on a map drawing no reports at all.
    fileReport('46.1', '7.2');

    expect(overlay.show).toHaveBeenCalled();
  });

  it('switches the layer on before it moves the camera', () => {
    const order = [];
    overlay.show = vi.fn(() => order.push('overlay'));
    window.pwaMapFocus.point = vi.fn(() => order.push('camera'));

    fileReport('46.1', '7.2');

    expect(order).toEqual(['overlay', 'camera']);
  });

  it('switches the layer on but moves nothing for a report with no coordinates', () => {
    // A MANUAL report filed before the place-picker's first onChange has
    // written anything. report_submit is what decides whether that is
    // acceptable; what this module must not do is fly the camera to NaN.
    fileReport('', '');

    expect(overlay.show).toHaveBeenCalled();
    expect(window.pwaMapFocus.point).not.toHaveBeenCalled();
    expect(window.pwaMapFocus.bounds).not.toHaveBeenCalled();
  });

  it('still renders the confirmation card', () => {
    // The card is the panel's answer to "did that work", and is why the
    // reveal deliberately does not close the sheet.
    fileReport('46.1', '7.2');

    expect(sheet.textContent).toContain('Report filed!');
  });
});
