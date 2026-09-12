/*
 * tests/js/test_pwa_offline.js — Vitest unit tests for
 * static/js/pwa_offline.js's connection-state UI (C2, 2026-08-03 JS review).
 *
 * Two behaviours are pinned first:
 *
 *   1. A caller-initiated abort is not a connectivity failure. The sign-in
 *      page starts the WebAuthn conditional ceremony on email-input focus
 *      and aborts it on the first keystroke, so a fully-online user typing
 *      their address used to have the whole app declaring itself offline
 *      for the life of the page.
 *   2. A successful same-origin response while ``navigator.onLine`` is true
 *      repaints as online. Without it the only recovery path is the
 *      ``online`` event, which never fires when connectivity never changed.
 *
 * ``pwa_offline.js`` is an IIFE that reads its DOM and wraps ``window.fetch``
 * at import time, so every test builds its fixture and installs its own
 * ``window.fetch`` mock BEFORE importing the module, then re-imports it fresh
 * via ``vi.resetModules()`` (same pattern as test_home_intro.js). Re-importing
 * per test also stops each run's wrapper stacking on the previous one's.
 *
 * ``window.pwaDb`` is left undefined for most tests: the persistence and
 * sync-log helpers all guard on it and return early, which keeps those tests
 * on the UI behaviour alone. Two blocks stub it, each because what it asserts
 * is what crosses that boundary — the sync-log block, which is about what gets
 * written, and SNOW-748's boot re-assert, whose whole subject is what comes
 * back out of ``meta:app``.
 *
 * SNOW-742 added a third mode; SNOW-748 rebuilt the surfaces that carry it. The
 * offline BANNER is gone. In its place: a PERMANENT header symbol, painted on
 * every page for every viewer and never hidden, and a connection-status PANEL
 * anchored beneath it. The symbol is the <summary> of a native <details>, so
 * the panel's opening and closing belong to the browser and to nav.html's
 * shared disclosure script — this module only follows the ``toggle`` event.
 * (It was briefly a bottom-centred toast, which is why the tests below assert
 * so hard about who owns visibility.) The distinction the mode tests turn on
 * is that
 * ``'offline'`` is the worker's guess that there is no route while
 * ``'offline-forced'`` is the user's instruction — so an ``online`` event, a
 * probe and a page reload all treat the two differently.
 *
 * The symbol and the menu switch paint DIFFERENT predicates, and several tests
 * exist only to hold that apart: the symbol reports whether the app is
 * reaching the server (so a dead interface strikes it through in ``'auto'``),
 * while the switch reports whether the user asked for offline mode (so a
 * merely-struggling connection must leave it off).
 *
 * The last block covers what that mode publishes. The control shipped without
 * it: ``snowdesk:connectivity-changed`` carried ``navigator.onLine`` alone and
 * fired only on an interface transition, so forcing offline mode changed
 * nothing any consumer could see — the map's layers menu kept its green sync
 * dots and the basemap download controls went on offering downloads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const PANEL_ID = 'pwa-connection-panel';
const SWITCH_ID = 'nav-offline-mode';
const INDICATOR_SELECTOR = '[data-network-indicator]';
const DISCLOSURE_SELECTOR = '[data-network-panel]';
const TOGGLE_ROW_SELECTOR = '[data-network-toggle]';
const CTA_SELECTOR = `#${PANEL_ID} [data-network-reconnect]`;

/**
 * The "Offline mode" row — the control half of the aeroplane-mode model.
 *
 * A real ``includes/_switch.html`` checkbox, not the role="menuitemcheckbox"
 * button this row shipped as, so the state the module writes is ``checked``
 * on an input rather than an attribute on a button.
 *
 * SNOW-921 moved it out of the subscriber menu and INTO the panel below, and
 * with it went the signed-in-only rule: the mode is a ``meta:app`` row and a
 * service-worker flag, so every viewer gets the row now. It stays its own
 * constant because the module must still cope with its absence — see
 * ``buildStaleShellFixture``.
 */
const SWITCH_ROW = `
  <div data-network-toggle class="hidden items-center gap-3">
    <label for="${SWITCH_ID}">Offline mode</label>
    <label for="${SWITCH_ID}">
      <input id="${SWITCH_ID}" type="checkbox" role="switch" class="peer sr-only">
    </label>
  </div>
`;

/**
 * The header disclosure from templates/includes/nav.html — the <details>, the
 * <summary> that is the connectivity symbol and its SNOW-921 traffic arrows,
 * and the network menu from templates/includes/_connection_panel.html inside
 * it.
 *
 * Mirrored here rather than only in the templates because the module toggles
 * them by ``data-role``, so a fixture missing one would silently make those
 * assertions vacuous — the role helper skips a role it cannot find. The
 * <details> wrapper is mirrored for the same reason one layer up: the module
 * reads open/closed from it, so a fixture that kept the old
 * button-plus-hidden-div shape would test a surface that no longer exists.
 * The arrows are mirrored for a third version of it: ``pulseTraffic`` returns
 * silently when it cannot find one, so a fixture without them would make
 * every traffic assertion pass by describing nothing.
 *
 * No ``hidden`` class anywhere on the panel: visibility is the <details>'s
 * ``open`` property, which is what stops this module and the template
 * disagreeing about which of them closes the surface.
 *
 * @param {{withSwitch?: boolean}} [options]
 * @returns {string}
 */
