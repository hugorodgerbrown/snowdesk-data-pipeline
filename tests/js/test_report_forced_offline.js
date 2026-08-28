/*
 * tests/js/test_report_forced_offline.js — filing an observation under a
 * user-forced offline mode is a queued submission, and the confirmation must
 * say so (SNOW-748).
 *
 * `report.js` reveals the confirmation card's "will sync when you're back
 * online" line from whether the app was offline at the moment of the tap. It
 * read `navigator.onLine`, which stays TRUE under a mode the user forced from
 * the header's network toggle — so a report that was in fact sitting in the
 * mutation queue told the user it had already been filed. The submission
 * itself was always queued; only the account of it was wrong, which on a
 * safety surface is the part that matters.
 *
 * The submit handler is delegated from `document` and reads the form's own
 * fields, so the tests drive it with a real `submit` event on a form matching
 * what `_report_form.html` renders. The queue and
 * `MapSheet.canQueueMutations` are stubbed: what is under test is which
 * question this module asks about connectivity, not the queue's own
 * behaviour (tests/js/test_mutation_queue.js).
 *
 * `IS_ELIGIBLE` and the three sheet elements are captured once when the IIFE
 * runs, so the fixture is built before the dynamic import — the same
 * per-module-instance-state constraint tests/js/test_report_panel.js
 * documents.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const SUBMIT_URL = '/partials/report/submit/';

document.body.innerHTML = `
  <button id="report-btn"
          data-report-eligible="true"
          data-report-unverified="false"
          data-signin-url="/sign-in/"
          data-report-list-url="/partials/report/list/"
          data-report-form-url="/partials/report/form/"></button>
  <div id="report-sheet" hidden></div>
  <template id="report-list-template">
    <div>
      <div data-report-gate></div>
      <div data-report-rows></div>
      <button type="button" data-panel-add>Report an observation</button>
    </div>
  </template>
  <template id="report-confirmation-template">
    <div>
      <p>Thank you for your report!</p>
      <p data-report-pending hidden>Saved — will sync when you're back online.</p>
    </div>
  </template>
  <div id="map-sheet-toast" role="alert" data-overlay data-overlay-hide="class" class="hidden">
    <span data-toast-body></span>
  </div>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/report.js');

const sheet = document.getElementById('report-sheet');

/**
 * Submit the report form the way a problem button does — the submitter
 * carries the observation type, which the handler patches into the body.
 *
 * @returns {void}
 */
function fileReport() {
  sheet.innerHTML = `
    <form id="report-form" action="${SUBMIT_URL}">
      <input type="hidden" name="csrfmiddlewaretoken" value="tok">
      <input type="hidden" name="lat" value="46.1">
      <input type="hidden" name="lon" value="7.2">
      <input type="hidden" name="location_source" value="GPS">
      <input type="hidden" name="observed_at" value="">
      <button type="submit" name="observation_type" value="AVALANCHE">Avalanche</button>
    </form>`;
  const form = sheet.querySelector('#report-form');
  const event = new Event('submit', { bubbles: true, cancelable: true });
  // jsdom does not populate `submitter` on a hand-built submit event, and the
  // handler refuses a submission without one.
  Object.defineProperty(event, 'submitter', {
    value: form.querySelector('button[name="observation_type"]'),
  });
  form.dispatchEvent(event);
}

/** True while the confirmation card's "will sync" line is on screen. */
function pendingLineVisible() {
  const line = sheet.querySelector('[data-report-pending]');
  return !!line && !line.hasAttribute('hidden');
}

beforeEach(() => {
  window.pwaTelemetry = { emit: vi.fn() };
  window.pwaMutationQueue = { enqueue: vi.fn(() => Promise.resolve()) };
  window.pwaDb = { isResetRequired: () => false };
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
  delete window.pwaMutationQueue;
  delete window.pwaDb;
  delete window.pwaConnectivity;
  sheet.replaceChildren();
});

describe('filing a report under a forced offline mode (SNOW-748)', () => {
  it('queues the report and says it will sync, with the interface up', () => {
    window.pwaConnectivity = { isOnline: () => false };
    // The distinguishing fact: nothing about the interface says offline.
    expect(navigator.onLine).toBe(true);

    fileReport();

    // The submission itself is unchanged — it is captured exactly as before,
    // which is what the queue is for.
    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(window.pwaMutationQueue.enqueue.mock.calls[0][0].url).toBe(SUBMIT_URL);
    expect(pendingLineVisible()).toBe(true);
  });

  it('claims no pending sync under the auto mode', () => {
    window.pwaConnectivity = { isOnline: () => true };

    fileReport();

    expect(window.pwaMutationQueue.enqueue).toHaveBeenCalledTimes(1);
    expect(pendingLineVisible()).toBe(false);
  });

  it('falls back to the interface on a page where pwa_offline.js has not run', () => {
    // No `window.pwaConnectivity` at all — the historical behaviour, which
    // the swap must preserve rather than treat as "offline".
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);

    fileReport();

    expect(pendingLineVisible()).toBe(true);
  });
});
