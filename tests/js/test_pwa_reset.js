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

// SNOW-618: the two [data-pwa-reset-trigger] buttons the binding tests
// drive. They must exist BEFORE the import below, because `bindAll()` runs
// once at import time over whatever is in the DOM then — under jsdom
// `document.readyState` is already 'complete', so the module's
// DOMContentLoaded branch never fires and a button added later is never
// bound. `beforeEach` re-appends these same elements after rebuilding the
// fixture, since listeners survive detach and re-attach.
buildFixture();
const trigger = document.createElement('button');
trigger.type = 'button';
trigger.setAttribute('data-pwa-reset-trigger', '');
document.body.appendChild(trigger);

const triggerSkipConfirm = document.createElement('button');
triggerSkipConfirm.type = 'button';
triggerSkipConfirm.setAttribute('data-pwa-reset-trigger', '');
triggerSkipConfirm.setAttribute('data-pwa-reset-skip-confirm', '');
document.body.appendChild(triggerSkipConfirm);

await import('../../static/js/pwa_reset.js');

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
  // Re-attach the already-bound triggers that buildFixture just wiped.
  document.body.appendChild(trigger);
  document.body.appendChild(triggerSkipConfirm);
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

describe('the trigger binding (SNOW-618)', () => {
  it('does nothing when the user declines the confirmation', async () => {
    const del = stubDelete('onsuccess');
    vi.stubGlobal('confirm', vi.fn(() => false));

    trigger.click();
    await new Promise((resolve) => setTimeout(resolve, 10));

    // A destructive, irreversible action behind a dialog the user said no
    // to. Running anyway is the worst possible reading of "cancel".
    expect(del).not.toHaveBeenCalled();
    expect(window.pwaTelemetry.emit).not.toHaveBeenCalled();
  });

  it('runs when the user accepts', async () => {
    stubDelete('onsuccess');
    vi.stubGlobal('confirm', vi.fn(() => true));

    trigger.click();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.reset.user_initiated',
      expect.objectContaining({ ok: true }),
    );
  });

  it('skips the dialog for a trigger that opts out', async () => {
    stubDelete('onsuccess');
    const confirmSpy = vi.fn(() => true);
    vi.stubGlobal('confirm', confirmSpy);

    triggerSkipConfirm.click();
    await new Promise((resolve) => setTimeout(resolve, 10));

    // Only the Update Required modal opts out, and it has already put its
    // own dialogue in front of the user — a second one reads as a bug.
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(window.pwaTelemetry.emit).toHaveBeenCalled();
  });
});

describe('telemetry names the caller (SNOW-618)', () => {
  it('reports a forced reset distinctly from an elective one', async () => {
    stubDelete('onsuccess');

    await window.pwaResetLocalData(true);

    // db.js's Reset Required overlay passes forced=true. The two events
    // are what separate "the app broke and had to wipe itself" from "the
    // user chose to" in PostHog — collapsing them would make the health
    // signal unreadable.
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.reset.forced',
      expect.objectContaining({ ok: true, failed_steps: [] }),
    );
  });

  it('defaults to user-initiated', async () => {
    stubDelete('onsuccess');

    await window.pwaResetLocalData();

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith(
      'pwa.reset.user_initiated',
      expect.objectContaining({ ok: true }),
    );
  });

  it('survives telemetry being absent entirely', async () => {
    stubDelete('onsuccess');
    delete window.pwaTelemetry;

    // This file loads on admin pages too, where telemetry.js does not.
    await expect(window.pwaResetLocalData()).resolves.toEqual({
      ok: true,
      failed: [],
    });
  });
});

describe('the web-storage step (SNOW-618)', () => {
  it('clears both stores', async () => {
    stubDelete('onsuccess');
    localStorage.setItem('snowdesk.something', 'x');
    sessionStorage.setItem('snowdesk.other', 'y');

    await window.pwaResetLocalData();

    expect(localStorage.getItem('snowdesk.something')).toBeNull();
    expect(sessionStorage.getItem('snowdesk.other')).toBeNull();
  });

  it('reports a refused store as a failed step rather than tolerating it', async () => {
    stubDelete('onsuccess');
    vi.spyOn(Storage.prototype, 'clear').mockImplementation(() => {
      throw new DOMException('QuotaExceededError');
    });

    const result = await window.pwaResetLocalData();

    // Safari in private mode. Data the reset promised to clear is still on
    // the device, so reloading would land the user back in it.
    expect(result.ok).toBe(false);
    expect(result.failed).toContain('web_storage');
  });
});

describe('the IndexedDB fallback path (SNOW-618)', () => {
  it('uses the known DB-name list when indexedDB.databases is unavailable', async () => {
    const del = stubDelete('onsuccess');
    // Older Safari has no indexedDB.databases(); the module falls back to
    // KNOWN_DB_NAMES rather than skipping the step and calling it clean.
    vi.spyOn(indexedDB, 'databases').mockImplementation(undefined);
    Object.defineProperty(indexedDB, 'databases', {
      value: undefined,
      configurable: true,
    });

    const result = await window.pwaResetLocalData();

    expect(del).toHaveBeenCalledWith(DB_NAME);
    expect(result.ok).toBe(true);
  });
});
