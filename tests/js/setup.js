/*
 * tests/js/setup.js — Vitest global setup for the JS unit-test harness (SNOW-495).
 *
 * Registered via `vitest.config.mjs`'s `setupFiles`. Runs once per test file,
 * before that file's module graph is imported, so every `test_*.js` file
 * sees a jsdom global with a working `indexedDB` already in place.
 *
 * `fake-indexeddb/auto` registers an in-memory `indexedDB` / `IDBKeyRange` /
 * `IDBFactory` implementation on `globalThis` — jsdom itself doesn't ship
 * IndexedDB (https://github.com/jsdom/jsdom/issues/2306), which is exactly
 * the surface `static/js/db.js` needs.
 */

import 'fake-indexeddb/auto';
