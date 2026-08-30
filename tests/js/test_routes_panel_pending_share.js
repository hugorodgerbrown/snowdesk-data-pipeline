/*
 * tests/js/test_routes_panel_pending_share.js — the routes panel for a
 * signed-out visitor holding a followed share link (SNOW-764).
 *
 * The third combination of the panel's two eligibility attributes, and its
 * own module instance for the reason test_routes_panel_anonymous.js gives:
 * routes.js captures both once, when its IIFE runs.
 *
 *   eligible + upload-eligible  → the ordinary signed-in panel
 *                                 (test_routes_panel.js)
 *   neither                     → the sign-in CTA replaces the rows
 *                                 (test_routes_panel_anonymous.js)
 *   eligible, cannot upload     → HERE. They were sent a route, so there
 *                                 ARE rows; saving one still needs an
 *                                 account.
 *
 * The behaviour this file exists to pin is that the sign-in prompt is
 * ADDED, not substituted. The pre-SNOW-764 anonymous branch replaced the
 * rows container's contents wholesale, and applying that here would hide
 * the route the link was for behind a prompt about saving it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';
import '../../static/js/share.js';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="true"
          data-routes-upload-eligible="false"
          data-signin-url="/sign-in/"
          data-route-create-url="/routes/partials/create/"
          data-route-list-url="/routes/partials/list/?variant=map"
          data-route-rename-url-template="/routes/partials/__UUID__/rename/"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div>
      <div data-routes-rows><p>Loading your routes…</p></div>
      <div data-panel-foot>
        <button type="button" data-panel-add>Add a route</button>
      </div>
    </div>
  </template>
  <form id="route-upload-form" hidden>
    <input type="hidden" name="csrfmiddlewaretoken" value="tok">
    <input type="file" id="route-upload-input" name="file">
  </form>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

await import('../../static/js/routes.js');

const btn = document.getElementById('route-add-btn');
const sheet = document.getElementById('route-sheet');
const uploadInput = document.getElementById('route-upload-input');

beforeEach(() => {
  globalThis.htmx.ajax.mockClear();
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 200 }));
  sheet.hidden = true;
  sheet.replaceChildren();
});

describe('a signed-out visitor holding a pending share', () => {
  it('loads the rows — the shared route is what they came for', () => {
    btn.click();

    expect(globalThis.htmx.ajax).toHaveBeenCalledTimes(1);
    // The URL is used VERBATIM — the server puts ?variant=map on it, and
    // rebuilding the path here would silently render the wrong row shape.
    expect(globalThis.htmx.ajax.mock.calls[0][1]).toBe(
      '/routes/partials/list/?variant=map',
    );
  });

  it('leaves the rows container alone rather than replacing it', () => {
    // The defect this guards: the pre-SNOW-764 anonymous branch called
    // rows.replaceChildren(cta), which here would wipe the list before it
    // arrived.
    btn.click();

    const rows = sheet.querySelector('[data-routes-rows]');
    expect(rows.querySelector('a')).toBeNull();
    expect(rows.textContent).not.toContain('Sign in to save a route.');
  });

  it('puts the sign-in prompt at the panel foot, under the rows', () => {
    btn.click();

    const foot = sheet.querySelector('[data-panel-foot]');
    expect(foot.textContent).toContain('Sign in to save a route.');
    expect(foot.querySelector('a').getAttribute('href')).toBe('/sign-in/');
  });

  it('still removes the add CTA — uploading needs an account', () => {
    btn.click();

    expect(sheet.querySelector('[data-panel-add]')).toBeNull();
  });

  it('does not open the file picker if an add CTA is present anyway', () => {
    btn.click();
    const rows = sheet.querySelector('[data-routes-rows]');
    const cta = document.createElement('button');
    cta.setAttribute('data-panel-add', '');
    rows.appendChild(cta);
    const picker = vi.spyOn(uploadInput, 'click');

    cta.click();

    expect(picker).not.toHaveBeenCalled();
    picker.mockRestore();
  });
});