function symbolAndPanel({ withSwitch = true } = {}) {
  return `
  <details class="relative" data-network-panel>
    <summary
      id="network-indicator-toggle"
      data-network-indicator
      data-network-state="online"
      aria-expanded="false"
      aria-controls="${PANEL_ID}"
    >
      <span data-role="network-online-icon"><svg></svg></span>
      <span data-role="network-offline-icon" class="hidden"><svg></svg></span>
      <span aria-hidden="true">
        <span data-traffic-arrow="up"><svg></svg></span>
        <span data-traffic-arrow="down"><svg></svg></span>
      </span>
      <span data-role="network-name-online" class="sr-only">Network menu: using the network</span>
      <span data-role="network-name-offline" class="sr-only hidden">Network menu: offline</span>
    </summary>
    <div id="${PANEL_ID}">
      <div>
        <span>
          <span data-role="online-message">Online — last synced</span>
          <span data-role="offline-message" class="hidden">Offline — last synced</span>
          <span data-role="latched-message" class="hidden">Offline mode — last synced</span>
          <span data-role="synced-at">—</span>
        </span>
        <button type="button" data-disclosure-close aria-label="Dismiss">×</button>
      </div>
      <span>
        <span data-role="online-explainer">Using the network.</span>
        <span data-role="offline-explainer" class="hidden">Lost contact.</span>
        <span data-role="latched-explainer" class="hidden">Stopped trying.</span>
        <span data-role="forced-explainer" class="hidden">You asked it to stay offline.</span>
      </span>
      ${withSwitch ? SWITCH_ROW : ''}
      <button type="button" data-network-reconnect>
        <span data-role="reconnect-label">Try reconnecting</span>
        <span data-role="resume-label" class="hidden">Use the network again</span>
      </button>
    </div>
  </details>
  <button type="button" data-network-required>Sync now</button>
`;
}

/**
 * A page as SNOW-921 renders it, for any viewer: symbol, traffic arrows,
 * menu, and the switch that changes the mode.
 *
 * The ``data-network-required`` button inside is any page's stand-in for a
 * control that cannot work without the network — that attribute is the
 * generic mechanism the whole site gates on, and the last block asserts a
 * forced mode reaches it.
 */
function buildFixture() {
  document.body.innerHTML = symbolAndPanel();
}

/**
 * A page whose menu has no switch row.
 *
 * This used to be ``buildAnonymousFixture``, and it stood for the anonymous
 * half of SNOW-748's split: the switch was in the account dropdown, so a
 * signed-out reader genuinely had a symbol and no way to change the mode.
 * SNOW-921 ended that — the row is in the menu every viewer gets — but the
 * MODULE CONTRACT it pinned still has to hold, because a page served from a
 * shell cached before that ticket renders exactly this shape. The two
 * elements are looked up separately for that reason, and a shared early
 * return on the missing row would leave such a page with no indication that
 * the app had stopped using the network.
 */
function buildStaleShellFixture() {
  document.body.innerHTML = symbolAndPanel({ withSwitch: false });
}

/**
 * nav.html's shared ``enhanceDisclosure`` script, mirrored in the three
 * behaviours pwa_offline.js has to survive: outside-click, Escape, and the
 * panel's ``[data-disclosure-close]`` control. All three close the <details>,
 * and the module learns about each one the same way — the ``toggle`` event.
 *
 * Mirrored rather than imported because the real thing is an inline <script>
 * in a Django template, which Vitest cannot import; this is that contract,
 * stated once, in the same spirit as the old overlays.js mirror it replaces.
 *
 * @returns {() => void} a teardown that unbinds the document/window listeners
 */
function installNavDisclosureScript() {
  const details = document.querySelector(DISCLOSURE_SELECTOR);
  const toggle = document.getElementById('network-indicator-toggle');
  const onClick = (event) => {
    if (!details.open) return;
    if (details.contains(event.target)) return;
    details.open = false;
  };
  const onKeydown = (event) => {
    if (event.key === 'Escape' && details.open) {
      details.open = false;
      toggle.focus();
    }
  };
  document.addEventListener('click', onClick);
  window.addEventListener('keydown', onKeydown);
  details.querySelector('[data-disclosure-close]').addEventListener('click', () => {
    details.open = false;
    toggle.focus();
  });
  return () => {
    document.removeEventListener('click', onClick);
    window.removeEventListener('keydown', onKeydown);
  };
}

/** Whether the element carrying ``data-role`` is currently visible. */
function roleShown(role) {
  const el = document.querySelector(`[data-role="${role}"]`);
  return !!el && !el.classList.contains('hidden');
}

/** The menu's "Offline mode" switch input, for readability at the call site. */
function switchInput() {
  return document.getElementById(SWITCH_ID);
}

/** The row the switch sits in — the element the module reveals. */
function toggleRow() {
  return document.querySelector(TOGGLE_ROW_SELECTOR);
}

/** The header symbol. */
function indicator() {
  return document.querySelector(INDICATOR_SELECTOR);
}

/** What the header symbol is currently reporting: 'online' or 'offline'. */
function indicatorState() {
  return indicator().getAttribute('data-network-state');
}

/** The <details> the symbol and the panel live in. */
function disclosure() {
  return document.querySelector(DISCLOSURE_SELECTOR);
}

/** Whether the connection-status panel is currently open. */
function panelShown() {
  return disclosure().open;
}

/**
 * Yield to the task queue.
 *
 * A <details> fires ``toggle`` asynchronously, so the module's
 * ``aria-expanded`` and freshness work lands a task after the press that
 * caused it. Every assertion about those follows an ``await tick()``.
 *
 * @returns {Promise<void>}
 */
function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** The panel's one CTA — the way back to the network. */
function reconnectButton() {
  return document.querySelector(CTA_SELECTOR);
}

/**
 * Record every ``snowdesk:connectivity-changed`` the module dispatches from
 * now on, and stop recording when ``stop()`` is called.
 *
 * Removed per test rather than left bound: every previous test's module
 * instance is still attached to the shared document (``vi.resetModules()``
 * gives a fresh module, it does not unbind the old one's listeners), so a
 * listener left in place would collect another test's broadcasts too.
 *
 * @returns {{online: boolean[], stop: () => void}}
 */
