/*
 * tests/js/test_debug_log_panel.js — Vitest unit tests for
 * static/js/debug_log_panel.js's two ways IN (SNOW-812, SNOW-921, SNOW-1415).
 *
 * The panel's painting is covered indirectly by the recorder's own tests;
 * what is asserted here is the part SNOW-921 added and the part it changed,
 * because both are silent when broken.
 *
 * The trace has been reachable only from a low-contrast pill in the
 * bottom-left corner of every page — a mark you find by already knowing it
 * is there. SNOW-921 added the network menu's "Debug log" row; SNOW-1415
 * moved it to the /offline/ debug page as an "Open debug log" button
 * (public/offline.html), when the menu became the per-area answer.
 *
 * That button is bound HERE rather than on that page, on the rule that the
 * surface owning a panel owns every way into it. The failure mode it guards is the one a template-only
 * change produces: a row that renders, takes a press and does nothing at
 * all, with every server-side test still green.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const MODULE = '../../static/js/debug_log_panel.js';

/**
 * The markup the reader binds against: includes/_debug_log_panel.html's
 * handle and body, plus the /offline/ page's "Open debug log" button.
 *
 * Trimmed to the elements this module actually looks up — the filter,
 * Copy and Clear are covered by the recorder's own contract — but the
 * strings <template> is mirrored in full because the module reads it at
 * import time through ``pwaStrings`` and would throw without it.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div data-testid="offline-debug-controls">
      <button type="button" data-open-debug-log>Open debug log</button>
    </div>
    <div id="debug-log">
      <button id="debug-log-handle" type="button" aria-expanded="false">
        <span id="debug-log-count">0</span>
      </button>
      <div id="debug-log-body" hidden>
        <input id="debug-log-enabled" type="checkbox">
        <select id="debug-log-filter"><option value=""></option></select>
        <span id="debug-log-status"></span>
        <button id="debug-log-close" type="button">×</button>
        <ol id="debug-log-list"></ol>
      </div>
      <template id="debug-log-strings-template">
        <span data-string="off">not recording</span>
        <span data-string="empty">Nothing recorded yet.</span>
        <span data-string="copied">copied</span>
        <span data-string="copy-failed">copy failed</span>
      </template>
    </div>
  `;
}

/**
 * A recorder stub carrying the surface this module reads. ``history()``
 * resolves empty so the module's deferred merge is a no-op rather than a
 * source of unhandled rejections.
 */
function stubRecorder() {
  window.pwaDebugLog = {
    entries: () => [],
    history: () => Promise.resolve([]),
    isEnabled: () => false,
    subscribe: () => {},
    clear: () => {},
    format: () => '',
  };
}

/** Re-import the reader fresh against the current fixture. */
async function loadPanel() {
  vi.resetModules();
  await import(MODULE);
  // The module defers its persisted-history merge past a promise.
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

/** Whether the trace panel is expanded. */
function panelOpen() {
  return !document.getElementById('debug-log-body').hidden;
}

/** The /offline/ page's "Open debug log" button. */
function menuRow() {
  return document.querySelector('[data-open-debug-log]');
}

beforeEach(async () => {
  localStorage.clear();
  buildFixture();
  stubRecorder();
  await import('../../static/js/i18n_strings.js');
});

// No teardown that removes ``window.pwaDebugLog``. Every previous test's
// module instance is still subscribed and still schedules repaints on an
// animation frame — ``vi.resetModules()`` gives a fresh module, it does not
// unbind the old one's callbacks — so a torn-down recorder is read by a
// frame that lands after the test that owned it. The stub is reinstalled by
// ``beforeEach`` instead, which is what those stale frames repaint against.

describe('the /offline/ entry point (SNOW-921, moved by SNOW-1415)', () => {
  it('opens the trace panel', async () => {
    await loadPanel();
    expect(panelOpen()).toBe(false);

    menuRow().click();

    expect(panelOpen()).toBe(true);
  });

  it('opens rather than toggles, however the panel was left', async () => {
    // The button is a request to READ the trace — not to flip whatever state it happens to
    // be in. A toggle here would close the panel of anyone who left it open
    // and then went looking for it in the menu, which is exactly the user
    // this entry point was added for.
    await loadPanel();
    menuRow().click();
    expect(panelOpen()).toBe(true);

    menuRow().click();

    expect(panelOpen()).toBe(true);
  });

  it('leaves the pill a toggle, which is what a pill is for', async () => {
    // The contrast is deliberate: the handle is the panel's own chrome and
    // sits next to it, so pressing it again to put it away is the obvious
    // reading. Asserted here so a later pass cannot "make them consistent"
    // without meeting the argument above.
    await loadPanel();
    const handle = document.getElementById('debug-log-handle');

    handle.click();
    expect(panelOpen()).toBe(true);

    handle.click();
    expect(panelOpen()).toBe(false);
  });

  it('binds nothing when the recorder is absent', async () => {
    // Both the row and this script are gated on the ``debug_log`` waffle
    // flag, but a shell cached across a flag change can still render one
    // without the other. The module's ``window.pwaDebugLog`` guard is what
    // stops the row becoming a control that throws.
    delete window.pwaDebugLog;
    await loadPanel();
    // Put it back for the stale frames described above; the instance under
    // test already took its early return at import time, so restoring the
    // global cannot bind it after the fact.
    stubRecorder();

    expect(() => menuRow().click()).not.toThrow();
    expect(panelOpen()).toBe(false);
  });

  it('is a no-op on a page with no button', async () => {
    // Every page renders the trace panel when the flag is on; the button is
    // on /offline/ only, so every other page has the panel and no button.
    menuRow().remove();
    await loadPanel();

    expect(panelOpen()).toBe(false);
    document.getElementById('debug-log-handle').click();
    expect(panelOpen()).toBe(true);
  });
});
