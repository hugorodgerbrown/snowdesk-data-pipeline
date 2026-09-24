/*
 * tests/js/test_sw_register_update_gate.js — when the update banner is
 * allowed to appear (SNOW-952, SNOW-1025).
 *
 * The banner exists for one case: a service worker stuck on an out-of-date
 * shell, which the browser cannot fix without help. A routine update is
 * applied silently, so the banner must not appear for it. That would put
 * back the interruption SNOW-1025 removed, which appeared after nearly every
 * deploy because the shell hash covers every `static/js` file.
 *
 * `window.pwaUpdateBanner.reveal()` runs two gates in series:
 *
 *   1. `shellIsStale` (SNOW-952): does the controlling worker hold a
 *      different shell cache from the one `/api/version` names?
 *   2. `workerIsStuck` (SNOW-1025): after `registration.update()`, is there
 *      no worker that will install and be applied silently?
 *
 * Harness notes
 * -------------
 * `sw_register.js` is a load-time IIFE that resolves `#sw-update-banner`
 * once and defines non-configurable globals, so it can be imported only
 * once per jsdom window. The controller's reply, the server's verdict and
 * the registration's state are therefore mutable module-level stubs that
 * each test sets before calling `window.pwaUpdateBanner.reveal()`.
 *
 * The decision is memoised against the server shell it answered, so every
 * test that expects a fresh evaluation names a distinct shell. That is the
 * memo's contract, not a workaround.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** What the stubbed worker answers `shell-identity` with, or null for silence. */
let workerReply = null;

/** One entry per message the page posted to the controller. */
const posted = [];

/** The body `/api/version` returns. */
let verdict = null;

/** `navigator.serviceWorker` listeners, so a test can fire `controllerchange`. */
const containerListeners = {};

const controller = {
  postMessage: (data, transfer) => {
    posted.push(data);
    const port = transfer && transfer[0];
    if (!port || !workerReply) return;
    // The real worker replies down the transferred port; jsdom's
    // MessageChannel delivers that asynchronously, as the browser does.
    port.postMessage(workerReply);
  },
};

/**
 * A stand-in ServiceWorker whose state the test drives.
 *
 * @param {string} state
 * @returns {{state: string, postMessage: Function, addEventListener: Function,
 *   removeEventListener: Function, moveTo: (next: string) => void}}
 */
function fakeWorker(state) {
  const listeners = new Set();
  const worker = {
    state: state,
    postMessage: vi.fn(),
    addEventListener: (type, fn) => {
      if (type === 'statechange') listeners.add(fn);
    },
    removeEventListener: (type, fn) => {
      if (type === 'statechange') listeners.delete(fn);
    },
    moveTo: (next) => {
      worker.state = next;
      listeners.forEach((fn) => fn());
    },
  };
  return worker;
}

/**
 * What `registration.update()` does in the current test: nothing (no new
 * worker), or install a worker the test then settles.
 *
 * @type {() => Promise<void>}
 */
let onUpdate = () => Promise.resolve();

