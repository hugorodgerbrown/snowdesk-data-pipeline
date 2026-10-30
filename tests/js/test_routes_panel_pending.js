/*
 * tests/js/test_routes_panel_pending.js — the routes panel's rows for a
 * route drawn offline (SNOW-1262, static/js/routes.js's paintPending).
 *
 * A save route_draw.js queued sits in window.pwaMutationQueue until it
 * replays, and the panel lists it from there, above the server's rows:
 *
 *   - still waiting: "Waiting to sync", with the badge and no menu;
 *   - refused for good: "Couldn't sync", the reason its stored status and
 *     error name — distinct for a validation refusal, the route cap, a
 *     clash on the key, a sign-out and a missing endpoint — and a "…" menu
 *     with Edit and Discard;
 *   - both show alongside the CACHED body when the list cannot load
 *     (the offline repaint), because they live outside [data-routes-rows];
 *   - every queue change repaints them.
 *
 * The queue and draw mode are stubs: their own behaviour is covered by
 * test_mutation_queue_route_create.js and test_route_draw_offline.js.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/row_focus.js';
import '../../static/js/row_removed.js';
import '../../static/js/map_sheet.js';

const LIST_URL = '/routes/partials/list/?variant=map';

document.body.innerHTML = `
  <button id="route-add-btn"
          data-routes-eligible="true"
          data-routes-upload-eligible="true"
          data-signin-url="/sign-in/"
          data-route-create-url="/routes/partials/create/"
          data-route-list-url="${LIST_URL}"></button>
  <div id="route-sheet" hidden></div>
  <template id="route-list-template">
    <div>
      <ul data-routes-pending hidden></ul>
      <div data-routes-rows><p>Loading your routes…</p></div>
    </div>
  </template>
  <template id="route-pending-row-template">
    <li><span data-row-label>Drawn route</span><span data-route-pending-badge>Waiting to sync</span></li>
  </template>
  <template id="route-failed-row-template">
    <li>
      <span data-row-label>Drawn route</span>
      <span data-row-meta>This route couldn't be saved.</span>
      <span data-route-failed-badge>Couldn't sync</span>
      <div data-overflow-menu>
        <button type="button" id="route-failed-actions" data-overflow-trigger
                aria-controls="route-failed-actions-menu"></button>
        <ul id="route-failed-actions-menu" role="menu">
          <li data-route-pending-edit-item><button type="button" data-route-pending-edit>Edit</button></li>
          <li><button type="button" data-route-pending-discard>Discard</button></li>
        </ul>
      </div>
    </li>
  </template>
  <form id="route-upload-form" hidden><input name="csrfmiddlewaretoken" value="tok"></form>
`;

globalThis.htmx = { ajax: vi.fn(() => Promise.resolve()), process: vi.fn() };

let queued = [];
window.pwaMutationQueue = {
  list: vi.fn(async () => queued),
  discard: vi.fn(async () => true),
};
window.pwaRouteDraw = { edit: vi.fn(), discard: vi.fn(async () => {}) };
window.pwaRoutesOffline = {
  read: vi.fn(async () => ({ body: '<ul><li>Cached route</li></ul>', cached_at: new Date().toISOString() })),
};

await import('../../static/js/routes.js');

const btn = document.getElementById('route-add-btn');
const sheet = document.getElementById('route-sheet');

/** One queued route-create row, as list() returns it. */
function row(overrides) {
  return {
    idempotency_key: 'k-1',
    kind: 'route-create',
    status: 'queued',
    body: JSON.stringify({ points: [[7, 46], [7, 46.001]], name: 'Mont Fort' }),
    last_status: null,
    last_error: null,
    ...overrides,
  };
}

function pendingRows() {
  return Array.from(sheet.querySelectorAll('[data-routes-pending] > li'));
}

async function openPanel() {
  if (!sheet.hidden) btn.click();
  btn.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  queued = [];
  window.pwaRouteDraw.edit.mockClear();
  window.pwaRouteDraw.discard.mockClear();
});

