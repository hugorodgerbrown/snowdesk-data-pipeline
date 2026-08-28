/*
 * tests/js/test_sw_register_update_build_line_probe.js — the build line on
 * the SW-driven reveal, where nobody has asked the server anything.
 *
 * `pwa_version_check.js` reaches the banner off the back of its own
 * `/api/version` round trip and hands the verdict over. The SW path has no
 * such answer — a waiting worker is a fact about this browser — so it
 * borrows that module's probe (`window.pwaVersionProbe`) rather than
 * carrying a second copy of the same request. The globals are defined in
 * load order on the page, and this is read lazily at reveal time for
 * exactly that reason.
 *
 * Its own file because `sw_register.js` fills the line once per page and
 * can only be imported once per jsdom window — see the sibling
 * test_sw_register_update_feedback.js for the passed-verdict path.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const SHELL_BUILD = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

/** Stands in for pwa_version_check.js's `/api/version` round trip. */
const probe = vi.fn(() =>
  Promise.resolve({
    current: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    released_at: '2026-08-28T14:03:00+00:00',
    update_required: false,
  }),
);

/** @returns {HTMLElement} the build-line slot. */
const line = () => document.getElementById('sw-update-banner-versions');

beforeAll(async () => {
  document.head.innerHTML = `
    <meta name="pwa-app-version" content="${SHELL_BUILD}">
    <meta name="pwa-app-released-at" content="2026-08-27T09:12:00+00:00">`;
  document.body.innerHTML = `
    <div id="sw-update-banner" class="hidden">
      <p id="sw-update-banner-versions" hidden></p>
      <button type="button" id="sw-update-banner-reload">Reload</button>
    </div>`;

  Object.defineProperty(navigator, 'serviceWorker', {
    value: {
      controller: null,
      register: () =>
        Promise.resolve({ waiting: null, installing: null, addEventListener: () => {} }),
      getRegistration: () => Promise.resolve(null),
      getRegistrations: () => Promise.resolve([]),
      addEventListener: () => {},
      ready: Promise.resolve({ active: null }),
    },
    configurable: true,
  });
  vi.stubGlobal('fetch', () =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ enabled: true, sw_url: '/sw.js', kill: false }),
    }),
  );
  window.pwaVersionProbe = probe;

  await import('../../static/js/sw_register.js');
});

describe('a reveal with no verdict', () => {
  it('asks the version-check module rather than issuing its own request', async () => {
    window.pwaUpdateBanner.reveal();

    await vi.waitFor(() => expect(line().hidden).toBe(false));
    expect(probe).toHaveBeenCalledTimes(1);
    expect(line().textContent).toContain('aaaaaaa');
    expect(line().textContent).toContain('bbbbbbb');
  });

  it('probes once per page however often the banner is re-revealed', async () => {
    window.pwaUpdateBanner.reveal();
    window.pwaUpdateBanner.reveal();

    expect(probe).toHaveBeenCalledTimes(1);
  });
});
