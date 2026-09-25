/*
 * tests/js/test_sw_register_matched_update.js — a waiting worker that
 * already holds this page's build takes over at once (SNOW-1027).
 *
 * SNOW-1025 applied every waiting worker the next time the page was hidden.
 * On a fresh tab that is the wrong rule: opening the tab is what installs
 * the new worker. The navigation is network-first, so the page is already
 * the new build and only the old worker is out of date, and the two stayed
 * mismatched until the user switched away, which on staging meant a fresh
 * tab sat with one worker running and one waiting.
 *
 * The page now names its shell in `<meta name="pwa-shell">`, and a waiting
 * worker reporting the same name over `shell-identity` gets SKIP_WAITING
 * immediately, visible or not. Anything else keeps the hide-to-apply rule,
 * which test_sw_register_silent_update.js covers.
 *
 * Harness notes
 * -------------
 * `sw_register.js` reads the meta once, at import, and its globals are
 * non-configurable, so this file imports it once with the meta in place.
 * Each test installs a fresh fake worker through `updatefound`, the path a
 * fresh tab takes, and drives it to `installed`. The page stays visible
 * throughout unless a test says otherwise.
 */

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const PAGE_SHELL = 'snowdesk-shell-newbuild0001';

/** Registration listeners, so a test can fire `updatefound`. */
const registrationListeners = {};

const registration = {
  waiting: null,
  installing: null,
  addEventListener: (type, fn) => {
    (registrationListeners[type] = registrationListeners[type] || []).push(fn);
  },
  update: vi.fn(() => Promise.resolve()),
};

/** The `warmCache` run the stubbed controller is holding open, if any. */
let warmRequestId = null;

/**
 * What the controlling worker says about every other window, or null for a
 * worker that never answers `activation-check`.
 */
let activationReply = { othersVisible: false, warming: false };

/** `navigator.serviceWorker` listeners, so a test can deliver messages. */
const containerListeners = {};

Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: {
      postMessage: (data, transfer) => {
        if (data && data.type === 'warm-cache') warmRequestId = data.requestId;
        if (data && data.type === 'activation-check' && activationReply) {
          transfer[0].postMessage({ type: 'activation-check', ...activationReply });
        }
      },
    },
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
 * A stand-in ServiceWorker that answers `shell-identity` with `shell` (or
 * never, for `null`) and records every SKIP_WAITING it is sent.
 *
 * @param {string | null} shell
 * @returns {{state: string, skipped: number, postMessage: Function,
 *   addEventListener: Function, removeEventListener: Function,
 *   moveTo: (next: string) => void}}
 */
function fakeWorker(shell) {
  const listeners = [];
  const worker = {
    state: 'installing',
    skipped: 0,
    postMessage: (data, transfer) => {
      if (data && data.type === 'SKIP_WAITING') worker.skipped += 1;
      if (data && data.type === 'shell-identity' && shell !== null) {
        transfer[0].postMessage({ type: 'shell-identity', cache: shell });
      }
    },
    addEventListener: (type, fn) => {
      if (type === 'statechange') listeners.push(fn);
    },
    removeEventListener: () => {},
    moveTo: (next) => {
      worker.state = next;
      listeners.forEach((fn) => fn());
    },
  };
  return worker;
}

/**
 * Run a fresh install through to "waiting", as the browser does on a fresh
 * tab: `updatefound`, then `installing` → `installed`.
 *
 * @param {ReturnType<typeof fakeWorker>} worker
 */
function install(worker) {
  registration.installing = worker;
  registrationListeners.updatefound.forEach((fn) => fn());
  registration.installing = null;
  registration.waiting = worker;
  worker.moveTo('installed');
}

/** Let the port reply and the registration read settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

beforeAll(async () => {
  document.head.innerHTML = `<meta name="pwa-shell" content="${PAGE_SHELL}">`;
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });

  await import('../../static/js/sw_register.js');
  await vi.waitFor(() => expect(registrationListeners.updatefound).toBeDefined());
});

afterEach(() => {
  registration.waiting = null;
  activationReply = { othersVisible: false, warming: false };
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
});

describe('a waiting worker that holds this page’s shell', () => {
  it('takes over at once, while the page is visible', async () => {
    const worker = fakeWorker(PAGE_SHELL);

    install(worker);
    await settle();

    expect(worker.skipped).toBe(1);
  });

  it('waits for a basemap download, then takes over while still visible', async () => {
    const run = window.pwaWarmCache(['https://tiles.example/1.pbf'], {});
    await vi.waitFor(() => expect(warmRequestId).not.toBeNull());
    const worker = fakeWorker(PAGE_SHELL);

    install(worker);
    await settle();
    expect(worker.skipped).toBe(0);

    (containerListeners.message || []).forEach((fn) =>
      fn({ data: { type: 'warm-cache-done', requestId: warmRequestId, ok: 1, failed: 0 } }),
    );
    await run;
    await settle();

    expect(worker.skipped).toBe(1);
  });
});

describe('another window (the Codex P1 on #978)', () => {
  // Matching THIS page's shell says nothing about other windows, and
  // activation reaches them all.
  it('holds a matched worker back while another window is on screen', async () => {
    activationReply = { othersVisible: true, warming: false };
    const worker = fakeWorker(PAGE_SHELL);

    install(worker);
    await settle();

    expect(worker.skipped).toBe(0);
  });

  it('holds a matched worker back while another window is downloading', async () => {
    activationReply = { othersVisible: false, warming: true };
    const worker = fakeWorker(PAGE_SHELL);

    install(worker);
    await settle();

    expect(worker.skipped).toBe(0);
  });
});

describe('a waiting worker that does not', () => {
  it('waits for the page to be hidden when it holds another shell', async () => {
    // The page was served by the old worker (offline, typically) and may
    // still read the old shell cache that activation would sweep.
    const worker = fakeWorker('snowdesk-shell-somethingelse');

    install(worker);
    await settle();
    expect(worker.skipped).toBe(0);

    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
    await settle();

    expect(worker.skipped).toBe(1);
  });

  it('waits when the worker never answers', async () => {
    const worker = fakeWorker(null);

    vi.useFakeTimers();
    try {
      install(worker);
      await vi.advanceTimersByTimeAsync(2100);
    } finally {
      vi.useRealTimers();
    }
    await settle();

    expect(worker.skipped).toBe(0);
  });

  it('is not applied early just because an older checked worker matched', async () => {
    // A newer install replaced the one that was checked. The newer one was
    // never asked, so it gets the hide-to-apply rule.
    const checked = fakeWorker(PAGE_SHELL);
    const newer = fakeWorker('snowdesk-shell-evennewer');
    // `checked` answers only once `newer` is already the one waiting.
    const answer = checked.postMessage;
    checked.postMessage = (data, transfer) => {
      if (data && data.type === 'shell-identity') registration.waiting = newer;
      answer(data, transfer);
    };

    install(checked);
    await settle();

    expect(checked.skipped).toBe(0);
    expect(newer.skipped).toBe(0);
  });
});