describe('a route waiting to sync', () => {
  it('is listed at the top with its name, the badge and no menu', async () => {
    queued = [row()];

    await openPanel();

    const [li] = pendingRows();
    expect(sheet.querySelector('[data-routes-pending]').hidden).toBe(false);
    expect(li.querySelector('[data-row-label]').textContent).toBe('Mont Fort');
    expect(li.querySelector('[data-route-pending-badge]').textContent).toBe('Waiting to sync');
    expect(li.querySelector('[data-overflow-menu]')).toBeNull();
    expect(window.pwaMutationQueue.list).toHaveBeenCalledWith({ kind: 'route-create' });
  });

  it('lists nothing, and hides the list, when the queue holds none', async () => {
    await openPanel();

    expect(pendingRows()).toHaveLength(0);
    expect(sheet.querySelector('[data-routes-pending]').hidden).toBe(true);
  });

  it('repaints when the queue changes', async () => {
    await openPanel();
    queued = [row()];

    document.dispatchEvent(new CustomEvent('pwa:mutation-queue-changed'));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(pendingRows()).toHaveLength(1);
  });

  it('shows alongside the cached rows when the list cannot load', async () => {
    queued = [row()];
    await openPanel();
    const rows = sheet.querySelector('[data-routes-rows]');

    document.dispatchEvent(new CustomEvent('htmx:sendError', { detail: { target: rows } }));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(rows.textContent).toContain('Cached route');
    expect(pendingRows()).toHaveLength(1);
  });
});

describe("a route that couldn't sync", () => {
  it.each([
    [400, null, "The line couldn't be saved as a route. Edit it and try again."],
    [409, 'route_limit_reached', "You've reached your saved-route limit. Delete one, then edit this to save it."],
    [409, null, 'This clashed with an earlier save. Edit it and save it again.'],
    [403, 'authentication_required', 'You were signed out when it synced. Edit it to save it again.'],
    [404, null, "Saving a drawn route isn't available right now."],
    [null, null, "This route couldn't be saved."],
  ])('a %s (%s) gives its own reason', async (status, error, reason) => {
    queued = [row({ status: 'failed', last_status: status, last_error: error })];

    await openPanel();

    const [li] = pendingRows();
    expect(li.querySelector('[data-row-meta]').textContent).toBe(reason);
    expect(li.querySelector('[data-route-failed-badge]')).not.toBeNull();
  });

  it('gives each row its own menu ids', async () => {
    queued = [row({ status: 'failed', last_status: 400 })];

    await openPanel();

    const trigger = pendingRows()[0].querySelector('[data-overflow-trigger]');
    expect(trigger.id).toBe('route-failed-actions-k-1');
    expect(trigger.getAttribute('aria-controls')).toBe('route-failed-actions-menu-k-1');
  });

  it('Edit hands the key to draw mode and closes the panel', async () => {
    queued = [row({ status: 'failed', last_status: 400 })];
    await openPanel();

    pendingRows()[0].querySelector('[data-route-pending-edit]').click();

    expect(window.pwaRouteDraw.edit).toHaveBeenCalledWith('k-1');
    expect(sheet.hidden).toBe(true);
  });

  it('Discard asks first, then discards the row and its draft', async () => {
    queued = [row({ status: 'failed', last_status: 400 })];
    await openPanel();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);

    pendingRows()[0].querySelector('[data-route-pending-discard]').click();

    expect(confirm).toHaveBeenCalledWith('Discard Mont Fort? The line you drew will be lost.');
    expect(window.pwaRouteDraw.discard).toHaveBeenCalledWith('k-1');
    confirm.mockRestore();
  });

  it('Discard does nothing when the user declines', async () => {
    queued = [row({ status: 'failed', last_status: 400 })];
    await openPanel();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);

    pendingRows()[0].querySelector('[data-route-pending-discard]').click();

    expect(window.pwaRouteDraw.discard).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
