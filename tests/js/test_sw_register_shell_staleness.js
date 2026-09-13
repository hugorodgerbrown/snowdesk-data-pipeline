/*
 * tests/js/test_sw_register_shell_staleness.js — the predicate the update
 * banner is gated on (SNOW-952).
 *
 * The banner is the user's escape hatch for a service worker that has got
 * stuck, and it had stopped reading as one: both reveal paths keyed off
 * the SERVER's build, which changes on every deploy and says nothing about
 * this device. `serve_sw` bakes the deploy SHA into the worker, so a new
 * worker installs and parks even on a deploy that touched only Python;
 * `update_available` on /api/version is literally "the server has
 * redeployed". Neither answers the only question the banner is about:
 * is the offline shell THIS DEVICE holds out of date?
 *
 * `shellIsStale` answers it by comparing the shell cache name the server
 * would serve (`shell`, from /api/version) against the one the controlling
 * worker reports in its `build-identity` reply. This file covers that
 * comparison and — as much as the assertion is about anything — its two
 * failure directions, which point opposite ways on purpose.
 *
 * Harness notes
 * -------------
 * `sw_register.js` is a load-time IIFE that resolves `#sw-update-banner`
 * once and defines non-configurable globals, so it can be imported only
 * once per jsdom window. The controller, the worker's reply and the
 * server's verdict are therefore mutable module-level stubs that each test
 * sets before calling through `window.pwaUpdateBanner.reveal()`.
 *
 * The gate memoises its answer against the server shell it answered for,
 * so every test that expects a fresh evaluation names a distinct shell.
 * That is the memo's contract, not a workaround: for one server shell the
 * answer cannot change, because the controlling worker cannot be replaced
 * without a `controllerchange` and the module reloads the page on one.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

/** What the stubbed worker answers `build-identity` with, or null for silence. */
let workerReply = null;

/** One entry per message the page posted to the controller. */
const posted = [];

/** The body the SERVER would return right now — what a refresh reads. */
let verdict = null;

/**
 * The body `pwa_version_check.js` is HOLDING, when it differs from the
 * server's. `verified()` returns this unless the caller asks for a
 * refresh, which is the real module's behaviour: it re-verifies once per
 * distinct version header, so a tab that confirmed one deploy keeps
 * handing that body back. Null means "holding nothing stale" and the
 * stub answers with the live body either way.
 */
let heldVerdict = null;

/** One entry per `verified()` call: the options it was given. */
const verifiedCalls = [];

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

const registration = {
  waiting: null,
  installing: null,
  addEventListener: () => {},
  update: vi.fn(() => Promise.resolve()),
};

Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: controller,
    register: vi.fn(() => Promise.resolve(registration)),
    getRegistration: () => Promise.resolve(registration),
    getRegistrations: () => Promise.resolve([]),
    addEventListener: () => {},
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

beforeAll(async () => {
  document.body.innerHTML = '<div id="sw-update-banner" class="hidden"></div>';
  window.pwaVersionInfo = {
    build: 'aaaaaaa1111',
    release: 'v29',
    verified: (options) => {
      verifiedCalls.push(options || {});
      if (options && options.refresh === true) {
        heldVerdict = verdict;
        return Promise.resolve(verdict);
      }
      return Promise.resolve(heldVerdict === null ? verdict : heldVerdict);
    },
  };

  await import('../../static/js/sw_register.js');
  await new Promise((resolve) => setTimeout(resolve, 20));
});

beforeEach(() => {
  posted.length = 0;
  verifiedCalls.length = 0;
  heldVerdict = null;
  banner().classList.add('hidden');
});

describe('the staleness gate', () => {
  it('stays silent when the worker holds the shell the server would serve', async () => {
    // The deploy changed no shell source: same cache name either side, so
    // there is nothing for a reload to fetch. This is the case that used
    // to interrupt every user after every deploy.
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-same' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-same' };

    const revealed = await window.pwaUpdateBanner.reveal();

    expect(revealed).toBe(false);
    expect(isRevealed()).toBe(false);
  });

  it('asks the worker once per server shell, not once per offer', async () => {
    // pwa_version_check.js re-offers the banner for every response that
    // replays a drifting version header, which on a cached feed is many.
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-memo' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-memo' };

    await window.pwaUpdateBanner.reveal();
    await window.pwaUpdateBanner.reveal();
    await window.pwaUpdateBanner.reveal();

    expect(posted.filter((m) => m.type === 'build-identity')).toHaveLength(1);
  });

  it('reveals when the worker holds a different shell', async () => {
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-new' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-old' };

    const revealed = await window.pwaUpdateBanner.reveal();

    expect(revealed).toBe(true);
    expect(isRevealed()).toBe(true);
  });

  it('re-evaluates when a second deploy names a different shell', async () => {
    // The memo is keyed, not latched: a session that outlives two deploys
    // gets an answer for each.
    verdict = { current: 'ccc', release: 'v31', shell: 'shell-first' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-first' };
    expect(await window.pwaUpdateBanner.reveal()).toBe(false);

    verdict = { current: 'ddd', release: 'v32', shell: 'shell-second' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });
});

