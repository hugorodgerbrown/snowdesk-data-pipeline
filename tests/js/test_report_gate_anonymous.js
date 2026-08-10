/*
 * tests/js/test_report_gate_anonymous.js — Vitest unit tests for
 * static/js/report.js's anonymous branch (SNOW-649).
 *
 * An anonymous tap on Report must open the sheet with a sign-in CTA and —
 * the part worth a test — must NOT ask the map for a location fix. Firing
 * `snowdesk:locate-request` for a user who cannot submit anything would put
 * a geolocation permission prompt in front of someone who has not asked for
 * one, which is both a privacy question and the kind of prompt that gets a
 * site permanently denied.
 *
 * `IS_ELIGIBLE` / `IS_UNVERIFIED` are read once when the IIFE runs, so each
 * eligibility state needs its own file — see test_report_location_flow.js
 * for the eligible path and test_report_gate_unverified.js for the third
 * state.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

const SIGNIN_URL = '/accounts/sign-in/';

document.body.innerHTML = `
  <button id="report-btn"
          data-report-eligible="false"
          data-report-unverified="false"
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
  btn.click();
});

describe('anonymous tap on Report', () => {
  it('never asks the map for a location fix', () => {
    expect(locateRequests).toEqual([]);
  });

  it('never loads the report form', () => {
    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });

  it('opens the sheet', () => {
    expect(sheet.hidden).toBe(false);
  });

  it('explains why, rather than showing an empty sheet', () => {
    expect(sheet.textContent).toContain('Sign in to submit a field observation.');
  });

  it('offers a link to the configured sign-in page', () => {
    const link = sheet.querySelector(`a[href="${SIGNIN_URL}"]`);
    expect(link).not.toBeNull();
    expect(link.textContent.trim()).toBe('Sign in');
  });

  it('carries a dismiss control, so the sheet is not a dead end (SNOW-474)', () => {
    expect(sheet.querySelector('[data-action="dismiss"]')).not.toBeNull();
  });
});
