/*
 * tests/js/test_mutation_queue.js — Vitest unit tests for
 * static/js/mutation_queue.js (SNOW-376, SNOW-462, SNOW-496).
 *
 * Ports most of tests/e2e/test_mutation_queue.py (17 cases) onto the
 * Vitest + jsdom harness — everything here is "the queue's own logic",
 * the same "SW-lifecycle tests: real vs simulated" split the retired
 * Playwright file itself documented (docs/client-side-tests.md). No real
 * backoff delay is ever awaited: every retry test forces the next replay
 * to be immediately eligible by writing `next_attempt_at: 0` straight into
 * the row.
 *
 * The two SNOW-462 reconcile-on-load scenarios (account change /
 * uninitialised principal) — and the one-off "stamps principal from the
 * meta tag" case — need a DIFFERENT `<meta name="pwa-user-id">` in place
 * BEFORE `window.pwaDb`'s `context()` first memoises it, which static
 * imports (always hoisted above any other file-level code) can't
 * sequence for more than one distinct principal per file. Each of those
 * lives in its own single-purpose file instead:
 *   - test_mutation_queue_principal.js
 *   - test_mutation_queue_reconcile_account_change.js
 *   - test_mutation_queue_reconcile_uninitialised.js
 * `test_mutation_queue_account_change.py`'s own headline scenario (a real
 * report-submission mutation rather than a synthetic row) folds into
 * test_mutation_queue_reconcile_account_change.js using the same
 * synthetic-mutation technique this file uses throughout — the thing
 * under test is mutation_queue.js's reconcile path, not report.js's call
 * site, which the retired Playwright file never covered separately anyway.
 *
 * db.js / mutation_queue_core.js / telemetry.js / mutation_queue.js are
 * each browser IIFEs that assign frozen, non-configurable `window.*`
 * properties — they can only be imported ONCE per test file (Vitest
 * guarantees a fresh module graph per FILE, not per test; see
 * test_db_reset_required.js's docstring for the same constraint). All
 * four are imported once, statically, at the top of this file, matching
 * base.html's own load order; every test shares that one instance, with
 * no `<meta name="pwa-user-id">` in place — `context().user_id` is
 * memoised as anonymous (null) for this whole file, which is what the
 * drain-guard's mismatched-principal case needs ('SOMEONE_ELSE' !== null)
 * and every other case here is indifferent to. `beforeEach` deletes the
 * underlying IndexedDB (not the JS modules) for a clean slate, mirroring
 * test_db.js.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/db.js';
import '../../static/js/mutation_queue_core.js';
import '../../static/js/telemetry.js';
import '../../static/js/mutation_queue.js';

const DB_NAME = 'snowdesk-pwa-v1';
const MUTATION_URL = '/api/test-mutation';

function deleteDb() {
  return new Promise((resolve) => {
    try {
      const req = indexedDB.deleteDatabase(DB_NAME);
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
    } catch (_e) {
      resolve();
    }
  });
}

function buildFixture() {
  document.body.innerHTML = `
    <span data-sync-badge class="hidden items-center"></span>
    <div id="mutation-queue-toast" class="hidden -translate-y-full opacity-0">
      <span>Some changes couldn't be saved and won't be retried.</span>
      <button type="button" data-action="dismiss">×</button>
    </div>
  `;
}

function mutationRows() {
  return window.pwaDb.getAll('queue:mutations');
}

function eventRows() {
  return window.pwaDb.getAll('queue:events');
}

/** Poll `predicate` (sync or async) until truthy, or fail after `timeoutMs`. */
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

beforeAll(async () => {
  // Let the one-time, fire-and-forget _reconcilePrincipal() kicked off by
  // this file's single static import of mutation_queue.js settle before
  // any test's own IndexedDB writes can race it.
  await new Promise((resolve) => setTimeout(resolve, 50));
});

beforeEach(async () => {
  buildFixture();
  await deleteDb();
  Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
  // Default: no service worker in scope, matching the retired Playwright
  // file's "simulated SW" pattern for every case except Background Sync
  // feature-detection, which defines its own fake per test below.
  delete navigator.serviceWorker;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. Enqueue while offline → replays on 'online' → 2xx deletes the row.
// ---------------------------------------------------------------------------

describe('enqueue while offline, replay on reconnect', () => {
  it('does not attempt a network round-trip while offline, and drains on online', async () => {
    // Filters out telemetry.js's own /api/telemetry flush — that module
    // ALSO listens for 'online' and, by the time this test dispatches it,
    // has a genuine pwa.mutation.enqueued row of its own to flush; a
    // global fetch stub would otherwise double-count that unrelated call.
    const mutationCalls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url === MUTATION_URL) {
          mutationCalls.push(url);
          return new Response('', { status: 201 });
        }
        return new Response(null, { status: 204 });
      }),
    );

    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    expect((await mutationRows()).length).toBe(1);
    expect(mutationCalls.length).toBe(0);

    const enqueuedEvent = await waitFor(async () =>
      (await eventRows()).find((e) => e.event === 'pwa.mutation.enqueued'),
    );
    expect(enqueuedEvent.properties).toEqual({ method: 'POST', url: MUTATION_URL });

    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
    window.dispatchEvent(new Event('online'));

    const drainedEvent = await waitFor(async () => {
      if ((await mutationRows()).length !== 0) return false;
      return (await eventRows()).find((e) => e.event === 'pwa.mutation.drained');
    });
    expect(mutationCalls.length).toBe(1);
    expect(drainedEvent.properties.count).toBe(1);
  });
});