function recordConnectivity() {
  const online = [];
  const listener = (event) => online.push(event.detail.online);
  document.addEventListener('snowdesk:connectivity-changed', listener);
  return {
    online,
    stop: () => document.removeEventListener('snowdesk:connectivity-changed', listener),
  };
}

/** The fixture's ``data-network-required`` control. */
function networkRequiredButton() {
  return document.querySelector('[data-network-required]');
}

/** Whether the menu switch is painted in an offline state. */
function switchChecked() {
  return switchInput().checked;
}

/**
 * Install a service-worker stub that records what the page posts to the
 * worker and lets a test push a worker-originated message back.
 *
 * @returns {{posted: object[], emit: (data: object) => void}}
 */
function stubServiceWorker() {
  const posted = [];
  const listeners = [];
  Object.defineProperty(window.navigator, 'serviceWorker', {
    configurable: true,
    value: {
      controller: { postMessage: (data) => posted.push(data) },
      addEventListener: (type, handler) => {
        if (type === 'message') listeners.push(handler);
      },
    },
  });
  return {
    posted,
    emit: (data) => listeners.forEach((handler) => handler({ data })),
  };
}

// The ``online`` handler the most recent ``loadModule()`` registered — see
// ``fireOnline`` for why the test calls it rather than dispatching the event.
let lastOnlineHandler = null;

/**
 * Re-import pwa_offline.js fresh against the current fixture and fetch
 * mock, awaiting the microtasks its async ``init()`` defers past.
 *
 * ``window.addEventListener`` is wrapped for the duration so the ``online``
 * handler this instance registers can be called directly later. Every previous
 * test's module instance is STILL bound to ``window`` — ``vi.resetModules()``
 * gives a fresh module, it does not unbind the old one's listeners — and each
 * carries its own ``networkMode`` closure over the shared DOM. Dispatching a
 * real ``online`` event therefore runs a dozen handlers, several of them in
 * ``'offline'`` and all of them writing to the one fixture.
 */
async function loadModule() {
  const realAdd = window.addEventListener.bind(window);
  window.addEventListener = (type, handler, options) => {
    if (type === 'online') lastOnlineHandler = handler;
    return realAdd(type, handler, options);
  };
  try {
    vi.resetModules();
    await import('../../static/js/pwa_offline.js');
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  } finally {
    window.addEventListener = realAdd;
  }
}

/** Run this instance's ``online`` handler, and nobody else's. */
function fireOnline() {
  lastOnlineHandler(new Event('online'));
}

/**
 * Click the menu switch and let its handler settle.
 *
 * SNOW-922 made the ON direction asynchronous: it asks the worker whether
 * the app would in fact open offline before it lets the mode strand the
 * device, so the mode change now lands a microtask or two after the click
 * rather than inside it. The OFF direction is still synchronous, and
 * deliberately so — that is the recovery direction, and nothing should
 * stand between a stranded user and the network.
 */
