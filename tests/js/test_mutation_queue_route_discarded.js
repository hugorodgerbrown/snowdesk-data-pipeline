/*
 * tests/js/test_mutation_queue_route_discarded.js — a route drawn offline
 * by one account is discarded when another signs in, and the user is told
 * (SNOW-1262, static/js/mutation_queue.js's _reconcilePrincipal).
 *
 * An account change always involves a page load, so the reconcile on load
 * is where the queue clears the other account's rows — that much is
 * SNOW-462's, covered by test_mutation_queue_reconcile_account_change.js.
 * What this adds is the telling: the discarded set held a route-create row,
 * so the page shows #mutation-queue-toast with the discard line from the
 * strings template rather than the failure line.
 *
 * Seeded the same way as that file — db.js first, "user A's session"
 * written, then the rest of the stack loaded once under user B's meta tag.
 */

import { describe, expect, it } from 'vitest';

const USER_A = 'user-a';
const USER_B = 'user-b';
const DISCARDED = 'A route drawn offline under another account was discarded.';

document.body.innerHTML = `
  <span data-sync-badge class="hidden items-center"></span>
  <div id="mutation-queue-toast" class="hidden -translate-y-full opacity-0">
    <span data-toast-body>Some changes couldn't be saved and won't be retried.</span>
    <button type="button" data-action="dismiss">×</button>
  </div>
  <template id="mutation-queue-strings-template">
    <span data-string="failed">Some changes couldn't be saved and won't be retried.</span>
    <span data-string="route-discarded">${DISCARDED}</span>
  </template>`;

await import('../../static/js/i18n_strings.js');
await import('../../static/js/db.js');
await window.pwaDb.put('queue:mutations', {
  idempotency_key: 'route-of-a',
  kind: 'route-create',
  method: 'POST',
  url: '/routes/drawn/',
  headers: {},
  body: '{"points": [[7, 46], [7, 46.001]], "name": "A"}',
  created_at: new Date().toISOString(),
  attempts: 0,
  status: 'queued',
  next_attempt_at: 0,
  principal: USER_A,
});
await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: USER_A });

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', USER_B);
document.head.appendChild(meta);

await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/telemetry.js');
await import('../../static/js/mutation_queue.js');

async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('condition never became true');
}

describe("another account's route drawn offline", () => {
  it('is discarded, and the toast says so', async () => {
    await waitFor(async () => (await window.pwaDb.getAll('queue:mutations')).length === 0);

    const toast = document.getElementById('mutation-queue-toast');
    await waitFor(() => !toast.classList.contains('hidden'));
    expect(toast.querySelector('[data-toast-body]').textContent).toBe(DISCARDED);
  });

  it('is never listed to the account now signed in', async () => {
    expect(await window.pwaMutationQueue.list({ kind: 'route-create' })).toEqual([]);
  });
});