describe('a successful drain announces that server state moved', () => {
  it('tells the map to re-read BOTH user-data overlays', async () => {
    // A queued create is not on the map until it has actually replayed, so
    // this is the only moment either overlay can learn about it — a field
    // observation filed here was listed in its panel and absent from the
    // community-reports layer until the page was reloaded.
    //
    // Both announcements fire for ANY successful drain: this queue carries
    // no per-operation kind, and map.js's listeners are gated on their own
    // overlay being loaded, so the wrong-kind case costs one cheap refetch
    // and never a wrong picture.
    const favourites = vi.fn();
    const reports = vi.fn();
    document.addEventListener('snowdesk:favourites-changed', favourites);
    document.addEventListener('snowdesk:reports-changed', reports);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('', { status: 201 })),
    );

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    await waitFor(async () => (await mutationRows()).length === 0);

    expect(favourites).toHaveBeenCalled();
    expect(reports).toHaveBeenCalled();
    document.removeEventListener('snowdesk:favourites-changed', favourites);
    document.removeEventListener('snowdesk:reports-changed', reports);
  });
});

// ---------------------------------------------------------------------------
// 2. Identical Idempotency-Key across ≥2 successive replays.
// ---------------------------------------------------------------------------

describe('Idempotency-Key', () => {
  it('is identical across every replay of the same row', async () => {
    let callCount = 0;
    const capturedKeys = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url, init) => {
        callCount += 1;
        capturedKeys.push(init.headers['Idempotency-Key']);
        return new Response('', { status: callCount < 3 ? 500 : 201 });
      }),
    );

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    await waitFor(async () => {
      const rows = await mutationRows();
      return rows.length === 1 && rows[0].attempts === 1;
    });

    for (let i = 0; i < 2; i++) {
      const rows = await mutationRows();
      rows[0].next_attempt_at = 0;
      await window.pwaDb.put('queue:mutations', rows[0]);
      await window.pwaMutationQueue.drain();
    }

    await waitFor(async () => (await mutationRows()).length === 0);

    expect(callCount).toBe(3);
    expect(capturedKeys.length).toBe(3);
    expect(new Set(capturedKeys).size).toBe(1);
    expect(capturedKeys[0]).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 3. Permanent 4xx → failed on first attempt; toast + failed_permanent.
// ---------------------------------------------------------------------------

describe('permanent 4xx', () => {
  it.each([400, 401, 403, 404, 422])(
    'marks the row failed on the first attempt (%i)',
    async (status) => {
      let callCount = 0;
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => {
          callCount += 1;
          return new Response('', { status });
        }),
      );

      await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

      await waitFor(async () => {
        const rows = await mutationRows();
        const failed = rows.length === 1 && rows[0].status === 'failed';
        const toastEl = document.getElementById('mutation-queue-toast');
        const toastVisible = toastEl ? !toastEl.classList.contains('hidden') : false;
        const events = await eventRows();
        const hasEvent = events.some((e) => e.event === 'pwa.mutation.failed_permanent');
        return failed && toastVisible && hasEvent;
      });

      expect(callCount).toBe(1);

      const failedEvent = (await eventRows()).find(
        (e) => e.event === 'pwa.mutation.failed_permanent',
      );
      expect(failedEvent.properties.reason).toBe('permanent_4xx');
      expect(failedEvent.properties.attempts).toBe(1);
      expect((await mutationRows())[0].attempts).toBe(1);
    },
  );

  // The toast's "×" dismiss-click transition (slide-up before hiding) needs
  // its OWN button listener wired by mutation_queue.js's _wireToast() at
  // IIFE-load time against an already-present #mutation-queue-toast — this
  // file's fixture is rebuilt fresh per test AFTER that one-time wiring, so
  // that specific behaviour is covered in its own file instead:
  // test_mutation_queue_toast_dismiss.js.
});

// ---------------------------------------------------------------------------
// 4. Backoff — {408,429}/network failure schedules a retry; failed at 20.
// ---------------------------------------------------------------------------

