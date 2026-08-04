/*
 * tests/js/test_sw_register_update_throttle.js — the visibilitychange
 * update-check throttle (SNOW-622) and the shared update-banner pair
 * (SNOW-623).
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
  // SNOW-623: the module resolves `#sw-update-banner` ONCE at load, and
  // synthesises an inline-styled admin fallback when it finds none — so the
  // public partial's markup has to be in place before the import. That also
  // fixes which idiom this file can exercise: class-toggled, not fallback.
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';

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

describe('window.pwaUpdateBanner (SNOW-623)', () => {
  /**
   * The element the module resolved at import — not a fresh one.
   *
   * Looked up per call rather than once at collection time: the fixture is
   * written inside `beforeAll`, which runs after `describe` bodies.
   *
   * @returns {HTMLElement}
   */
  const el = () => document.getElementById('sw-update-banner');

  it('is published for the version-check path to reach', () => {
    // pwa_version_check.js reveals this same element on a confirmed
    // server-header drift, and used to carry its own copy of the fork.
    expect(typeof window.pwaUpdateBanner?.reveal).toBe('function');
    expect(typeof window.pwaUpdateBanner?.hide).toBe('function');
  });

  it('reveals and hides a class-toggled banner', () => {
    window.pwaUpdateBanner.reveal();
    expect(el().classList.contains('hidden')).toBe(false);

    window.pwaUpdateBanner.hide();
    expect(el().classList.contains('hidden')).toBe(true);
  });

  it('is idempotent — a second reveal changes nothing', () => {
    window.pwaUpdateBanner.reveal();
    window.pwaUpdateBanner.reveal();

    expect(el().classList.contains('hidden')).toBe(false);
  });

  it('never flips which idiom the markup uses', () => {
    // The admin fallback this file synthesises is inline-styled; the
    // public partial is class-toggled. Revealing one with the other's
    // idiom leaves it invisible, which is the fork both owners had to
    // reproduce and only one now does.
    window.pwaUpdateBanner.reveal();

    expect(el().style.display).toBe('');
  });

  it('is frozen, so a third owner cannot quietly replace the reveal', () => {
    expect(Object.isFrozen(window.pwaUpdateBanner)).toBe(true);
  });
});
