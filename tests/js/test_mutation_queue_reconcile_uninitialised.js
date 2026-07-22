/*
 * tests/js/test_mutation_queue_reconcile_uninitialised.js — Vitest unit
 * test for static/js/mutation_queue.js's SNOW-462 reconcile-on-load
 * "no baseline principal yet" path (SNOW-496).
 *
 * Ports tests/e2e/test_mutation_queue.py's
 * test_reconcile_clears_queue_when_principal_never_recorded — a
 * pre-existing `queue:mutations` row with NO corresponding
 * `mutations.principal` baseline in `meta:app` is discarded on the first
 * reconcile pass with reason `'principal_uninitialised'`, distinct from a
 * genuine account change.
 *
 * Seeds the row directly via `window.pwaDb` (db.js imported alone, first)
 * — bypassing `enqueue()`, whose own reconcile-on-load would otherwise
 * already have run — so the one full-stack import below is the FIRST
 * reconcile this DB has ever seen, finding both an absent baseline and a
 * non-empty queue. See test_mutation_queue_toast_dismiss.js's docstring
 * for why a module can only load once per test file, which is what makes
 * this seed-then-single-import shape necessary.
 */

import { describe, expect, it } from 'vitest';

const MUTATION_URL = '/api/test-mutation';

document.body.innerHTML = '<span data-sync-badge class="hidden items-center"></span>';

await import('../../static/js/db.js');
await window.pwaDb.put('queue:mutations', {
  idempotency_key: 'uninitialised-principal-key',
  method: 'POST',
  url: MUTATION_URL,
  headers: {},
  body: null,
  created_at: new Date().toISOString(),
  attempts: 0,
  status: 'queued',
  next_attempt_at: 0,
  principal: null,
});

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

describe('reconcile-on-load — no baseline principal ever recorded', () => {
  it("discards the pre-existing row with reason 'principal_uninitialised'", async () => {
    await waitFor(async () => (await window.pwaDb.getAll('queue:mutations')).length === 0);

    const discardedEvent = await waitFor(async () => {
      const events = await window.pwaDb.getAll('queue:events');
      return events.find(
        (e) =>
          e.event === 'pwa.mutation.discarded' &&
          e.properties.reason === 'principal_uninitialised',
      );
    });
    expect(discardedEvent.properties.count).toBe(1);
  });
});