describe('retryable statuses schedule backoff', () => {
  it.each([408, 429])(
    'advances next_attempt_at and increments attempts (%i)',
    async (status) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status })));

      const before = Date.now();
      await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

      await waitFor(async () => {
        const rows = await mutationRows();
        return rows.length === 1 && rows[0].attempts === 1;
      });
      const row = (await mutationRows())[0];

      expect(row.status).toBe('retry-scheduled');
      expect(row.attempts).toBe(1);
      const delay = row.next_attempt_at - before;
      expect(delay).toBeGreaterThanOrEqual(1000);
      expect(delay).toBeLessThanOrEqual(4000);
    },
  );
});

describe('network error', () => {
  it('is treated as a retry, not a permanent failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network error')));

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    await waitFor(async () => {
      const rows = await mutationRows();
      return rows.length === 1 && rows[0].attempts === 1;
    });
    expect((await mutationRows())[0].status).toBe('retry-scheduled');
  });
});

describe('max attempts', () => {
  it('marks the row failed once it reaches MAX_ATTEMPTS (20) without a 2xx', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 429 })));

    await window.pwaDb.put('queue:mutations', {
      idempotency_key: 'fixed-test-key',
      method: 'POST',
      url: MUTATION_URL,
      headers: {},
      body: null,
      created_at: new Date().toISOString(),
      attempts: 19,
      status: 'retry-scheduled',
      next_attempt_at: 0,
      principal: null,
    });
    await window.pwaMutationQueue.drain();

    const row = (await mutationRows())[0];
    expect(row.status).toBe('failed');
    expect(row.attempts).toBe(20);

    const events = await waitFor(async () => {
      const matches = (await eventRows()).filter(
        (e) => e.event === 'pwa.mutation.failed_permanent',
      );
      return matches.length ? matches : false;
    });
    expect(events.some((e) => e.properties.reason === 'max_attempts')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 5. Nav badge — live non-failed count; absent when the queue empties.
// ---------------------------------------------------------------------------

describe('nav sync badge', () => {
  it('shows the pending count and hides once the queue drains', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    const badge = document.querySelector('[data-sync-badge]');
    expect(badge.classList.contains('hidden')).toBe(false);
    expect(badge.textContent).toBe('2 changes queued');

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 201 })));
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
    await window.pwaMutationQueue.drain();

    await waitFor(() => document.querySelector('[data-sync-badge]').classList.contains('hidden'));
  });

  it('carries inline-flex only while it has a count (SNOW-445)', async () => {
    const badge = document.querySelector('[data-sync-badge]');
    expect(badge.classList.contains('hidden')).toBe(true);
    expect(badge.classList.contains('inline-flex')).toBe(false);

    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    expect(badge.classList.contains('hidden')).toBe(false);
    expect(badge.classList.contains('inline-flex')).toBe(true);
  });

  it('reads "1 change queued" in the singular', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    expect(document.querySelector('[data-sync-badge]').textContent).toBe('1 change queued');
  });
});

// ---------------------------------------------------------------------------
// 6. registration.sync.register('mutation-queue') is feature-detected.
// ---------------------------------------------------------------------------

describe('Background Sync feature-detection', () => {
  function fakeServiceWorker({ syncSupported }) {
    const syncCalls = [];
    const fakeRegistration = {
      installing: null,
      waiting: null,
      active: null,
      update: () => Promise.resolve(),
      ...(syncSupported
        ? {
            sync: {
              register: (tag) => {
                syncCalls.push(tag);
                return Promise.resolve();
              },
            },
          }
        : {}),
    };
    Object.defineProperty(navigator, 'serviceWorker', {
      configurable: true,
      value: {
        addEventListener: () => {},
        removeEventListener: () => {},
        ready: Promise.resolve(fakeRegistration),
        register: () => Promise.resolve(fakeRegistration),
        getRegistrations: () => Promise.resolve([]),
        controller: null,
      },
    });
    return syncCalls;
  }

  it('registers the shared SYNC_TAG when SyncManager is present', async () => {
    const syncCalls = fakeServiceWorker({ syncSupported: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 201 })));

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    await waitFor(() => syncCalls.length > 0);
    expect(syncCalls).toEqual(['mutation-queue']);
  });

  it('skips registration without error when SyncManager is absent', async () => {
    const syncCalls = fakeServiceWorker({ syncSupported: false });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 201 })));

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(syncCalls).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// clear() — empties the store and hides the nav badge.
// ---------------------------------------------------------------------------

