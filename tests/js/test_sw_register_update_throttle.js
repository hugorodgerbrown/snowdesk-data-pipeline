/*
 * tests/js/test_sw_register_update_throttle.js — the visibilitychange
 * update-check throttle (SNOW-622).
 *
 * `registration.update()` is a network round trip for the worker script
 * every time it is called — `/sw.js` is served `Cache-Control: no-cache`,
 * so it revalidates unconditionally. `visibilitychange` fires on every tab
 * switch, every app switch on mobile, and every screen unlock, so a user
 * flicking between apps paid one request each way.
 *
 * Harness notes
 * -------------
 * `sw_register.js` registers the worker at import time, gated on a
 * `/api/pwa-config/` fetch and on `navigator.serviceWorker` existing —
 * neither of which jsdom provides. Both are stubbed before the dynamic
 * import, and the registration promise has to settle before the listener
 * this file is about exists, hence the `waitFor` below rather than a bare
 * import.
 *
 * The throttle is time-based, so `Date.now` is stubbed rather than waiting
 * out a real minute.
 */

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

/** Wall clock the module reads through `Date.now`, advanced by tests. */
let now = 1_000_000;

/** One entry per `registration.update()` the module issued. */
const updates = [];

const registration = {
  waiting: null,
  installing: null,
  addEventListener: () => {},
  update: vi.fn(() => {
    updates.push(now);
    return Promise.resolve();
  }),
};

vi.spyOn(Date, 'now').mockImplementation(() => now);

// jsdom has no serviceWorker container at all.
Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: null,
    register: vi.fn(() => Promise.resolve(registration)),
    getRegistrations: () => Promise.resolve([]),
    addEventListener: () => {},
    ready: Promise.resolve(registration),
  },
  configurable: true,
});

vi.stubGlobal(
  'fetch',
  vi.fn(async () =>
    // The config gate: `enabled` true and a real `sw_url`, so the module
    // proceeds to register rather than bailing.
    new Response(JSON.stringify({ enabled: true, sw_url: '/sw.js' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  ),
);

/**
 * Fire a `visibilitychange` with the given visibility state.
 *
 * @param {'visible'|'hidden'} state
 */
function visibilityChange(state) {
  Object.defineProperty(document, 'visibilityState', {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeAll(async () => {
  await import('../../static/js/sw_register.js');
  // The listener is attached inside the register() promise chain.
  for (let i = 0; i < 20 && !registration.update.mock; i += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => setTimeout(resolve, 20));
});

afterEach(() => {
  updates.length = 0;
  registration.update.mockClear();
});

describe('visibilitychange update check', () => {
  it('checks for an update when the tab becomes visible', () => {
    now += 10 * 60_000;

    visibilityChange('visible');

    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('does not check when the tab is hidden', () => {
    now += 10 * 60_000;

    visibilityChange('hidden');

    expect(registration.update).not.toHaveBeenCalled();
  });

  it('collapses a burst of tab switches into one check', () => {
    now += 10 * 60_000;

    // A user flicking between apps: each round trip was a full
    // revalidation of the worker script.
    for (let i = 0; i < 10; i += 1) {
      visibilityChange('hidden');
      visibilityChange('visible');
    }

    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('checks again once the interval has passed', () => {
    now += 10 * 60_000;
    visibilityChange('visible');
    expect(registration.update).toHaveBeenCalledTimes(1);

    // Just under a minute: still throttled.
    now += 59_000;
    visibilityChange('visible');
    expect(registration.update).toHaveBeenCalledTimes(1);

    // Past it: a deploy the user has not noticed for a minute is surfaced.
    now += 2_000;
    visibilityChange('visible');
    expect(registration.update).toHaveBeenCalledTimes(2);
  });
});
