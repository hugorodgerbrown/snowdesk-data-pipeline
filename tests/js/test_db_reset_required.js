/*
 * tests/js/test_db_reset_required.js — Vitest unit test for db.js's
 * Reset Required latch (SNOW-375, SNOW-495).
 *
 * Split into its own file because `static/js/db.js`'s `_resetRequired` flag
 * is a one-way latch for the module's lifetime — once tripped, every
 * subsequent `open()` call in that module instance rejects, for good.
 * Vitest isolates each test FILE in a fresh module graph (not each `it`),
 * so putting this case in its own file is what gives it a pristine
 * `window.pwaDb` to trip.
 *
 * Ported from `tests/e2e/test_pwa_db.py::test_reset_required_on_migration_failure`.
 * We can't make db.js's internal migration throw directly, but opening the
 * DB at a version ABOVE db.js's own `DB_VERSION` first guarantees the next
 * `pwaDb.open()` hits a genuine `VersionError` (browsers refuse to open at
 * an older version than the one already on disk) — which routes through
 * the same `_enterResetRequired` path as a real migration failure.
 *
 * The real `#pwa-reset-required` overlay lives in `templates/base.html`,
 * which isn't rendered in this harness — the minimal markup db.js actually
 * touches is injected directly into `document.body`.
 */

import { beforeEach, expect, it } from 'vitest';

import '../../static/js/db.js';

const DB_NAME = 'snowdesk-pwa-v1';

beforeEach(() => {
  document.body.innerHTML = '<div id="pwa-reset-required" class="hidden"></div>';
});

it('enters Reset Required when db.js hits a VersionError', async () => {
  // Open (and close) the DB at a version above db.js's DB_VERSION so the
  // next attempt from db.js can only fail.
  await new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 99);
    req.onsuccess = () => {
      req.result.close();
      resolve();
    };
    req.onerror = () => reject(req.error);
    req.onupgradeneeded = () => {};
  });

  // Now force db.js to attempt an open — it will hit VersionError.
  await expect(window.pwaDb.open()).rejects.toBeTruthy();

  expect(window.pwaDb.isResetRequired()).toBe(true);
  const overlay = document.getElementById('pwa-reset-required');
  expect(overlay.classList.contains('hidden')).toBe(false);

  // Subsequent open() calls must also reject without hitting IndexedDB.
  await expect(window.pwaDb.open()).rejects.toBeTruthy();
});