async function clickSwitch() {
  switchInput().click();
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

/**
 * Answer the SNOW-922 lock-out confirmation for the duration of a test.
 *
 * Every test in this file runs with no ``window.pwaNetworkMode``, which is
 * the module-absent fallback path — and on that path the guard cannot ask
 * the worker anything, so it proceeds without a dialogue. The stub is here
 * for the tests that DO load the module and need to answer it.
 *
 * @param {boolean} answer
 */
function stubConfirm(answer) {
  const real = window.confirm;
  // A spy, so a test can assert the dialogue was never RAISED — "did not
  // warn" is a different claim from "was not obeyed", and the first is
  // the one the guard's cheap path makes.
  window.confirm = vi.fn().mockReturnValue(answer);
  return () => {
    window.confirm = real;
  };
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

describe('the header symbol tracks the connection', () => {
  it('does not report offline when a fetch is aborted', async () => {
    const err = abortError();
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/account/passkey/auth/request/')).rejects.toBe(err);

    expect(indicatorState()).toBe('online');
    expect(roleShown('network-online-icon')).toBe(true);
    expect(roleShown('network-offline-icon')).toBe(false);
  });

  it('reports offline on a genuine network failure', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi.fn().mockRejectedValue(err);
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);

    expect(indicatorState()).toBe('offline');
    expect(roleShown('network-offline-icon')).toBe(true);
    expect(roleShown('network-online-icon')).toBe(false);
  });

  it('recovers on a successful same-origin response while online', async () => {
    const err = new TypeError('Failed to fetch');
    window.fetch = vi
      .fn()
      .mockRejectedValueOnce(err)
      .mockResolvedValueOnce(okResponse());
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toBe(err);
    expect(indicatorState()).toBe('offline');

    await window.fetch('/api/ratings/');

    expect(indicatorState()).toBe('online');
  });

  it('names itself for the state, in text the template rendered', async () => {
    // Both glyph partials set aria-hidden on their own <svg>, so the sr-only
    // spans ARE the accessible name. Toggled here, never assigned: a name set
    // from a JS literal ships English to every locale, because makemessages
    // never scans static/js.
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(roleShown('network-name-online')).toBe(true);
    expect(roleShown('network-name-offline')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    expect(roleShown('network-name-offline')).toBe(true);
    expect(roleShown('network-name-online')).toBe(false);
  });

  it('is painted on a page whose menu has no switch row at all', async () => {
    // The two elements are looked up separately, and this is the case that
    // needs it. Until SNOW-921 it was the anonymous page — the switch was in
    // the account dropdown. It is now a page served from a shell cached
    // before that ticket, which is a state every deploy passes through. A
    // shared early return on the missing row would leave such a page with no
    // indication that the app had stopped using the network.
    buildStaleShellFixture();
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(switchInput()).toBeNull();
    expect(indicatorState()).toBe('online');

    sw.emit({ type: 'network-mode', mode: 'offline' });

    expect(indicatorState()).toBe('offline');
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the panel the symbol discloses
// ---------------------------------------------------------------------------
//
// The banner this replaces revealed itself whenever the app stopped reaching
// the server, and hid itself again when it recovered. The panel does neither:
// it opens and closes on the user's press alone, and the module only ever
// repaints its copy. These tests are what stops the old reveal-on-failure
// behaviour creeping back in under a different element id.
//
// Visibility is the <details>'s, not this module's — the panel was briefly a
// toast whose ``hidden`` class pwa_offline.js owned, and the symbol's
// ``aria-expanded`` could therefore disagree with the screen whenever anything
// else closed it. Now every close arrives on one event, so the tests below
// press the symbol, the "×", Escape and the page behind it, and expect the
// same three consequences each time.

describe('the connection-status panel (SNOW-748)', () => {
  it('stays closed until the symbol is pressed', async () => {
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toThrow();

    // Offline, and still nothing over the page. The header says so; the detail
    // is one press away.
    expect(indicatorState()).toBe('offline');
    expect(panelShown()).toBe(false);
  });

  it('opens on a press of the symbol and closes on the next one', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    indicator().click();
    await tick();
    expect(panelShown()).toBe(true);
    expect(indicator().getAttribute('aria-expanded')).toBe('true');

    indicator().click();
    await tick();
    expect(panelShown()).toBe(false);
    expect(indicator().getAttribute('aria-expanded')).toBe('false');
  });

  it('never changes the network mode', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    indicator().click();
    await tick();

    // A disclosure, not a switch. An earlier pass shipped this element as a
    // toggle for the mode itself, which is what aria-expanded (rather than
    // aria-pressed) now promises a screen-reader user it is not.
    expect(sw.posted).toEqual([]);
    expect(switchChecked()).toBe(false);
  });

  it('follows the panel\'s own close control', async () => {
    const teardown = installNavDisclosureScript();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      indicator().click();
      await tick();
      expect(indicator().getAttribute('aria-expanded')).toBe('true');

      document.querySelector('[data-disclosure-close]').click();
      await tick();

      // nav.html's shared script closes the <details>; the module learns from
      // the toggle event. Without that the symbol would claim the panel was
      // still open, and the next press would "close" an already-closed one.
      expect(panelShown()).toBe(false);
      expect(indicator().getAttribute('aria-expanded')).toBe('false');
    } finally {
      teardown();
    }
  });

  it('follows an Escape it did not handle', async () => {
    const teardown = installNavDisclosureScript();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      indicator().click();
      await tick();

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      await tick();

      expect(panelShown()).toBe(false);
      expect(indicator().getAttribute('aria-expanded')).toBe('false');
    } finally {
      teardown();
    }
  });

  it('follows a click on the page behind it', async () => {
    const teardown = installNavDisclosureScript();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      indicator().click();
      await tick();

      networkRequiredButton().click();
      await tick();

      // Outside-click is the dismissal a popover is judged on: it floats over
      // the map, and a reader who has finished with it reaches for the map,
      // not for the "×".
      expect(panelShown()).toBe(false);
      expect(indicator().getAttribute('aria-expanded')).toBe('false');
    } finally {
      teardown();
    }
  });

  it('leaves the panel open on a click inside it', async () => {
    const teardown = installNavDisclosureScript();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      indicator().click();
      await tick();

      document.querySelector('[data-role="synced-at"]').click();
      await tick();

      expect(panelShown()).toBe(true);
    } finally {
      teardown();
    }
  });

  it('fills the freshness cell when it opens', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    await window.fetch('/api/ratings/');

    // Em dash until a sync is known; a relative phrase once one is.
    indicator().click();
    await tick();

    expect(document.querySelector('[data-role="synced-at"]').textContent).not.toBe('—');
  });

  it('explains a healthy connection, which the banner never had to', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The banner only existed in the failure case, so it had no copy for this
    // one. The symbol is pressable at any moment, so the panel needs it.
    expect(roleShown('online-message')).toBe(true);
    expect(roleShown('online-explainer')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
    expect(roleShown('latched-message')).toBe(false);
  });

  it('offers no way back while the network is already in use', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(reconnectButton().classList.contains('hidden')).toBe(true);
  });

  it('shows the way back in both offline states', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The worker's own latch: a repair, and "Try reconnecting" is the verb.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    expect(roleShown('reconnect-label')).toBe(true);

    // The user's choice: nothing is broken, so the verb changes and the button
    // stays. It is the whole exit for a signed-out reader either way, which is
    // why it is asserted in both and not just the one the header text differs
    // on.
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    expect(roleShown('resume-label')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// SNOW-742 — the offline latch, as the page sees it
// ---------------------------------------------------------------------------
//
// The UI used to key off ``navigator.onLine`` alone. That is exactly the
// signal which stays TRUE on the Underground — the radio is attached, there is
// simply no route — so the state these tests cover is one the old banner could
// not represent at all: latched, and online as far as the platform knows.

describe('offline latch (SNOW-742)', () => {
  it('shows the latched copy when the worker announces a latch, even while onLine', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(roleShown('online-message')).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Painted despite navigator.onLine being true throughout — the case the
    // old onLine-only banner was blind to.
    expect(window.navigator.onLine).toBe(true);
    expect(indicatorState()).toBe('offline');
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('online-message')).toBe(false);
    expect(roleShown('latched-explainer')).toBe(true);
  });

  it('offers the way back only once the app has actually stopped trying', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    // Struggling but not latched: the app is still trying on its own, so
    // "try reconnecting" would do nothing it is not already doing.
    await expect(window.fetch('/api/ratings/')).rejects.toThrow();
    expect(roleShown('offline-message')).toBe(true);
    expect(reconnectButton().classList.contains('hidden')).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline' });

    // Latched: the app has stopped, so the useful action is the way back.
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    expect(roleShown('reconnect-label')).toBe(true);
  });

  it('asks the worker to unlatch when the user taps Try reconnecting', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    reconnectButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(roleShown('online-message')).toBe(true);
  });

  it('clears the symbol once the worker reports it has unlatched', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(indicatorState()).toBe('offline');

    // A probe found a route again.
    sw.emit({ type: 'network-mode', mode: 'auto' });

    expect(indicatorState()).toBe('online');
  });
});

