/*
 * tests/js/test_pwa_launch_shell.js — Vitest unit tests for
 * static/js/pwa_launch_shell.js (SNOW-878).
 *
 * The launch shell is a full-screen overlay covering the whole app while
 * it boots, which makes "when does it go away" the only interesting
 * question about it — and the one with real consequences. A dismissal
 * that never fires turns a cosmetic feature into a total outage for the
 * users who installed the app; one that fires too early hands them the
 * blank map frame the overlay exists to hide.
 *
 * These are jsdom tests rather than Playwright ones on purpose: every
 * assertion here is about listener wiring, timers and class strings,
 * which is exactly what docs/client-side-tests.md puts in this layer.
 * Nothing below needs a browser to execute real MapLibre.
 *
 * The module is an IIFE that reads the DOM at import time, so each test
 * builds its fixture first and then imports fresh via vi.resetModules().
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SHELL_ID = 'pwa-launch-shell';
const LAUNCHING_CLASS = 'pwa-launching';
const DONE_CLASS = 'pwa-launch-shell--done';

/**
 * Render the overlay as includes/_pwa_launch_shell.html does, optionally
 * alongside a #map so the module takes the map-page branch.
 */
function buildFixture({ launching = true, withMap = false } = {}) {
  document.documentElement.className = launching ? LAUNCHING_CLASS : '';
  document.body.innerHTML = `
    <div id="${SHELL_ID}" class="pwa-launch-shell" role="status" aria-live="polite">
      <img class="pwa-launch-shell__mark" src="/static/icons/pwa/icon-192.png" alt="">
      <p class="pwa-launch-shell__label">Loading…</p>
    </div>
    ${withMap ? '<div id="map"></div>' : ''}
  `;
}

async function loadModule() {
  vi.resetModules();
  await import('../../static/js/pwa_launch_shell.js');
}

const shell = () => document.getElementById(SHELL_ID);

let readyStateSpy = null;

/**
 * Pin document.readyState for the duration of a test.
 *
 * jsdom reports 'complete' as soon as the fixture is in place, but a
 * deferred script in a real document runs during parsing — after
 * DOMContentLoaded's DOM is built, before load — where readyState is
 * 'interactive'. Left at the jsdom default, every no-map test would take
 * the already-loaded branch and assert nothing about the load listener.
 */
function mockReadyState(value) {
  readyStateSpy = vi.spyOn(document, 'readyState', 'get').mockReturnValue(value);
}

beforeEach(() => {
  vi.useFakeTimers();
  mockReadyState('interactive');
});

afterEach(() => {
  vi.useRealTimers();
  readyStateSpy?.mockRestore();
  readyStateSpy = null;
  document.documentElement.className = '';
  document.body.innerHTML = '';
});

describe('gating', () => {
  it('removes the overlay outright when this is not a launch', async () => {
    // A browser tab, or a second navigation inside an app session: the
    // inline gate never added the class. The overlay is display:none in
    // CSS, so this is about not leaving an aria-live region and a
    // failsafe animation in every page's DOM.
    buildFixture({ launching: false });
    await loadModule();
    expect(shell()).toBeNull();
  });

  it('leaves the overlay in place while the app is launching', async () => {
    buildFixture();
    await loadModule();
    expect(shell()).not.toBeNull();
    expect(shell().classList.contains(DONE_CLASS)).toBe(false);
  });

  it('does nothing when there is no overlay on the page', async () => {
    document.body.innerHTML = '<div id="map"></div>';
    await expect(loadModule()).resolves.not.toThrow();
  });
});

describe('dismissal on the map page', () => {
  it('waits for snowdesk:map-ready, not for load', async () => {
    // The load event is the wrong signal here: MapLibre fetches its
    // style and tiles with fetch(), which does not hold load back, so
    // load fires reliably while #map is still empty. Dismissing then
    // would reveal the blank frame the splash exists to cover.
    buildFixture({ withMap: true });
    await loadModule();

    window.dispatchEvent(new Event('load'));
    expect(shell().classList.contains(DONE_CLASS)).toBe(false);

    document.dispatchEvent(new CustomEvent('snowdesk:map-ready'));
    expect(shell().classList.contains(DONE_CLASS)).toBe(true);
  });

  it('removes the overlay from the DOM after the fade', async () => {
    buildFixture({ withMap: true });
    await loadModule();

    document.dispatchEvent(new CustomEvent('snowdesk:map-ready'));
    expect(shell()).not.toBeNull();

    vi.advanceTimersByTime(400);
    expect(shell()).toBeNull();
  });

  it('removes the overlay on transitionend without waiting out the timer', async () => {
    buildFixture({ withMap: true });
    await loadModule();

    document.dispatchEvent(new CustomEvent('snowdesk:map-ready'));
    shell().dispatchEvent(new Event('transitionend'));
    expect(shell()).toBeNull();
  });

  it('gives up after the timeout when the map never reports ready', async () => {
    // WebGL unavailable, the style request hanging offline, MapLibre
    // throwing during init: the event simply never comes, and without
    // this the user is left staring at a splash over an app that has
    // finished loading everything it is going to.
    buildFixture({ withMap: true });
    await loadModule();

    vi.advanceTimersByTime(4999);
    expect(shell().classList.contains(DONE_CLASS)).toBe(false);

    vi.advanceTimersByTime(1);
    expect(shell().classList.contains(DONE_CLASS)).toBe(true);
  });
});

describe('dismissal on a page without a map', () => {
  it('dismisses on load', async () => {
    buildFixture();
    await loadModule();

    expect(shell().classList.contains(DONE_CLASS)).toBe(false);
    window.dispatchEvent(new Event('load'));
    expect(shell().classList.contains(DONE_CLASS)).toBe(true);
  });

  it('dismisses immediately when load has already fired', async () => {
    // A deferred script normally runs before load, but a cached page
    // restored from bfcache can execute it after. Adding a listener then
    // would wait for an event that has already been and gone.
    buildFixture();
    mockReadyState('complete');
    await loadModule();

    expect(shell().classList.contains(DONE_CLASS)).toBe(true);
  });
});

describe('idempotence', () => {
  it('survives every signal firing', async () => {
    // All three race by design and none is cancelled, so the handler has
    // to tolerate being called repeatedly against a node it has already
    // removed.
    buildFixture({ withMap: true });
    await loadModule();

    document.dispatchEvent(new CustomEvent('snowdesk:map-ready'));
    window.dispatchEvent(new Event('load'));
    vi.advanceTimersByTime(10000);

    expect(shell()).toBeNull();
  });
});
