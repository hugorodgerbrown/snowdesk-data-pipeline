/*
 * tests/js/test_report_gate_unverified.js — Vitest unit tests for
 * static/js/report.js's authenticated-but-unverified branch (SNOW-477,
 * covered under SNOW-649).
 *
 * This is the third eligibility state, and the one most likely to be lost
 * in a refactor: it is neither "signed in" nor "anonymous", and the only
 * thing distinguishing it in the DOM is a second data attribute. Collapsing
 * it back into the anonymous branch would show a signed-in user a "Sign in"
 * button — an instruction they have already followed, with no hint that the
 * real blocker is an unread verification email.
 *
 * Separate file because `IS_UNVERIFIED` is captured once when the IIFE runs;
 * see test_report_location_flow.js for the eligible path.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const SIGNIN_URL = '/accounts/sign-in/';

document.body.innerHTML = `
  <button id="report-btn"
          data-report-eligible="false"
          data-report-unverified="true"
          data-signin-url="${SIGNIN_URL}"
          data-report-form-url="/partials/report/form/"></button>
  <div id="report-sheet" hidden></div>
  <template id="report-list-template">
    <div>
      <div class="flex items-center justify-between px-2 pt-1 pb-3">
        <span>Field observations</span>
        <button type="button" data-action="dismiss" aria-label="Close">×</button>
      </div>
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

let locateRequests;

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  locateRequests = [];
  document.addEventListener('snowdesk:locate-request', () => locateRequests.push(true));
  // SNOW-658: the roundel toggles, so each test has to start from a closed
  // sheet — otherwise every second tap in this beforeEach would close the
  // panel the test is about to read.
  sheet.hidden = true;
  btn.click();
});

describe('unverified tap on Report', () => {
  it('never asks the map for a location fix', () => {
    expect(locateRequests).toEqual([]);
  });

  it('never loads the report form', () => {
    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });

  it('opens the sheet', () => {
    expect(sheet.hidden).toBe(false);
  });

  it('names the real blocker — verification, not sign-in', () => {
    expect(sheet.textContent).toContain('Verify your email');
  });

  it('does NOT offer a sign-in link, which this user has already used', () => {
    // The bug this guards: unverified falling through to the anonymous
    // branch, telling a signed-in user to sign in.
    expect(sheet.querySelector(`a[href="${SIGNIN_URL}"]`)).toBeNull();
  });

  it('carries a dismiss control, so the sheet is not a dead end (SNOW-474)', () => {
    expect(sheet.querySelector('[data-action="dismiss"]')).not.toBeNull();
  });
});