// ---------------------------------------------------------------------------
// The sync log's write side
// ---------------------------------------------------------------------------
//
// The panel on /account/settings/ answers "has this device actually reached
// the server", so a row only earns its place if the reader would recognise it
// as the app fetching something. telemetry.js flushes its own buffer every 30
// seconds and on every lifecycle event, which filled the panel with rows
// describing nothing the reader did.

describe('sync-log write filter', () => {
  /**
   * Stub the three ``window.pwaDb`` methods pwa_offline.js touches and
   * record every path handed to ``appendSyncLog``.
   *
   * @returns {string[]} live array of logged paths
   */
  function stubDb() {
    const logged = [];
    window.pwaDb = {
      get: async () => undefined,
      put: async () => {},
      appendSyncLog: async (entry) => {
        logged.push(entry.path);
      },
    };
    return logged;
  }

  afterEach(() => {
    delete window.pwaDb;
  });

  it('does not log a telemetry flush', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/telemetry'));
    await loadModule();

    await window.fetch('/api/telemetry', { method: 'POST' });

    expect(logged).toEqual([]);
  });

  it('still logs a request the reader would recognise', async () => {
    const logged = stubDb();
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/ratings/'));
    await loadModule();

    await window.fetch('/api/ratings/');

    expect(logged).toEqual(['/api/ratings/']);
  });
});

// ---------------------------------------------------------------------------
// SNOW-748 — the menu switch, and a forced mode that stays forced
// ---------------------------------------------------------------------------
//
// The control SNOW-742 built lived in the banner, which only revealed once the
// connection had already failed — so the user it was for, "I have signal now
// and am about to lose it", could never reach it. It is now at the top of the
// account menu, the settings half of the aeroplane-mode model whose status-bar
// half is the header symbol. That move exposed the defect these tests pin: a
// user's request used to be the worker's auto-latch, and an auto-latch is
// probed back to 'auto' within thirty seconds. ``'offline'`` is the worker's
// guess; ``'offline-forced'`` is the user's.

describe('the menu offline-mode switch (SNOW-748)', () => {
  it('is revealed by this module, not by the template', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    // The nav renders it `hidden` so a page whose script never runs does not
    // offer a control nothing will honour.
    expect(toggleRow().classList.contains('hidden')).toBe(true);

    await loadModule();

    expect(toggleRow().classList.contains('hidden')).toBe(false);
    expect(toggleRow().classList.contains('flex')).toBe(true);
  });

  it('asks for a forced mode — never the auto-latch — when switched on while online', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    await clickSwitch();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
    expect(sw.posted).not.toContainEqual({ type: 'network-mode', mode: 'offline' });
  });

  it('round-trips: switching it off asks for auto again', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    await clickSwitch();
    expect(switchChecked()).toBe(true);
    expect(indicatorState()).toBe('offline');

    switchInput().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(switchChecked()).toBe(false);
    expect(indicatorState()).toBe('online');
  });

  it('reports on under either offline mode, including one it did not start', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(switchChecked()).toBe(false);

    // ``checked`` is the row's whole state: a switch says on/off and nothing
    // finer, and the worker's latch is as much "not using the network" as the
    // user's own choice is.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(switchChecked()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(switchChecked()).toBe(true);
  });

  it('stays off while the app is merely struggling, though the symbol does not', async () => {
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toThrow();

    // The two surfaces answer different questions, and this is the case that
    // separates them. The symbol reports whether the app is reaching the
    // server, so it goes struck-through. The switch reports whether the USER
    // asked for offline mode, and nobody did — a switch that flicks itself on
    // when the lift crosses a ridge reports the worker's decision as theirs.
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(false);
  });
});

