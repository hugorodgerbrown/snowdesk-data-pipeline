/*
 * tests/js/test_pwa_reset.js — Vitest unit tests for static/js/pwa_reset.js
 * (SNOW-378, SNOW-384; blocked-delete handling from the 2026-08-03 JS review,
 * finding M6).
 *
 * Focus is the IndexedDB step: a delete that fires `onblocked` — another tab
 * still holds the connection — must not count as a success. Before M6 it did,
 * and the page reloaded straight back into the Reset Required overlay it had
 * just been asked to clear.
 *
 * Harness notes:
 *   - `pwa_reset.js` publishes `window.pwaResetLocalData` with
 *     `Object.defineProperty(..., { configurable: false })`, so the module is
 *     imported ONCE at the top of this file. `vi.resetModules()` + re-import
 *     would hit "Cannot redefine property" on the second IIFE run.
 *   - jsdom's `location.reload` is an unforgeable own property (non-writable,
 *     non-configurable), so it cannot be spied on. The reload decision is
 *     asserted through the outcome `resetLocalData` returns and through the
 *     overlay state instead.
 *   - jsdom ships neither `navigator.serviceWorker` nor `caches`, so steps (1)
 *     and (2) short-circuit and IndexedDB is the only failure source here —
 *     which is exactly the step under test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/pwa_reset.js';

const DB_NAME = 'snowdesk-pwa-v1';
const ORIGINAL_BODY_COPY = 'Snowdesk needs to reset local data on this device.';

/** Mirror of the markup `templates/includes/_pwa_reset_required.html` renders. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="pwa-reset-required" class="hidden">
      <h2 id="pwa-reset-required-title">Reset local data</h2>
      <p id="pwa-reset-required-body">${ORIGINAL_BODY_COPY}</p>
      <button id="pwa-reset-required-cta" type="button">Reset now</button>
    </div>
  `;
}

/**
 * Stub `indexedDB.deleteDatabase` with a request that settles on the named
 * handler. The module attaches its handlers synchronously on return, so a
 * macrotask hop is enough to guarantee they are in place first.
 *
 * @param {'onsuccess'|'onerror'|'onblocked'} handler
 */
function stubDelete(handler) {
  const spy = vi.fn(() => {
    const req = {};
    setTimeout(() => {
      if (typeof req[handler] === 'function') req[handler]();
    }, 0);
    return req;
  });
  vi.spyOn(indexedDB, 'deleteDatabase').mockImplementation(spy);
  return spy;
}

beforeEach(() => {
  buildFixture();
  vi.spyOn(indexedDB, 'databases').mockResolvedValue([{ name: DB_NAME }]);
  window.pwaTelemetry = { emit: vi.fn() };
});

afterEach(() => {
  vi.restoreAllMocks();
  delete window.pwaTelemetry;
});

describe('a blocked IndexedDB delete', () => {
  it('is not reported as a successful reset', async () => {
    stubDelete('onblocked');

    const outcome = await window.pwaResetLocalData(true);

    expect(outcome.ok).toBe(false);
    expect(outcome.failed).toEqual(['indexeddb']);
  });

  it('explains the failure on the Reset Required overlay', async () => {
    stubDelete('onblocked');
    // The forced path: db.js has already revealed the overlay.
    document.getElementById('pwa-reset-required').classList.remove('hidden');

    await window.pwaResetLocalData(true);

    const body = document.getElementById('pwa-reset-required-body');
    expect(body.textContent).not.toContain(ORIGINAL_BODY_COPY);
    expect(body.textContent).toContain('the offline database');
    expect(body.textContent).toContain('another tab');
  });

  it('reveals the overlay on the elective path, where nothing showed it', async () => {
    stubDelete('onblocked');

    await window.pwaResetLocalData();

    const overlay = document.getElementById('pwa-reset-required');
    expect(overlay.classList.contains('hidden')).toBe(false);
  });

  it('reports the failed step through the existing telemetry event', async () => {
    stubDelete('onblocked');

    await window.pwaResetLocalData(true);

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('pwa.reset.forced', {
      ok: false,
      failed_steps: ['indexeddb'],
    });
  });

  it('binds the overlay CTA to a retry when it revealed the overlay itself', async () => {
    const deleteDb = stubDelete('onblocked');

    await window.pwaResetLocalData();
    expect(deleteDb).toHaveBeenCalledTimes(1);

    document.getElementById('pwa-reset-required-cta').click();
    await vi.waitFor(() => expect(deleteDb).toHaveBeenCalledTimes(2));
  });

  it('leaves an already-revealed overlay CTA alone, since db.js owns it', async () => {
    // Double-binding would run two wipes per tap on the forced path.
    const deleteDb = stubDelete('onblocked');
    document.getElementById('pwa-reset-required').classList.remove('hidden');

    await window.pwaResetLocalData(true);
    document.getElementById('pwa-reset-required-cta').click();
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(deleteDb).toHaveBeenCalledTimes(1);
  });
});

describe('an errored IndexedDB delete', () => {
  it('is also a failure, and leaves the page in place', async () => {
    stubDelete('onerror');

    const outcome = await window.pwaResetLocalData(true);

    expect(outcome.ok).toBe(false);
    expect(outcome.failed).toEqual(['indexeddb']);
  });
});

describe('a clean run', () => {
  it('reports success and does not touch the overlay', async () => {
    stubDelete('onsuccess');
    // The success path reloads; jsdom answers navigation with a virtual-console
    // error, which would otherwise be noise on an otherwise-passing run.
    vi.spyOn(console, 'error').mockImplementation(() => {});

    const outcome = await window.pwaResetLocalData();

    expect(outcome.ok).toBe(true);
    expect(outcome.failed).toEqual([]);
    const overlay = document.getElementById('pwa-reset-required');
    expect(overlay.classList.contains('hidden')).toBe(true);
    expect(document.getElementById('pwa-reset-required-body').textContent).toContain(
      ORIGINAL_BODY_COPY,
    );
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.reset.user_initiated',
      { ok: true, failed_steps: [] },
    );
  });
});
