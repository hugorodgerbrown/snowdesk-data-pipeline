/*
 * tests/js/test_pwa_dev_shell_toggle.js — Vitest unit tests for
 * static/js/pwa_dev_shell_toggle.js (SNOW-585).
 *
 * The module is a load-time IIFE that reads #sw-dev-shell-cache-optin from
 * the DOM once — the fixture must exist BEFORE the module is imported, and
 * each test needs a fresh IIFE run against its own fixture/localStorage/
 * window.pwaDb state, hence `vi.resetModules()` + `await import(...)` per
 * test (same pattern as tests/js/test_home_intro.js).
 *
 * `window.pwaDb` and `navigator.serviceWorker.controller` are stubbed
 * per-test — jsdom ships neither.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'pwa.devShellCache.optIn';
const META_KEY = 'sw.devShellCache';

function buildFixture() {
  document.body.innerHTML = `
    <input id="sw-dev-shell-cache-optin" type="checkbox">
  `;
}

/** Re-import the module fresh against the current fixture/localStorage state. */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/pwa_dev_shell_toggle.js');
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch (_e) {
    // Ignore.
  }
});

afterEach(() => {
  delete window.pwaDb;
  vi.unstubAllGlobals();
});

describe('initial checkbox state', () => {
  it('is unchecked when nothing is persisted (default opted-out)', async () => {
    buildFixture();
    await loadModule();
    expect(document.getElementById('sw-dev-shell-cache-optin').checked).toBe(false);
  });

  it('is checked when localStorage already carries the opt-in flag', async () => {
    localStorage.setItem(STORAGE_KEY, '1');
    buildFixture();
    await loadModule();
    expect(document.getElementById('sw-dev-shell-cache-optin').checked).toBe(true);
  });

  it('does nothing (does not throw) when the checkbox is absent from the page', async () => {
    document.body.innerHTML = '';
    await expect(loadModule()).resolves.toBeUndefined();
  });
});

describe('toggling the checkbox', () => {
  it('persists the opt-in flag to localStorage on check', async () => {
    buildFixture();
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change'));

    expect(localStorage.getItem(STORAGE_KEY)).toBe('1');
  });

  it('persists the opt-out flag to localStorage on uncheck', async () => {
    localStorage.setItem(STORAGE_KEY, '1');
    buildFixture();
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = false;
    checkbox.dispatchEvent(new Event('change'));

    expect(localStorage.getItem(STORAGE_KEY)).toBe('0');
  });

  it('mirrors the flag into meta:app via window.pwaDb.put', async () => {
    buildFixture();
    const put = vi.fn().mockResolvedValue(undefined);
    window.pwaDb = { put };
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change'));

    expect(put).toHaveBeenCalledWith('meta:app', { key: META_KEY, value: true });
  });

  it('posts a dev-shell-cache message to the controlling service worker', async () => {
    buildFixture();
    const postMessage = vi.fn();
    vi.stubGlobal('navigator', {
      ...navigator,
      serviceWorker: { controller: { postMessage } },
    });
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change'));

    expect(postMessage).toHaveBeenCalledWith({ type: 'dev-shell-cache', enabled: true });
  });

  it('does not throw when window.pwaDb is absent', async () => {
    buildFixture();
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = true;
    expect(() => checkbox.dispatchEvent(new Event('change'))).not.toThrow();
  });

  it('does not throw when no service worker controls the page', async () => {
    buildFixture();
    vi.stubGlobal('navigator', { ...navigator, serviceWorker: { controller: null } });
    await loadModule();
    const checkbox = document.getElementById('sw-dev-shell-cache-optin');

    checkbox.checked = true;
    expect(() => checkbox.dispatchEvent(new Event('change'))).not.toThrow();
  });
});
