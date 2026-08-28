/*
 * tests/js/test_pwa_offline.js — Vitest unit tests for
 * static/js/pwa_offline.js's banner reveal/hide rules (C2, 2026-08-03 JS
 * review).
 *
 * Two behaviours are pinned here:
 *
 *   1. A caller-initiated abort is not a connectivity failure. The sign-in
 *      page starts the WebAuthn conditional ceremony on email-input focus
 *      and aborts it on the first keystroke, so a fully-online user typing
 *      their address used to pin the banner open for the life of the page.
 *   2. A successful same-origin response while ``navigator.onLine`` is true
 *      re-hides a revealed banner. Without it the only hide path is the
 *      ``online`` event, which never fires when connectivity never changed.
 *
 * ``pwa_offline.js`` is an IIFE that reads ``#pwa-offline-banner`` and wraps
 * ``window.fetch`` at import time, so every test builds its fixture and
 * installs its own ``window.fetch`` mock BEFORE importing the module, then
 * re-imports it fresh via ``vi.resetModules()`` (same pattern as
 * test_home_intro.js). Re-importing per test also stops each run's wrapper
 * stacking on the previous one's.
 *
 * ``window.pwaDb`` is left undefined for the banner tests: the persistence
 * and sync-log helpers all guard on it and return early, which keeps those
 * tests on the banner behaviour alone. The sync-log block at the foot of
 * the file stubs it, since what it asserts is what gets written.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const BANNER_ID = 'pwa-offline-banner';

/**
 * Render the banner markup from templates/includes/_offline_banner.html.
 *
 * SNOW-742 added the latched variants and the two mode controls. They are
 * mirrored here rather than only in the template because the module toggles
 * them by ``data-role``, so a fixture missing one would silently make those
 * assertions vacuous — the toggle helper skips a role it cannot find.
 */
function buildFixture() {
  document.body.innerHTML = `
    <details id="${BANNER_ID}" data-role="offline-freshness" role="status" class="hidden">
      <summary>
        <span data-role="offline-message">Offline — last synced</span>
        <span data-role="latched-message" class="hidden">Offline mode — last synced</span>
        <span data-role="synced-at">—</span>
      </summary>
      <div>
        <p data-role="offline-explainer">Lost contact.</p>
        <p data-role="latched-explainer" class="hidden">Stopped trying.</p>
        <button type="button" data-role="reconnect" class="hidden">Try reconnecting</button>
        <button type="button" data-role="stay-offline">Stay offline</button>
      </div>
    </details>
  `;
}

/** Whether the element carrying ``data-role`` is currently visible. */
function roleShown(role) {
  const el = banner().querySelector(`[data-role="${role}"]`);
  return !!el && !el.classList.contains('hidden');
}

/**
 * Install a service-worker stub that records what the page posts to the
 * worker and lets a test push a worker-originated message back.
 *
 * @returns {{posted: object[], emit: (data: object) => void}}
 */
function stubServiceWorker() {
  const posted = [];
  const listeners = [];
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: {
      controller: { postMessage: (data) => posted.push(data) },
      addEventListener: (type, handler) => {
        if (type === 'message') listeners.push(handler);
      },
    },
  });
  return {
    posted,
    emit: (data) => listeners.forEach((handler) => handler({ data })),
  };
}

/** The banner element, for readability at the assertion site. */
function banner() {
  return document.getElementById(BANNER_ID);
}

/** Whether the banner is currently revealed. */
function bannerShown() {
  return !banner().classList.contains('hidden');
}

/**
 * Re-import pwa_offline.js fresh against the current fixture and fetch
 * mock, awaiting the microtasks its async ``init()`` defers past.
 */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/pwa_offline.js');
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

/**
 * An Error shaped like the DOMException a real ``AbortController`` raises
 * when it cancels an in-flight fetch.
 *
 * @returns {Error}
 */
function abortError() {
  const err = new Error('The operation was aborted.');
  err.name = 'AbortError';
  return err;
}

/**
 * A minimal same-origin 200 stand-in carrying the three fields
 * ``absorbFreshness`` reads: headers, resolved URL, status.
 *
 * @param {string} path
 * @returns {object}
 */