describe('the forced offline mode, as the page renders it (SNOW-748)', () => {
  it('paints the symbol offline while navigator.onLine is true', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    // The normal case for this mode: a working connection the user has asked
    // the app not to use. Keying the symbol off onLine would say "online".
    expect(window.navigator.onLine).toBe(true);
    expect(indicatorState()).toBe('offline');
  });

  it('explains a forced mode differently from a latch, and shares the summary line', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(roleShown('latched-explainer')).toBe(true);
    expect(roleShown('forced-explainer')).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });

    // "There is no usable connection" is false here, so that copy must go.
    expect(roleShown('forced-explainer')).toBe(true);
    expect(roleShown('latched-explainer')).toBe(false);
    // The summary line is shared: both modes mean "not contacting the server".
    expect(roleShown('latched-message')).toBe(true);
    expect(roleShown('offline-message')).toBe(false);
    expect(roleShown('online-message')).toBe(false);
  });

  it('offers the way back with the verb that fits, in both modes', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(reconnectButton().classList.contains('hidden')).toBe(false);
    // "Try reconnecting" reads as a repair, and nothing is broken.
    expect(roleShown('resume-label')).toBe(true);
    expect(roleShown('reconnect-label')).toBe(false);

    reconnectButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('is the whole way back on a page with no switch row', async () => {
    // SNOW-748's reason for keeping the banner's reconnect control was the
    // anonymous reader, who had no switch. SNOW-921 gave them one — but a
    // page served from a shell cached before that ticket still has none, and
    // for it this button is the only exit from a worker-latched mode.
    buildStaleShellFixture();
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(toggleRow()).toBeNull();

    reconnectButton().click();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('survives an online event, where an auto-latch does not', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    fireOnline();

    // The user is very often ONLINE when they choose this mode — a metered
    // roam, a battery to nurse, a tunnel ahead — so an interface event must
    // not overrule them.
    expect(sw.posted).not.toContainEqual({ type: 'network-mode', mode: 'auto' });
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(true);

    // The contrast: an auto-latch is the worker guessing there is no route,
    // and an online event is better evidence, so that one does lift.
    sw.emit({ type: 'network-mode', mode: 'offline' });
    fireOnline();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'auto' });
  });

  it('re-asserts the persisted mode as itself, not as a latch', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    // A worker terminated while idle comes back in 'auto'. Re-asserting is
    // what restores the mode — and re-asserting the WRONG one hands it a
    // latch, which schedules the probe that ends the user's choice.
    window.pwaDb = {
      get: vi.fn().mockResolvedValue({ key: 'network.mode', value: 'offline-forced' }),
      put: vi.fn().mockResolvedValue(undefined),
      add: vi.fn().mockResolvedValue(undefined),
      getAll: vi.fn().mockResolvedValue([]),
      isResetRequired: () => false,
    };

    await loadModule();

    expect(sw.posted).toContainEqual({ type: 'network-mode', mode: 'offline-forced' });
    expect(indicatorState()).toBe('offline');
    expect(switchChecked()).toBe(true);
    expect(roleShown('forced-explainer')).toBe(true);
    delete window.pwaDb;
  });
});