const registration = {
  waiting: null,
  installing: null,
  addEventListener: () => {},
  update: vi.fn(() => onUpdate()),
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
      new Response(JSON.stringify({ enabled: true, sw_url: '/sw.js' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
  ),
);

/** @returns {HTMLElement} the banner the module resolved at import. */
const banner = () => document.getElementById('sw-update-banner');

/** @returns {boolean} whether the banner is currently on screen. */
const isRevealed = () => !banner().classList.contains('hidden');

/**
 * Stand the harness up as "this device holds an old shell": the server
 * names `server`, the worker answers `held`.
 *
 * @param {string} server
 * @param {string} held
 */
function staleShell(server, held) {
  verdict = { current: 'bbbbbbb2222', release: 'v30', shell: server };
  workerReply = { type: 'shell-identity', cache: held };
}

beforeAll(async () => {
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';
  window.pwaVersionInfo = { verified: () => Promise.resolve(verdict) };

  await import('../../static/js/sw_register.js');
  await new Promise((resolve) => setTimeout(resolve, 20));
});

beforeEach(() => {
  posted.length = 0;
  registration.waiting = null;
  registration.installing = null;
  registration.update.mockClear();
  onUpdate = () => Promise.resolve();
  banner().classList.add('hidden');
});

describe('gate one: is the shell stale?', () => {
  it('stays silent when the worker holds the shell the server would serve', async () => {
    // A deploy that changed no shell source: nothing for a reload to fetch.
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-same' };
    workerReply = { type: 'shell-identity', cache: 'shell-same' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(false);
    expect(isRevealed()).toBe(false);
    // Gate two is never asked.
    expect(registration.update).not.toHaveBeenCalled();
  });

  it('stays silent when nothing is controlling the page', async () => {
    // No controller means no cached shell, so nothing can be out of date.
    // This is the one unknown that must NOT fail open.
    const container = navigator.serviceWorker;
    Object.defineProperty(container, 'controller', { value: null, configurable: true });
    staleShell('shell-nocontroller', '');

    try {
      expect(await window.pwaUpdateBanner.reveal()).toBe(false);
    } finally {
      Object.defineProperty(container, 'controller', {
        value: controller,
        configurable: true,
      });
    }
  });
});

describe('gate two: can the browser fix it alone?', () => {
  it('stays silent while a new worker is waiting to be applied', async () => {
    // The minutes after an ordinary deploy: the new worker has installed
    // and is waiting for the page to be hidden. That is SNOW-1025's
    // screenshot: a routine update that asked the user to act.
    staleShell('shell-waiting', 'shell-old');
    registration.waiting = fakeWorker('installed');

    expect(await window.pwaUpdateBanner.reveal()).toBe(false);
    expect(isRevealed()).toBe(false);
  });

  it('stays silent when the update check installs a worker', async () => {
    staleShell('shell-installs', 'shell-old');
    const installing = fakeWorker('installing');
    onUpdate = () => {
      registration.installing = installing;
      return Promise.resolve();
    };

    const pending = window.pwaUpdateBanner.reveal();
    await vi.waitFor(() => expect(registration.update).toHaveBeenCalled());
    installing.moveTo('installed');

    expect(await pending).toBe(false);
    expect(isRevealed()).toBe(false);
  });

  it('reveals when the new worker fails to install', async () => {
    // The case the banner exists for. `install` precaches with one atomic
    // `cache.addAll`, so one bad entry rejects it and the worker goes
    // redundant. Nothing will ever reach "waiting" without a reload.
    staleShell('shell-redundant', 'shell-old');
    const installing = fakeWorker('installing');
    onUpdate = () => {
      registration.installing = installing;
      return Promise.resolve();
    };

    const pending = window.pwaUpdateBanner.reveal();
    await vi.waitFor(() => expect(registration.update).toHaveBeenCalled());
    installing.moveTo('redundant');

    expect(await pending).toBe(true);
    expect(isRevealed()).toBe(true);
  });

  it('reveals when no new worker appears at all', async () => {
    staleShell('shell-nothing', 'shell-old');

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('reveals when the update check itself fails', async () => {
    // The server has just answered /api/version, so this is not a device
    // that is simply offline: sw.js could not be fetched or evaluated.
    staleShell('shell-update-rejects', 'shell-old');
    onUpdate = () => Promise.reject(new TypeError('script evaluation failed'));

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });

  it('reveals when an install is still running after the budget', async () => {
    staleShell('shell-slow', 'shell-old');
    const installing = fakeWorker('installing');
    onUpdate = () => {
      registration.installing = installing;
      return Promise.resolve();
    };

    vi.useFakeTimers();
    try {
      const pending = window.pwaUpdateBanner.reveal();
      // In two steps: the install budget only starts once the worker's
      // reply and the update check have settled.
      await vi.advanceTimersByTimeAsync(100);
      await vi.advanceTimersByTimeAsync(30100);

      expect(await pending).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('what the gates do when they cannot tell', () => {
  it('treats a worker that never answers as stale, and still asks gate two', async () => {
    // A worker predating `shell-identity`, or one wedged in a long
    // waitUntil. The read is bounded, so it resolves instead of hanging.
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-silent' };
    workerReply = null;
    registration.waiting = fakeWorker('installed');

    vi.useFakeTimers();
    try {
      const pending = window.pwaUpdateBanner.reveal();
      await vi.advanceTimersByTimeAsync(2100);

      // Stale, but a worker is waiting, so still not stuck.
      expect(await pending).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it('treats a server that names no shell as stale', async () => {
    verdict = { current: 'bbbbbbb2222', release: 'v30' };
    workerReply = { type: 'shell-identity', cache: 'shell-held' };

    // No worker appears, so the stale answer carries through.
    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });
});

describe('the memo', () => {
  it('asks the worker and the registration once per server shell', async () => {
    // pwa_version_check.js re-offers the banner for every response that
    // replays a drifting version header, which on a cached feed is many.
    staleShell('shell-memo', 'shell-old');

    await window.pwaUpdateBanner.reveal();
    await window.pwaUpdateBanner.reveal();
    await window.pwaUpdateBanner.reveal();

    expect(posted.filter((m) => m.type === 'shell-identity')).toHaveLength(1);
    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('re-evaluates when a second deploy names a different shell', async () => {
    verdict = { current: 'ccc', release: 'v31', shell: 'shell-first' };
    workerReply = { type: 'shell-identity', cache: 'shell-first' };
    expect(await window.pwaUpdateBanner.reveal()).toBe(false);

    verdict = { current: 'ddd', release: 'v32', shell: 'shell-second' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });

  it('is dropped when the controller changes', async () => {
    // Since SNOW-1025 a silent activation replaces the controller under a
    // live page, so an answer about the old worker must not outlive it.
    staleShell('shell-controllerchange', 'shell-old');
    expect(await window.pwaUpdateBanner.reveal()).toBe(true);

    containerListeners.controllerchange.forEach((fn) => fn());
    workerReply = { type: 'shell-identity', cache: 'shell-controllerchange' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(false);
  });
});

describe('the identity message port', () => {
  it('is closed on every settlement path', async () => {
    // Assigning onmessage starts the port. An unclosed port per offer
    // accumulates for as long as the page stays open.
    const closed = [];
    const RealChannel = globalThis.MessageChannel;
    class RecordingChannel extends RealChannel {
      constructor() {
        super();
        const close = this.port1.close.bind(this.port1);
        this.port1.close = () => {
          closed.push(true);
          close();
        };
      }
    }
    vi.stubGlobal('MessageChannel', RecordingChannel);

    try {
      staleShell('shell-port-answer', 'shell-port-answer');
      await window.pwaUpdateBanner.reveal();
      expect(closed).toHaveLength(1);

      verdict = { current: 'bbb', release: 'v30', shell: 'shell-port-silent' };
      workerReply = null;
      vi.useFakeTimers();
      const pending = window.pwaUpdateBanner.reveal();
      await vi.advanceTimersByTimeAsync(2100);
      vi.useRealTimers();
      await pending;
      expect(closed).toHaveLength(2);
    } finally {
      vi.useRealTimers();
      vi.stubGlobal('MessageChannel', RealChannel);
    }
  });
});
