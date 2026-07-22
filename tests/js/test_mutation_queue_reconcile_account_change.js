/*
 * tests/js/test_mutation_queue_reconcile_account_change.js — Vitest unit
 * test for static/js/mutation_queue.js's SNOW-462 reconcile-on-load
 * account-change path (SNOW-496).
 *
 * Ports tests/e2e/test_mutation_queue.py's
 * test_reconcile_clears_queue_on_account_change AND
 * tests/e2e/test_mutation_queue_account_change.py's headline scenario —
 * both prove the same thing (a fresh page load under a different
 * principal clears the WHOLE queue via the reconcile-on-load path, not
 * the drain-guard), differing only in whether the queued mutation came
 * from a synthetic row or a real report-submission call site; the module
 * under test — mutation_queue.js's own reconcile logic — is identical
 * either way, so one synthetic-row case here covers both.
 *
 * A real second "page load" as a different user can't be simulated by
 * re-importing the module (db.js's `Object.defineProperty(window, 'pwaDb',
 * …)` is non-configurable, so a module can only load once per test file —
 * see test_mutation_queue_toast_dismiss.js's docstring for the same
 * constraint). Instead this seeds the "prior session" state directly —
 * one queued row + the `mutations.principal` baseline, both stamped for
 * user A — via `window.pwaDb` (imported alone, first) BEFORE the one
 * full-stack import that represents user B's fresh load; that single
 * `_wireLifecycle()` run's reconcile pass is what this test observes.
 */

import { describe, expect, it } from 'vitest';

const MUTATION_URL = '/api/test-mutation';
const USER_A = 'user-a';
const USER_B = 'user-b';

document.body.innerHTML = '<span data-sync-badge class="hidden items-center"></span>';

// db.js alone first, to seed "user A's prior session" state without
// running mutation_queue.js's reconcile against it yet.
await import('../../static/js/db.js');
await window.pwaDb.put('queue:mutations', {
  idempotency_key: 'account-change-key',
  method: 'POST',
  url: MUTATION_URL,
  headers: {},
  body: null,
  created_at: new Date().toISOString(),
  attempts: 0,
  status: 'queued',
  next_attempt_at: 0,
  principal: USER_A,
});
await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: USER_A });

// User B signs in on the same browser and the page "reloads" — re-render
// <meta name="pwa-user-id"> as B, then load the rest of the stack for the
// first (and only) time in this file.
const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', USER_B);
document.head.appendChild(meta);

await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/telemetry.js');
await import('../../static/js/mutation_queue.js');

/** Poll `predicate` until truthy, or fail after `timeoutMs`. */
async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let last;
  while (Date.now() < deadline) {
    last = await predicate();
    if (last) return last;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`condition never became true (last=${JSON.stringify(last)})`);
}

describe('reconcile-on-load — account change', () => {
  it("clears the WHOLE queue when B's load observes A's baseline principal", async () => {
    await waitFor(async () => (await window.pwaDb.getAll('queue:mutations')).length === 0);

    const discardedEvent = await waitFor(async () => {
      const events = await window.pwaDb.getAll('queue:events');
      return events.find(
        (e) => e.event === 'pwa.mutation.discarded' && e.properties.reason === 'account_change',
      );
    });
    expect(discardedEvent.properties.count).toBe(1);
  });
});
