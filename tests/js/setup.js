/*
 * tests/js/setup.js — Vitest global setup for the JS unit-test harness (SNOW-495, SNOW-496).
 *
 * Registered via `vitest.config.mjs`'s `setupFiles`. Runs once per test file,
 * before that file's module graph is imported, so every `test_*.js` file
 * sees a jsdom global with a working `indexedDB` already in place.
 *
 * `fake-indexeddb/auto` registers an in-memory `indexedDB` / `IDBKeyRange` /
 * `IDBFactory` implementation on `globalThis` — jsdom itself doesn't ship
 * IndexedDB (https://github.com/jsdom/jsdom/issues/2306), which is exactly
 * the surface `static/js/db.js` needs.
 *
 * SNOW-496: two more harmless, always-on stubs jsdom doesn't ship, needed by
 * `home_intro.js` / `map_help.js`:
 *
 *   - `window.matchMedia` — jsdom throws "not implemented" on the bare
 *     property; both modules call it once for `prefers-reduced-motion`.
 *     Defaults to `matches: false` (motion allowed) — tests that care about
 *     the reduced-motion branch override it per-test.
 *   - `Element.prototype.scrollIntoView` — jsdom doesn't implement layout at
 *     all, so this is a no-op; `map_help.js`'s `render()` calls it on every
 *     step change.
 *
 * SNOW-672: `signin_cta.js` is imported here rather than stubbed. It is a
 * real module, and on the page it is always loaded ahead of the three
 * surfaces that call it (see the script tags in `public/home.html`), so a
 * test file importing `favourites.js` / `routes.js` / `report.js` should see
 * `window.snowdeskSigninCta` for the same reason the browser does.
 *
 * Deliberately NOT stubbed globally here: `fetch` and `sendBeacon`. Unlike
 * the two DOM APIs above, tests that exercise `telemetry.js` /
 * `mutation_queue.js` need to assert ON the calls those modules make, so
 * each such test FILE installs its own `vi.stubGlobal('fetch', vi.fn())` /
 * `navigator.sendBeacon = vi.fn()` — see test_telemetry.js and
 * test_mutation_queue.js.
 */

import 'fake-indexeddb/auto';

import '../../static/js/signin_cta.js';

if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  });
}

if (typeof Element.prototype.scrollIntoView !== 'function') {
  Element.prototype.scrollIntoView = () => {};
}
