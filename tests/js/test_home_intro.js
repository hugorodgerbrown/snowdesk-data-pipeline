/*
 * tests/js/test_home_intro.js — Vitest unit tests for static/js/home_intro.js
 * (SNOW-314, SNOW-496).
 *
 * Ports tests/e2e/test_home_intro.py's three cases onto the Vitest + jsdom
 * harness. home_intro.js's own dismiss handling is thin — the actual hide
 * + localStorage-persist for the "×" click is done by overlays.js's shared
 * delegated handler (SNOW-486), so both modules are loaded here, matching
 * production load order (overlays.js loads on every public page before
 * home_intro.js).
 *
 * home_intro.js reads `#home-intro` and returns early if it's absent, so
 * the fixture (mirroring the shape rendered by
 * public/templates/public/partials/_map_embed.html) must exist BEFORE the
 * module is imported. Both modules are dynamically imported per test (via
 * `vi.resetModules()` + `await import(...)`) so each test gets a fresh
 * IIFE run against its own fixture/location/localStorage state.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'snowdesk.home.intro';
const DISMISSED_VALUE = 'dismissed';

function buildFixture() {
  document.body.innerHTML = `
    <div id="home-intro" data-overlay data-overlay-persist="snowdesk.home.intro=dismissed">
      <div class="home-intro-card">
        <button id="home-intro-dismiss" type="button" data-action="dismiss">
          Explore the map
        </button>
      </div>
    </div>
  `;
}

/** Re-import overlays.js + home_intro.js fresh against the current fixture/location. */
async function loadModules() {
  vi.resetModules();
  await import('../../static/js/overlays.js');
  await import('../../static/js/home_intro.js');
}

beforeEach(() => {
  try {
    localStorage.clear();
  } catch (_e) {
    // Ignore.
  }
  window.location.hash = '';
});

afterEach(() => {
  window.location.hash = '';
});

describe('dismiss', () => {
  it('hides the overlay and persists the dismissed flag to localStorage', async () => {
    buildFixture();
    await loadModules();
    const overlay = document.getElementById('home-intro');

    expect(overlay.hasAttribute('hidden')).toBe(false);

    document.getElementById('home-intro-dismiss').click();

    expect(overlay.hasAttribute('hidden')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });
});

describe('reload with the dismissed flag set', () => {
  it('starts hidden without needing another dismiss', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
  });
});

describe('/#about hash restore', () => {
  it('reopens the overlay on load regardless of the persisted dismissed state', async () => {
    localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
    window.location.hash = '#about';
    buildFixture();
    await loadModules();

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });

  it('reopens the overlay on a later hashchange to #about', async () => {
    buildFixture();
    await loadModules();
    document.getElementById('home-intro-dismiss').click();
    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);

    window.location.hash = '#about';
    window.dispatchEvent(new Event('hashchange'));

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(false);
  });
});

describe('Escape key', () => {
  it('hides the overlay and persists the dismissed flag', async () => {
    buildFixture();
    await loadModules();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));

    expect(document.getElementById('home-intro').hasAttribute('hidden')).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(DISMISSED_VALUE);
  });
});
