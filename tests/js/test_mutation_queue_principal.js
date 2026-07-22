/*
 * tests/js/test_mutation_queue_principal.js — Vitest unit test for
 * static/js/mutation_queue.js's enqueue-time principal stamping
 * (SNOW-462, SNOW-496).
 *
 * Split out of test_mutation_queue.js: `db.js`'s `context()` (and so
 * `_currentPrincipal()`) memoises `<meta name="pwa-user-id">` once for the
 * life of the module, so a non-anonymous principal needs its own file with
 * the meta tag in place before the one-time dynamic import.
 */

import { describe, expect, it } from 'vitest';

const MUTATION_URL = '/api/test-mutation';
const USER_ID = '42';

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', USER_ID);
document.head.appendChild(meta);

document.body.innerHTML = '<span data-sync-badge class="hidden items-center"></span>';

await import('../../static/js/db.js');
await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/telemetry.js');
await import('../../static/js/mutation_queue.js');

// Let the one-time reconcile-on-load settle before this test's own write —
// see test_mutation_queue_toast_dismiss.js's docstring for the race this
// avoids.
await new Promise((resolve) => setTimeout(resolve, 50));

describe("enqueue()'s principal stamp", () => {
  it('reads the current principal from <meta name="pwa-user-id">', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    const rows = await window.pwaDb.getAll('queue:mutations');
    expect(rows[0].principal).toBe(USER_ID);
  });
});
