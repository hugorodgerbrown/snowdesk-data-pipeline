/*
 * tests/js/test_mutation_queue_drain_on_load.js — Vitest unit test for
 * static/js/mutation_queue.js's SNOW-860 drain-on-load trigger.
 *
 * Page load was not one of the queue's drain triggers. `_wireLifecycle()`
 * painted the badge from the store and wired four triggers, none of which
 * fires for a tab that simply opened:
 *
 *   - `online` fires on an offline→online TRANSITION, never for a tab that
 *     was online the whole time;
 *   - `visibilitychange` needs a tab switch;
 *   - the 30s timer is created fresh on every load, so refreshing restarted
 *     the only clock that would have sent the row;
 *   - `pagehide` fires as the tab tears down, by which point a replay fetch
 *     is racing the teardown.
 *
 * So a row left over from a previous session was DISPLAYED and never
 * attempted. Reported as "1 change queued" sitting there while online,
 * clearing only after navigating around long enough for one of the other
 * triggers to land.
 *
 * The module can only be imported once per test file — `db.js` installs
 * `window.pwaDb` with `Object.defineProperty(…, {configurable: false})` —
 * so the whole point of this being its own file is that the "previous
 * session's" row is seeded through `pwaDb` (imported alone, first) BEFORE
 * the single full-stack import that represents the fresh load. That one
 * `_wireLifecycle()` run is what this test observes.
 *
 * The assertion is deliberately that NO event is dispatched: dispatching
 * `online` or `pagehide` here would pass against the old code too and
 * prove nothing.
 */

import { describe, expect, it, vi } from 'vitest';

const MUTATION_URL = '/api/test-mutation';
const PRINCIPAL = 'user-a';

document.body.innerHTML = '<span data-sync-badge class="hidden items-center"></span>';

// Online, so `drain()`'s connectivity guard lets the replay through.
Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 201 })));

// db.js alone first, to seed the previous session's leftover row without
// running mutation_queue.js's lifecycle against it yet.
await import('../../static/js/db.js');
await window.pwaDb.put('queue:mutations', {
  idempotency_key: 'drain-on-load-key',
  method: 'POST',
  url: MUTATION_URL,
  headers: {},
  body: null,
  created_at: new Date().toISOString(),
  attempts: 0,
  status: 'queued',
  next_attempt_at: 0,
  principal: PRINCIPAL,
});
// Same principal, so reconcile-on-load keeps the row rather than clearing
// it — this test is about the drain, not the account-change path.
await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: PRINCIPAL });

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-user-id');
meta.setAttribute('content', PRINCIPAL);
document.head.appendChild(meta);

// The "fresh load".
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

describe('drain on load (SNOW-860)', () => {
  it('replays a leftover row with no event dispatched and no timer elapsed', async () => {
    await waitFor(async () => (await window.pwaDb.getAll('queue:mutations')).length === 0);

    // Replayed to the right place, with the row's own stable key — the
    // load drain is the ordinary drain path, not a shortcut around it.
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe(MUTATION_URL);
    expect(init.headers['Idempotency-Key']).toBe('drain-on-load-key');
  });

  it('clears the badge it painted on the way in', async () => {
    // The badge is the symptom the user reported: it was painted from the
    // store at wire time and then had nothing to correct it.
    await waitFor(() => document.querySelector('[data-sync-badge]').classList.contains('hidden'));
    expect(document.querySelector('[data-sync-badge]').classList.contains('hidden')).toBe(true);
  });
});
