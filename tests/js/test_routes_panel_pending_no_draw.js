/*
 * tests/js/test_routes_panel_pending_no_draw.js — Discard on a route
 * waiting to sync when draw mode is not on the page (SNOW-1262).
 *
 * With the route_drawing flag off map.html loads no route_draw.js, so
 * there is no window.pwaRouteDraw, but the routes panel still lists a save
 * queued while the flag was on and offers Discard. That Discard must take
 * the stored draft with the row — through route_draw_core.js's
 * forgetDraftFor, which _routes_surface.html now loads flag or no flag —
 * or turning drawing back on would restore the discarded line. A draft
 * tagged with a different key is another save's, and stays.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_sheet.js';
import '../../static/js/route_draw_core.js';

const DRAFT_KEY = 'snowdesk.route-draft';

document.body.innerHTML = `
  <button id="route-add-btn" data-routes-eligible="true" data-routes-upload-eligible="true"
          data-signin-url="/sign-in/" data-route-list-url="/routes/partials/list/?variant=map"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div><ul data-routes-pending hidden></ul><div data-routes-rows></div></div>
  </template>
  <template id="route-failed-row-template">
    <li>
      <span data-row-label>Drawn route</span><span data-row-meta></span>
      <div data-overflow-menu>
        <button type="button" data-overflow-trigger></button>
        <ul role="menu">
          <li data-route-pending-edit-item><button type="button" data-route-pending-edit>Edit</button></li>
          <li><button type="button" data-route-pending-discard>Discard</button></li>
        </ul>
      </div>
    </li>
  </template>`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

let queued = [];
window.pwaMutationQueue = { list: vi.fn(async () => queued), discard: vi.fn(async () => true) };

await import('../../static/js/routes.js');

const btn = document.getElementById('route-add-btn');
const sheet = document.getElementById('route-sheet');
const core = self.pwaRouteDrawCore;
const POINTS = [[7, 46], [7, 46.001]];

async function openWith(key) {
  queued = [{ idempotency_key: key, status: 'failed', body: '{"name": "Mont Fort"}', last_status: 400 }];
  if (!sheet.hidden) btn.click();
  btn.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

async function discardRow(key) {
  await openWith(key);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
  sheet.querySelector('[data-route-pending-discard]').click();
  confirm.mockRestore();
}

describe('Discard with no draw mode on the page', () => {
  it('offers no Edit', async () => {
    await openWith('k-1');

    expect(window.pwaRouteDraw).toBeUndefined();
    expect(sheet.querySelector('[data-route-pending-edit]')).toBeNull();
  });

  it("removes the row and the draft tagged with that save's key", async () => {
    localStorage.setItem(DRAFT_KEY, core.serialiseDraft(POINTS, 'Mont Fort', [0, 1], { failed: 'k-1' }));

    await discardRow('k-1');

    expect(window.pwaMutationQueue.discard).toHaveBeenCalledWith('k-1');
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
  });

  it('leaves a draft tagged with a different key alone', async () => {
    const other = core.serialiseDraft(POINTS, 'Other', [0, 1], { key: 'k-2' });
    localStorage.setItem(DRAFT_KEY, other);

    await discardRow('k-1');

    expect(localStorage.getItem(DRAFT_KEY)).toBe(other);
  });
});