describe('clear()', () => {
  it('empties the store and hides the nav badge', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    expect((await mutationRows()).length).toBe(2);

    await window.pwaMutationQueue.clear();

    expect(await mutationRows()).toEqual([]);
    expect(document.querySelector('[data-sync-badge]').classList.contains('hidden')).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 7. SNOW-462 drain-guard — discards a row stamped for a different principal.
// ---------------------------------------------------------------------------

describe('drain-guard (SNOW-462)', () => {
  it('discards (never replays) a row stamped for a different principal', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 201 }));
    vi.stubGlobal('fetch', fetchMock);

    // This file's current principal is anonymous (null) throughout — any
    // non-null stamped principal is, by construction, a mismatch.
    await window.pwaDb.put('queue:mutations', {
      idempotency_key: 'mismatched-principal-key',
      method: 'POST',
      url: MUTATION_URL,
      headers: {},
      body: null,
      created_at: new Date().toISOString(),
      attempts: 0,
      status: 'queued',
      next_attempt_at: 0,
      principal: 'SOMEONE_ELSE',
    });
    await window.pwaMutationQueue.drain();

    await waitFor(async () => (await mutationRows()).length === 0);
    expect(fetchMock).not.toHaveBeenCalled();

    const discardedEvent = await waitFor(async () =>
      (await eventRows()).find((e) => e.event === 'pwa.mutation.discarded'),
    );
    expect(discardedEvent.properties.reason).toBe('principal_mismatch');
  });
});

// ---------------------------------------------------------------------------
// 8. Offline guard — a drain while offline must not spend an attempt.
// ---------------------------------------------------------------------------

describe('offline drain guard', () => {
  it('does not advance attempts across drains while navigator.onLine is false', async () => {
    // Records only the mutation replay — telemetry.js's own flush shares
    // this stub and would otherwise be counted as a queue round-trip.
    const mutationCalls = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url === MUTATION_URL) {
          mutationCalls.push(url);
          throw new TypeError('network error');
        }
        return new Response(null, { status: 204 });
      }),
    );

    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    expect((await mutationRows())[0].attempts).toBe(0);

    // Three passes stand in for the lifecycle triggers that keep firing
    // while a tab stays open offline (online, visibilitychange, the 30s
    // interval). Each unguarded pass burns one of the row's 20 attempts.
    await window.pwaMutationQueue.drain();
    await window.pwaMutationQueue.drain();
    await window.pwaMutationQueue.drain();

    const row = (await mutationRows())[0];
    expect(row.attempts).toBe(0);
    expect(row.status).toBe('queued');
    expect(mutationCalls).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 9. pagehide drains as the tab tears down (mirrors telemetry.js).
// ---------------------------------------------------------------------------

describe('pagehide trigger', () => {
  it('drains the queue when the tab is torn down', async () => {
    Object.defineProperty(navigator, 'onLine', { value: false, configurable: true });
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    expect((await mutationRows()).length).toBe(1);

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 201 })));
    Object.defineProperty(navigator, 'onLine', { value: true, configurable: true });
    window.dispatchEvent(new Event('pagehide'));

    await waitFor(async () => (await mutationRows()).length === 0);
  });
});

describe('failed rows do not starve the queue (SNOW-617)', () => {
  /**
   * Write `count` permanently-failed rows, oldest first.
   *
   * They are never deleted — the nav badge's error state is defined on
   * their presence (docs/mutation-queue.md) — so they accumulate at the
   * head of the store, which is what made the old batch selection starve.
   *
   * @param {number} count
   * @returns {Promise<void>}
   */
  async function seedFailedRows(count) {
    for (let i = 0; i < count; i += 1) {
      await window.pwaDb.put('queue:mutations', {
        idempotency_key: `failed-${i}`,
        method: 'POST',
        url: MUTATION_URL,
        headers: {},
        body: null,
        created_at: new Date().toISOString(),
        attempts: 20,
        status: 'failed',
        next_attempt_at: 0,
        principal: null,
      });
    }
  }

  it('drains an eligible row sitting behind a full batch of failed ones', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    // BATCH_SIZE is 50: exactly enough failed rows to fill a pass under
    // the old read-then-filter order, leaving the queued row below
    // permanently unreachable.
    await seedFailedRows(50);
    await window.pwaDb.put('queue:mutations', {
      idempotency_key: 'eligible-one',
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

    await window.pwaMutationQueue.drain();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const remaining = await mutationRows();
    expect(remaining.map((r) => r.idempotency_key)).not.toContain('eligible-one');
    // The failed rows are still there — deleting them would take the
    // badge's error state with them.
    expect(remaining).toHaveLength(50);
  });

  it('leaves a failed row in the store rather than pruning it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 400 })));

    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });
    await waitFor(async () => {
      const rows = await mutationRows();
      return rows.length === 1 && rows[0].status === 'failed';
    });

    // Pinning the retention decision: the badge swaps to its error tokens
    // "when any row in the store has status: 'failed'", so the row is what
    // carries that state. Starvation is solved by how the batch is
    // selected, not by deleting the evidence.
    const rows = await mutationRows();
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('failed');
  });
});
