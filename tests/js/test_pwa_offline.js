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
 * ``window.pwaDb`` is left undefined throughout: the persistence and
 * sync-log helpers all guard on it and return early, which keeps these
 * tests on the banner behaviour alone.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const BANNER_ID = 'pwa-offline-banner';

/** Render the banner markup from templates/includes/_offline_banner.html. */
function buildFixture() {
  document.body.innerHTML = `
    <details id="${BANNER_ID}" data-role="offline-freshness" role="status" class="hidden">
      <summary>
        <span data-role="offline-message">Offline — last synced</span>
        <span data-role="synced-at">—</span>
      </summary>
    </details>
  `;
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