describe('what a forced mode publishes to the rest of the app (SNOW-748)', () => {
  /*
   * The toggle shipped without this half. `broadcastConnectivity` carried
   * `navigator.onLine` alone, and fired only from the `online`/`offline`
   * listeners and at boot — so pressing the toggle dispatched nothing, and
   * every consumer (the layers menu's sync dots, both basemap download
   * controls, the downloads sheet) went on believing the network was
   * available. Hugo reproduced it live: zero events, and a Download button
   * still `disabled: false`.
   */

  it('reports offline the moment the mode goes forced, while onLine is true', async () => {
    // Installed but not read back: this test drives the mode from the page's
    // own switch, and the stub is only here so the post to the worker lands
    // somewhere fresh rather than on a previous test's stub.
    stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    const seen = recordConnectivity();

    try {
      await clickSwitch();

      // The premise of this whole mode: the interface is up throughout.
      expect(window.navigator.onLine).toBe(true);
      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('reports online again when the user returns to auto', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    const seen = recordConnectivity();

    try {
      switchInput().click();

      expect(seen.online).toEqual([true]);
    } finally {
      seen.stop();
    }
  });

  it('reports a latch the worker announced on its own, too', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    const seen = recordConnectivity();

    try {
      // Not only the user's mode: a latch also stops the app calling the
      // server, and the dots and download controls must see that as well.
      sw.emit({ type: 'network-mode', mode: 'offline' });

      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('does not re-report online on an interface event while forced', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    const seen = recordConnectivity();

    try {
      fireOnline();

      // The `online` listener used to broadcast a hardcoded `true`, which
      // handed every consumer the network back the first time the radio
      // blinked — under a mode the user had chosen and the worker was still
      // enforcing.
      expect(seen.online).toEqual([false]);
    } finally {
      seen.stop();
    }
  });

  it('disables data-network-required controls under a forced mode', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(networkRequiredButton().disabled).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(networkRequiredButton().disabled).toBe(true);

    // And an interface event must not undo it — same trap as the broadcast
    // above, one mechanism across.
    fireOnline();
    expect(networkRequiredButton().disabled).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(networkRequiredButton().disabled).toBe(false);
  });

  it('answers the same question through window.pwaConnectivity', async () => {
    const sw = stubServiceWorker();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    // The read the map's controls make when they repaint — they re-render on
    // the event, so the two must agree or the paint contradicts the symbol.
    expect(window.pwaConnectivity.isOnline()).toBe(true);

    sw.emit({ type: 'network-mode', mode: 'offline-forced' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'offline' });
    expect(window.pwaConnectivity.isOnline()).toBe(false);

    sw.emit({ type: 'network-mode', mode: 'auto' });
    expect(window.pwaConnectivity.isOnline()).toBe(true);
  });
});

describe('the lock-out guard on the switch’s ON direction (SNOW-922)', () => {
  /*
   * Switching Offline mode ON is the move that produced SNOW-922. Under
   * 'offline-forced' sw.js refuses every navigation and answers from
   * cache, so a device with no shell page cached for the current account
   * reaches static/offline.html and nothing else — and until that ticket
   * the only control that ended the state was this switch, inside the app
   * that would not open. A live signal made no difference: the worker
   * refuses the network, not the radio.
   *
   * So the ON direction now asks the worker whether the app would in fact
   * open before it lets the mode strand the device. The OFF direction
   * asks nothing and is still synchronous — that is the recovery
   * direction, and nothing belongs between a stranded user and the
   * network.
   */

  /**
   * Stand in for pwa_network_mode.js.
   *
   * A stub rather than the real module because what these tests are about
   * is the DECISION pwa_offline.js makes with the answer; the module's own
   * behaviour is pinned in tests/js/test_pwa_network_mode.js.
   *
   * @param {boolean} canOpen What the worker would say.
   */
  function stubNetworkMode(canOpen) {
    const set = vi.fn().mockResolvedValue('auto');
    window.pwaNetworkMode = {
      coerce: (value) =>
        value === 'offline' || value === 'offline-forced' ? value : 'auto',
      set,
      canOpenOffline: vi.fn().mockResolvedValue(canOpen),
    };
    return { set, api: window.pwaNetworkMode };
  }

  afterEach(() => {
    delete window.pwaNetworkMode;
  });

  it('does not warn when the worker says the app is saved', async () => {
    const mode = stubNetworkMode(true);
    const restore = stubConfirm(false);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      await clickSwitch();

      expect(window.confirm).not.toHaveBeenCalled();
      expect(mode.set).toHaveBeenCalledWith('offline-forced');
      expect(switchChecked()).toBe(true);
    } finally {
      restore();
    }
  });

  it('warns, and stands down, when the app is not saved and the user declines', async () => {
    const mode = stubNetworkMode(false);
    const restore = stubConfirm(false);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      await clickSwitch();

      // The mode is never entered, and the switch goes back to where the
      // user left it — assigning `checked` fires no `change`, so this
      // cannot re-enter the handler.
      expect(mode.set).not.toHaveBeenCalled();
      expect(switchChecked()).toBe(false);
      expect(indicatorState()).toBe('online');
    } finally {
      restore();
    }
  });

  it('proceeds when the user says yes anyway', async () => {
    // Someone who genuinely wants aeroplane mode on a fresh device can
    // still have it. The warning names the consequence; it does not
    // overrule them.
    const mode = stubNetworkMode(false);
    const restore = stubConfirm(true);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      await clickSwitch();

      expect(mode.set).toHaveBeenCalledWith('offline-forced');
      expect(switchChecked()).toBe(true);
    } finally {
      restore();
    }
  });

  it('never asks anything on the way back to the network', async () => {
    // The recovery direction. A confirmation here would be a dialogue
    // between a stranded user and the fix.
    const mode = stubNetworkMode(false);
    const restore = stubConfirm(true);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      await clickSwitch();
      mode.api.canOpenOffline.mockClear();

      switchInput().click();

      expect(mode.api.canOpenOffline).not.toHaveBeenCalled();
      expect(mode.set).toHaveBeenLastCalledWith('auto');
    } finally {
      restore();
    }
  });

  /*
   * Found in review by Codex on the first push. The ON path awaits the
   * worker for up to three seconds; the OFF path answers at once. So a
   * user who flipped ON and changed their mind inside that window got
   * 'auto' immediately and then 'offline-forced' when the stale callback
   * landed — their LAST action losing to their previous one, on the one
   * switch where that means the app stops calling the server against
   * their stated wish. A press that has been superseded now does nothing.
   */
  it('abandons a pending ON when the user flips back OFF before it answers', async () => {
    let answer;
    const set = vi.fn().mockResolvedValue('auto');
    window.pwaNetworkMode = {
      coerce: (value) =>
        value === 'offline' || value === 'offline-forced' ? value : 'auto',
      set,
      // Held open, so the test controls exactly when the worker replies.
      canOpenOffline: vi.fn(() => new Promise((resolve) => (answer = resolve))),
    };
    const restore = stubConfirm(true);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      // ON — the preflight starts and does not settle.
      switchInput().click();
      // OFF, while it is still in flight. This is answered at once.
      switchInput().click();
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
      expect(set).toHaveBeenLastCalledWith('auto');

      // Now the worker answers the press that has been taken back.
      answer(true);
      for (let i = 0; i < 5; i += 1) await Promise.resolve();

      // The stale result changes nothing: not the mode, not the switch.
      expect(set).toHaveBeenLastCalledWith('auto');
      expect(set).not.toHaveBeenCalledWith('offline-forced');
      expect(switchChecked()).toBe(false);
      expect(indicatorState()).toBe('online');
    } finally {
      restore();
      delete window.pwaNetworkMode;
    }
  });

  it('raises no dialogue for a press the user has already taken back', async () => {
    // The other half: a warning about a press that is no longer live is a
    // question with no right answer, so it is never asked.
    let answer;
    window.pwaNetworkMode = {
      coerce: (value) =>
        value === 'offline' || value === 'offline-forced' ? value : 'auto',
      set: vi.fn().mockResolvedValue('auto'),
      canOpenOffline: vi.fn(() => new Promise((resolve) => (answer = resolve))),
    };
    const restore = stubConfirm(true);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      switchInput().click();
      switchInput().click();
      for (let i = 0; i < 5; i += 1) await Promise.resolve();

      // `false` is the answer that would otherwise raise the warning.
      answer(false);
      for (let i = 0; i < 5; i += 1) await Promise.resolve();

      expect(window.confirm).not.toHaveBeenCalled();
    } finally {
      restore();
      delete window.pwaNetworkMode;
    }
  });

  it('still honours a press the user has NOT taken back', async () => {
    // The guard must abandon a superseded press without abandoning a slow
    // one — otherwise the fix quietly removes the feature.
    let answer;
    const set = vi.fn().mockResolvedValue('auto');
    window.pwaNetworkMode = {
      coerce: (value) =>
        value === 'offline' || value === 'offline-forced' ? value : 'auto',
      set,
      canOpenOffline: vi.fn(() => new Promise((resolve) => (answer = resolve))),
    };
    const restore = stubConfirm(true);
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    try {
      switchInput().click();
      for (let i = 0; i < 5; i += 1) await Promise.resolve();

      answer(true);
      for (let i = 0; i < 5; i += 1) await Promise.resolve();

      expect(set).toHaveBeenCalledWith('offline-forced');
      expect(switchChecked()).toBe(true);
    } finally {
      restore();
      delete window.pwaNetworkMode;
    }
  });
});

