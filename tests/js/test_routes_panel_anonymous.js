/*
 * tests/js/test_routes_panel_anonymous.js — the routes panel's signed-out
 * branch (SNOW-686).
 *
 * Its own file because `IS_ELIGIBLE` is captured once, when routes.js's IIFE
 * runs, from #route-add-btn's data-routes-eligible — so the eligible and
 * anonymous branches cannot be exercised from one module instance. Same
 * per-module-instance-state split as test_favourites_panel_anonymous.js and
 * the test_report_gate_*.js pair.
 *
 * The roundel is rendered for signed-out visitors too (within the ``routes``
 * waffle flag's gate), so the affordance is discoverable rather than
 * appearing on sign-in — the shape #favourite-add-btn and #report-btn already
 * had. What the tap must NOT do is offer an upload that cannot work.
 *
 * WHY THIS BRANCH IS TESTED THOUGH IT IS UNREACHABLE TODAY. The ``routes``
 * flag is seeded superusers-only (apps/routes/migrations/0002_seed_routes_flag),
 * so in production every visitor who can see this roundel is signed in.
 * Opening the flag up to ``everyone`` is an admin toggle, not a code change
 * — the branch has to already be right at the moment somebody flips it, and
 * a branch nothing exercises is a branch nobody has checked. See
 * apps.public.views._routes_context for the two gates and why they are
 * separate.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="false"
          data-signin-url="/sign-in/"
          data-route-create-url="/routes/partials/create/"
          data-route-list-url="/routes/partials/list/?variant=map"
          data-route-rename-url-template="/routes/partials/__UUID__/rename/"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div>
      <div data-routes-rows><p>Loading your routes…</p></div>
      <button type="button" data-panel-add>Add a route</button>
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

describe('the signed-out branch', () => {
  it('still opens the panel — the roundel is not a dead control', () => {
    btn.click();

    expect(sheet.hidden).toBe(false);
  });

  it('shows a sign-in CTA in place of the rows', () => {
    btn.click();

    const rows = sheet.querySelector('[data-routes-rows]');
    expect(rows.textContent).toContain('Sign in to save a route.');
    const link = rows.querySelector('a');
    expect(link.getAttribute('href')).toBe('/sign-in/');
    expect(link.textContent).toBe('Sign in');
  });

  it('removes the add CTA — there is nothing behind it yet', () => {
    btn.click();

    expect(sheet.querySelector('[data-panel-add]')).toBeNull();
  });

  it('never requests the list', () => {
    // routes:list answers 403 for an anonymous request. Asking anyway would
    // spend a round trip to be told what the surface already knows, and the
    // failure would repaint the CTA with the panel's error line.
    btn.click();

    expect(globalThis.htmx.ajax).not.toHaveBeenCalled();
  });

  it('does not open the file picker even if an add CTA is present', () => {
    // Belt and braces: the CTA is removed above, but the click handler
    // guards on eligibility too, so a surface that rendered one anyway
    // cannot start an upload that would 403.
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

  it('uploads nothing if a file somehow reaches the input', () => {
    Object.defineProperty(uploadInput, 'files', {
      configurable: true,
      value: [new File(['<gpx></gpx>'], 'x.gpx')],
    });

    uploadInput.dispatchEvent(new Event('change', { bubbles: true }));

    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
});