function okResponse(path = '/api/ratings/') {
  return {
    headers: { get: () => null },
    url: `${window.location.origin}${path}`,
    status: 200,
  };
}

beforeEach(() => {
  buildFixture();
});

describe('offline banner reveal', () => {
  it('does not reveal the banner when a fetch is aborted', async () => {
    const err = abortError();
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/account/passkey/auth/request/')).rejects.toBe(err);

    expect(bannerShown()).toBe(false);
  });

  it('still reveals the banner on a genuine network failure', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);

    expect(bannerShown()).toBe(true);
  });
});

describe('offline banner recovery', () => {
  it('re-hides a revealed banner on a successful same-origin response while online', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValueOnce(okResponse());
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);
    expect(bannerShown()).toBe(true);

    await window.fetch('/api/ratings/');

    expect(bannerShown()).toBe(false);
  });
});


// ---------------------------------------------------------------------------
// SNOW-742 — the offline latch, as the page sees it
// ---------------------------------------------------------------------------
//
// The banner used to key off ``navigator.onLine`` alone. That is exactly the
// signal which stays TRUE on the Underground — the radio is attached, there is
// simply no route — so the state these tests cover is one the old banner could
// not represent at all: latched, and online as far as the platform knows.

describe('offline latch banner (SNOW-742)', () => {
  it('shows the latched variant when the worker announces a latch, even while onLine', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(bannerShown()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Revealed despite navigator.onLine being true throughout — the case the
    // old onLine-only banner was blind to.
    expect(window.navigator.onLine).toBe(true);
    expect(bannerShown()).toBe(true);
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
    expect(roleShown('latched-explainer')).toBe(true);
  });

  it('offers only the control that does something in each state', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    // Struggling but not latched: the app is still trying, so the useful
    // action is to tell it to stop.
    await expect(window.fetch('/api/ratings/')).rejects.toThrow();
    expect(roleShown('stay-offline')).toBe(true);
    expect(roleShown('reconnect')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Latched: the app has stopped, so the useful action is the way back.
    expect(roleShown('reconnect')).toBe(true);
    expect(roleShown('stay-offline')).toBe(false);
  });

  it('asks the worker to unlatch when the user taps Try reconnecting', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    banner().querySelector('[data-role="reconnect"]').click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(roleShown('offline-message')).toBe(true);
  });

  it('asks the worker to latch when the user taps Stay offline', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    banner().querySelector('[data-role="stay-offline"]').click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline' });
    expect(bannerShown()).toBe(true);
  });

  it('hides the banner again once the worker reports it has unlatched', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(bannerShown()).toBe(true);

    // A probe found a route again.
    sw.emit({ type: 'network-mode', mode: 'auto' });

    expect(bannerShown()).toBe(false);
  });
});


// ---------------------------------------------------------------------------
// The sync log's write side
// ---------------------------------------------------------------------------
//
// The panel on /account/settings/ answers "has this device actually reached
// the server", so a row only earns its place if the reader would recognise it
// as the app fetching something. telemetry.js flushes its own buffer every 30
// seconds and on every lifecycle event, which filled the panel with rows
// describing nothing the reader did.

describe('sync-log write filter', () => {
  /**
   * Stub the three ``window.pwaDb`` methods pwa_offline.js touches and
   * record every path handed to ``appendSyncLog``.
   *
   * @returns {string[]} live array of logged paths
   */
  function stubDb() {
    const logged = [];
    window.pwaDb = {
      get: async () => undefined,
      put: async () => {},
      appendSyncLog: async (entry) => {
        logged.push(entry.path);
      },
    };
    return logged;
  }

  afterEach(() => {
    delete window.pwaDb;
  });

  it('does not log a telemetry flush', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/telemetry'));
    await loadModule();

    await window.fetch('/api/telemetry', { method: 'POST' });

    expect(logged).toEqual([]);
  });

  it('still logs a request the reader would recognise', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/ratings/'));
    await loadModule();

    await window.fetch('/api/ratings/');

    expect(logged).toEqual(['/api/ratings/']);
  });
});
