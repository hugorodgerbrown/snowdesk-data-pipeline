/*
 * tests/js/test_pwa_sw_version_probe.js — the /_sw-version/ worker list
 * (SNOW-517, SNOW-1027).
 *
 * After a deploy a browser often holds two service workers, one running and
 * one waiting. DevTools labels them with Chrome's own counter, which says
 * nothing about which build each is. The staff page lists every worker the
 * registration holds with the CACHE_VERSION each reports over
 * `shell-identity`, and marks the one controlling the page and any that
 * match the deployed version.
 *
 * Harness notes
 * -------------
 * The probe is a load-time IIFE, imported once. The registration's slots
 * are mutable stubs, and each test calls `window.pwaSwVersionProbe.render()`
 * after arranging them, which is what the probe's own listeners call.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

const DEPLOYED = 'snowdesk-shell-new000000001';
const OLD = 'snowdesk-shell-old000000001';

/**
 * A stand-in ServiceWorker that answers `shell-identity` with `shell`, or
 * never for `null`.
 *
 * @param {string} state
 * @param {string | null} shell
 * @returns {object}
 */
function fakeWorker(state, shell) {
  return {
    state: state,
    addEventListener: () => {},
    postMessage: (data, transfer) => {
      if (data && data.type === 'shell-identity' && shell !== null) {
        transfer[0].postMessage({ type: 'shell-identity', cache: shell });
      }
    },
  };
}

const active = fakeWorker('activated', OLD);

const registration = {
  active: active,
  waiting: null,
  installing: null,
  addEventListener: () => {},
};

Object.defineProperty(navigator, 'serviceWorker', {
  value: {
    controller: active,
    getRegistration: () => Promise.resolve(registration),
    addEventListener: () => {},
  },
  configurable: true,
});

/** @returns {string[][]} each rendered row's slot, state, version and note. */
function rows() {
  return [...document.querySelectorAll('[data-sw-workers-body] tr')].map((tr) =>
    ['slot', 'state', 'shell', 'note'].map(
      (field) => tr.querySelector(`[data-field="${field}"]`)?.textContent ?? '',
    ),
  );
}

beforeAll(async () => {
  document.body.innerHTML = `
    <dd data-testid="sw-deployed-version">${DEPLOYED}</dd>
    <dd data-testid="sw-live-version">—</dd>
    <table data-testid="sw-workers"><tbody data-sw-workers-body>
      <tr><td colspan="3">—</td></tr>
    </tbody></table>
    <template id="sw-worker-strings">
      <span data-string="active">Active</span>
      <span data-string="waiting">Waiting</span>
      <span data-string="installing">Installing</span>
      <span data-string="controls">controls this page</span>
      <span data-string="deployed">= deployed</span>
      <span data-string="no-answer">no answer</span>
    </template>
    <template id="sw-worker-row-template">
      <tr>
        <td data-field="slot"></td><td data-field="state"></td>
        <td><span data-field="shell"></span><span data-field="note"></span></td>
      </tr>
    </template>`;
  await import('../../static/js/pwa_sw_version_probe.js');
});

describe('the worker list', () => {
  it('lists a running and a waiting worker with the version each holds', async () => {
    // The staging screenshot: one worker activated, one waiting.
    registration.waiting = fakeWorker('installed', DEPLOYED);

    await window.pwaSwVersionProbe.render();

    expect(rows()).toEqual([
      ['Active', 'activated', OLD, ' (controls this page)'],
      ['Waiting', 'installed', DEPLOYED, ' (= deployed)'],
    ]);
    expect(document.querySelector('[data-testid="sw-live-version"]').textContent).toBe(OLD);
  });

  it('lists an installing worker too', async () => {
    registration.waiting = null;
    registration.installing = fakeWorker('installing', DEPLOYED);

    await window.pwaSwVersionProbe.render();

    expect(rows().map((row) => row[0])).toEqual(['Active', 'Installing']);
    registration.installing = null;
  });

  it('says so when a worker does not answer', async () => {
    registration.waiting = fakeWorker('installed', null);

    vi.useFakeTimers();
    try {
      const pending = window.pwaSwVersionProbe.render();
      await vi.advanceTimersByTimeAsync(4100);
      await pending;
    } finally {
      vi.useRealTimers();
    }

    expect(rows()[1]).toEqual(['Waiting', 'installed', 'no answer', '']);
    registration.waiting = null;
  });
});