describe('how fresh the server half has to be', () => {
  it('re-reads the verdict for a newly installed worker', async () => {
    // The waiting-worker path is woken by a WORKER, not by the version
    // check's own round trip, so it cannot accept whatever body that
    // module happens to be holding.
    verdict = { current: 'ccc', release: 'v31', shell: 'shell-refresh' };

    await window.pwaUpdateBanner.reveal(true);

    // [0] is the gate; labelBanner asks again, unrefreshed, to name builds.
    expect(verifiedCalls[0]).toEqual({ refresh: true });
  });

  it('accepts the held body for the header-drift path', async () => {
    // There, the body was fetched by the verification that raised the
    // question moments earlier — going back to the network would be a
    // second round trip for the same answer.
    verdict = { current: 'ccc', release: 'v31', shell: 'shell-noreload' };

    await window.pwaUpdateBanner.reveal();

    expect(verifiedCalls[0]).toEqual({ refresh: false });
  });

  it('does not miss a second deploy behind a stale held verdict', async () => {
    // The regression, in full. A tab verifies deploy B, which changed the
    // build but no shell source — correctly silent. B's body is now held
    // indefinitely: the version check re-verifies once per distinct
    // header value, and every later replay of B's header reuses it.
    verdict = { current: 'bbb', release: 'v30', shell: 'shell-B' };
    heldVerdict = verdict;
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-B' };
    expect(await window.pwaUpdateBanner.reveal()).toBe(false);

    // Deploy C changes a shell source. Its worker installs and parks, and
    // that install is the only notice this tab gets. Judged against B's
    // held body the shells match and the banner is swallowed — the tab
    // then sits on a stale shell with nothing left to tell it.
    verdict = { current: 'ccc', release: 'v31', shell: 'shell-C' };

    expect(await window.pwaUpdateBanner.reveal(true)).toBe(true);
    expect(isRevealed()).toBe(true);
  });
});

describe('what the gate does when it cannot tell', () => {
  it('reveals when the worker answers without a cache name', async () => {
    // Every worker deployed before SNOW-952 answers build-identity with no
    // `cache` field. Revealing is what happened before this change, and an
    // unanswering worker is a symptom of the state the banner exists to
    // escape — so the unknown fails OPEN. It self-corrects on the next
    // deploy, when the controlling worker knows the field.
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-nocache' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', release: 'v29' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });

  it('reveals when the worker never answers at all', async () => {
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-silent' };
    workerReply = null;

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  }, 10000);

  it('reveals when the server names no shell', async () => {
    // A server that predates the field, or one that could not be reached
    // at all: "cannot confirm" is never "confirmed current".
    verdict = { current: 'bbbbbbb2222', release: 'v30' };
    workerReply = { type: 'build-identity', build: 'aaaaaaa1111', cache: 'shell-held' };

    expect(await window.pwaUpdateBanner.reveal()).toBe(true);
  });

  it('stays silent when nothing is controlling the page', async () => {
    // The opposite direction, and the reason the check is not simply "fail
    // open on anything unknown": with no controller there is no cached
    // shell, so there is nothing that can be out of date. The page in
    // front of the user came off the network.
    const container = navigator.serviceWorker;
    Object.defineProperty(container, 'controller', { value: null, configurable: true });
    verdict = { current: 'bbbbbbb2222', release: 'v30', shell: 'shell-nocontroller' };
    workerReply = null;

    try {
      expect(await window.pwaUpdateBanner.reveal()).toBe(false);
      expect(isRevealed()).toBe(false);
    } finally {
      Object.defineProperty(container, 'controller', {
        value: controller,
        configurable: true,
      });
    }
  });
});
