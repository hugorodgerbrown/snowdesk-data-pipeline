/*
 * tests/js/test_sw_register_silent_update.js — a routine service-worker
 * update is applied without asking anyone (SNOW-1025).
 *
 * Before SNOW-1025 a waiting worker revealed the "Update available"
 * banner, and nothing else could activate it: `sw.js` never calls
 * `skipWaiting()`. The shell hash covers every `static/js` file, so nearly
 * every deploy asked every user to press Reload. Now the waiting worker is
 * sent SKIP_WAITING the next time the page is hidden, and the
 * `controllerchange` that follows does not reload the page.
 *
 * Harness notes
 * -------------
 * `sw_register.js` is a load-time IIFE whose globals are non-configurable,
 * so it is imported once. The registration it gets back already has a
 * waiting worker and the page already has a controller: "an update
 * installed while you were away". `document.visibilityState` is redefined
 * per test, and `visibilitychange` is dispatched by hand, as a tab or app
 * switch would fire it.
 *
 * jsdom does not implement `location.reload()`, so `location` is stubbed
 * with a spy for the test that asserts the page did NOT reload.
 */

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** Messages posted to the waiting worker. */
const posted = [];

const waitingWorker = {
  state: 'installed',
  postMessage: (msg) => posted.push(msg),
};

const registration = {
  waiting: waitingWorker,
  installing: null,
  addEventListener: () => {},
  update: vi.fn(() => Promise.resolve()),
};

/** `navigator.serviceWorker` listeners, so a test can fire `controllerchange`. */
const containerListeners = {};

/** Every telemetry event the module emitted. */
const emitted = [];

/** The `warmCache` run the stubbed worker is holding open, if any. */
let warmRequestId = null;

const controller = {
  postMessage: (data) => {
    if (data && data.type === 'warm-cache') warmRequestId = data.requestId;
  },
};

Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: controller,
    register: vi.fn(() => Promise.resolve(registration)),
    getRegistration: () => Promise.resolve(registration),
    getRegistrations: () => Promise.resolve([]),
    addEventListener: (type, fn) => {
      (containerListeners[type] = containerListeners[type] || []).push(fn);
    },
    ready: Promise.resolve(registration),
  },
  configurable: true,
});

vi.stubGlobal(
  'fetch',
  vi.fn(
    async () =>
      new Response(JSON.stringify({ kill: false, sw_url: '/sw.js' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  ),
);

/**
 * Put the page in `state` and fire the event a tab or app switch fires.
 *
 * @param {'visible' | 'hidden'} state
 */
function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

/** Let the module's awaits (getRegistration and friends) run. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

/**
 * Deliver a message from the worker to the page, as the browser does.
 *
 * @param {object} data
 */
function fromWorker(data) {
  (containerListeners.message || []).forEach((fn) => fn({ data: data }));
}

beforeAll(async () => {
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';
  window.pwaTelemetry = { emit: (event, props) => emitted.push({ event, props }) };
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });

  await import('../../static/js/sw_register.js');
  await vi.waitFor(() => expect(navigator.serviceWorker.register).toHaveBeenCalled());
  await settle();
});

afterEach(() => {
  posted.length = 0;
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
});

describe('a worker that installed while the page was open', () => {
  it('shows no banner and is left alone while the page is visible', async () => {
    await settle();

    expect(document.getElementById('sw-update-banner').classList.contains('hidden')).toBe(true);
    expect(posted).toEqual([]);
  });

  it('is applied the moment the page is hidden', async () => {
    setVisibility('hidden');
    await settle();

    expect(posted).toEqual([{ type: 'SKIP_WAITING' }]);
  });

  it('is not applied when the page comes back into view', async () => {
    setVisibility('visible');
    await settle();

    expect(posted).toEqual([]);
  });
});

describe('a basemap download in flight', () => {
  it('holds the update back until the download settles', async () => {
    const run = window.pwaWarmCache(['https://tiles.example/1.pbf'], {});
    await vi.waitFor(() => expect(warmRequestId).not.toBeNull());

    // Hidden mid-download: activating now would retire the worker that is
    // doing it.
    setVisibility('hidden');
    await settle();
    expect(posted).toEqual([]);

    // The download finishes while the page is still hidden, which releases
    // the held update.
    fromWorker({ type: 'warm-cache-done', requestId: warmRequestId, ok: 1, failed: 0 });
    await run;
    await settle();

    expect(posted).toEqual([{ type: 'SKIP_WAITING' }]);
  });
});

describe('the controllerchange a silent activation fires', () => {
  it('does not reload, records the update and takes down a stale banner', async () => {
    setVisibility('hidden');
    await settle();
    expect(posted).toEqual([{ type: 'SKIP_WAITING' }]);

    const banner = document.getElementById('sw-update-banner');
    banner.classList.remove('hidden');
    const reload = vi.fn();
    vi.stubGlobal('location', { href: 'http://localhost/', reload: reload });
    emitted.length = 0;

    try {
      containerListeners.controllerchange.forEach((fn) => fn());
      await settle();

      expect(reload).not.toHaveBeenCalled();
      expect(emitted.map((e) => e.event)).toEqual(['pwa.sw.update_applied']);
      expect(banner.classList.contains('hidden')).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('is not recorded as an update when nothing was posted', async () => {
    // A first-install claim fires the same event.
    emitted.length = 0;
    containerListeners.controllerchange.forEach((fn) => fn());
    await settle();

    expect(emitted).toEqual([]);
  });
});