// ---------------------------------------------------------------------------
// SNOW-921 — the traffic arrows
// ---------------------------------------------------------------------------
//
// The header said whether the app COULD reach the server and when it last
// DID, and nothing at all about whether anything was moving right now. A pan
// over a downloaded region and a pan spending a roaming connection looked
// identical from the top bar.
//
// Two arrows beside the glyph, one lit per edge. Deliberately approximate —
// they answer "is anything moving", not "how much" — so nothing below counts
// anything. What IS pinned is the part that would be wrong rather than
// merely imprecise: which edges light which arrow, that a failure lights no
// arrival, and what the pair stays dark for. That last one is the whole
// difference between an activity lamp and a metronome: telemetry flushes its
// buffer every 30 seconds forever, so a pair that pulsed for it would blink
// at an idle tab until the battery ran out.

/**
 * One traffic arrow.
 *
 * @param {'up'|'down'} direction
 * @returns {Element|null}
 */
function trafficArrow(direction) {
  return document.querySelector(`[data-traffic-arrow="${direction}"]`);
}

/**
 * Whether an arrow is currently lit.
 *
 * Read as the presence of ``data-active``, which is the module's entire
 * output here: ``src/css/main.css`` owns the colour, the opacity and the
 * transition, so there is no class to assert and no colour that could drift
 * between the two files.
 *
 * @param {'up'|'down'} direction
 * @returns {boolean}
 */
function arrowLit(direction) {
  return trafficArrow(direction).hasAttribute('data-active');
}

describe('the traffic arrows (SNOW-921)', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts dark, lights up on the way out and down on the way back', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    expect(arrowLit('up')).toBe(false);
    expect(arrowLit('down')).toBe(false);

    await window.fetch('/api/ratings/');

    expect(arrowLit('up')).toBe(true);
    expect(arrowLit('down')).toBe(true);
  });

  it('goes out again after the pulse window', async () => {
    vi.useFakeTimers();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();
    await window.fetch('/api/ratings/');

    expect(arrowLit('up')).toBe(true);

    // Just short of the window: still lit. A single round trip has to be
    // legible, which is the reason the pulse outlives the request.
    vi.advanceTimersByTime(400);
    expect(arrowLit('up')).toBe(true);

    vi.advanceTimersByTime(100);
    expect(arrowLit('up')).toBe(false);
    expect(arrowLit('down')).toBe(false);
  });

  it('lights nothing on the way back from a failed request', async () => {
    // Nothing came back. An arrow that lit here would say the opposite of
    // what the struck-through glyph is about to say, two pixels away.
    window.fetch = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    await loadModule();

    await expect(window.fetch('/api/ratings/')).rejects.toThrow();

    expect(arrowLit('up')).toBe(true);
    expect(arrowLit('down')).toBe(false);
    expect(indicatorState()).toBe('offline');
  });

  it('stays dark for the telemetry flush', async () => {
    // static/js/telemetry.js posts on a 30s cadence and on every lifecycle
    // event. This is the exclusion that keeps the pair from blinking at the
    // page talking to itself, forever, on a tab nobody is touching.
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/telemetry'));
    await loadModule();

    await window.fetch('/api/telemetry');

    expect(arrowLit('up')).toBe(false);
    expect(arrowLit('down')).toBe(false);
  });

  it('stays dark for static assets', async () => {
    // A cold boot pulls forty of them and says nothing a user wanted to know.
    window.fetch = vi.fn().mockResolvedValue(okResponse('/static/js/map.js'));
    await loadModule();

    await window.fetch('/static/js/map.js');

    expect(arrowLit('up')).toBe(false);
  });

  it('lights for a cross-origin tile, which is the interesting case', async () => {
    // Basemap tiles are the bulkiest thing this app fetches, and a pan served
    // entirely from a pinned bucket looks exactly like one spending a roaming
    // connection. The arrows are the only surface that can tell those apart,
    // so the same-origin exclusions above deliberately do not reach them.
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    await window.fetch('https://tiles.snowdesk-data.info/14/8522/5829.pbf');

    expect(arrowLit('up')).toBe(true);
  });

  it('reads the URL off a Request object as well as a string', async () => {
    // ``fetch`` takes a string, a URL or a Request, and the arrows are the
    // only caller in this module that has to know which — everything else
    // reads the response. Getting this wrong is silent: an unrecognised
    // input falls through to "pulse anyway", so the telemetry exclusion
    // above would quietly stop working for any caller using a Request.
    window.fetch = vi.fn().mockResolvedValue(okResponse('/api/telemetry'));
    await loadModule();

    await window.fetch({ url: '/api/telemetry' });

    expect(arrowLit('up')).toBe(false);
  });

  it('follows htmx traffic too, which never touches fetch', async () => {
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    document.body.dispatchEvent(new CustomEvent('htmx:beforeRequest'));
    expect(arrowLit('up')).toBe(true);
    expect(arrowLit('down')).toBe(false);

    document.body.dispatchEvent(new CustomEvent('htmx:afterOnLoad', { detail: {} }));
    expect(arrowLit('down')).toBe(true);
  });

  it('is a no-op on a page that renders no arrows', async () => {
    // A shell cached before this ticket. The pulse must not throw into the
    // fetch wrapper it is called from — an activity lamp that can break a
    // request is worse than no lamp.
    buildStaleShellFixture();
    trafficArrow('up')?.remove();
    trafficArrow('down')?.remove();
    window.fetch = vi.fn().mockResolvedValue(okResponse());
    await loadModule();

    const response = await window.fetch('/api/ratings/');

    expect(response.status).toBe(200);
  });
});
