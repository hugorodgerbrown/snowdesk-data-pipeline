/*
 * tests/js/test_observations_offline_reset_required.js — the offline row
 * store when the app is in Reset Required (SNOW-661).
 *
 * Its own file for the reason test_db_reset_required.js is: db.js assigns a
 * frozen, NON-CONFIGURABLE window.pwaDb, so a test that has imported the
 * real wrapper cannot substitute one whose isResetRequired() answers true.
 * Vitest isolates each test FILE in a fresh module graph, which is what
 * makes the stub below possible — nothing here imports db.js.
 *
 * The behaviour under test is the guard every DB-touching module in this
 * codebase carries: in the terminal state the wrapper's data is not to be
 * trusted, so the panel gets nothing to paint and draws its failure line
 * rather than repainting from a database the app has given up on.
 */

import { beforeEach, describe, expect, it } from 'vitest';

// Before the module loads, so its dbReady() check sees the latched state
// from the first call.
window.pwaDb = {
  isResetRequired: () => true,
  get: () => Promise.reject(new Error('should never be called')),
  put: () => Promise.reject(new Error('should never be called')),
  context: () => ({ user_id: null }),
};

await import('../../static/js/observations_offline.js');

beforeEach(() => {
  document.body.innerHTML = '<div data-report-rows></div>';
});

describe('Reset Required', () => {
  it('reads back nothing at all', async () => {
    expect(await window.pwaObservationsOffline.read()).toBeNull();
  });

  it('writes nothing, rather than throwing into the swap it rides on', async () => {
    // The stubbed put() rejects; the guard is what keeps the listener from
    // reaching it, and a rejection escaping here would break the panel's
    // rows arriving.
    document.body.dispatchEvent(
      new CustomEvent('htmx:beforeSwap', {
        detail: {
          target: document.querySelector('[data-report-rows]'),
          serverResponse: '<ul><li>Whumpfing</li></ul>',
          isError: false,
          shouldSwap: true,
        },
      }),
    );

    await expect(
      window.pwaObservationsOffline.write('<ul><li>Whumpfing</li></ul>'),
    ).resolves.toBeUndefined();
  });
});
