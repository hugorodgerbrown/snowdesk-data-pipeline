/*
 * tests/js/test_mutation_queue_route_create.js — the mutation queue's
 * SNOW-1262 additions, on the page side (static/js/mutation_queue.js).
 *
 * A route drawn offline is the first queued mutation whose caller needs to
 * know, on a LATER load, what became of it. So the queue grew, additively:
 *
 *   - ``enqueue`` takes the caller's own Idempotency-Key and a ``kind``, and
 *     resolves to the key;
 *   - ``list({kind})`` reads the current principal's rows;
 *   - a successful replay of a ``kind`` row leaves an acknowledgement in
 *     ``meta:app`` (``acknowledged(key)``) before the row is deleted;
 *   - a refusal's status and JSON ``error`` stay on the row;
 *   - ``discard(key)`` throws away a failed row;
 *   - every change raises ``pwa:mutation-queue-changed``, and a drain that
 *     landed a route raises ``snowdesk:routes-changed``.
 *
 * The worker's half of the same records is in test_sw.js; the account
 * change that discards a route is in
 * test_mutation_queue_route_discarded.js (it needs its own principal before
 * db.js memoises one — see test_mutation_queue.js's header).
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/db.js';
import '../../static/js/mutation_queue_core.js';
import '../../static/js/telemetry.js';
import '../../static/js/mutation_queue.js';

const DB_NAME = 'snowdesk-pwa-v1';
const CREATE_URL = '/routes/drawn/';
const BODY = JSON.stringify({ points: [[7, 46], [7, 46.001]], name: 'Mont Fort' });

function deleteDb() {
  return new Promise((resolve) => {
    const req = indexedDB.deleteDatabase(DB_NAME);
    req.onsuccess = () => resolve();
    req.onerror = () => resolve();
  });
}

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

/** A route-create enqueue, as route_draw.js makes it. */
function enqueueRoute(key) {
  return window.pwaMutationQueue.enqueue({
    method: 'POST',
    url: CREATE_URL,
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': 'csrf' },
    body: BODY,
    idempotencyKey: key,
    kind: 'route-create',
  });
}

/** Stub fetch: the create endpoint answers `status` with `body`. */
function answer(status, body) {
  const calls = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url, init) => {
      if (url === CREATE_URL) {
        calls.push(init);
        return new Response(body === undefined ? '' : JSON.stringify(body), { status });
      }
      return new Response(null, { status: 204 });
    }),
  );
  return calls;
}

function setOnline(online) {
  Object.defineProperty(navigator, 'onLine', { value: online, configurable: true });
}

beforeAll(async () => {
  await new Promise((resolve) => setTimeout(resolve, 50));
});

beforeEach(async () => {
  document.body.innerHTML = `
    <span data-sync-badge class="hidden items-center"></span>
    <div id="mutation-queue-toast" class="hidden -translate-y-full opacity-0">
      <span data-toast-body>Some changes couldn't be saved and won't be retried.</span>
      <button type="button" data-action="dismiss">×</button>
    </div>`;
  await deleteDb();
  setOnline(false);
  delete navigator.serviceWorker;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the caller's key", () => {
  it('queues the row under the key the caller passed, and resolves to it', async () => {
    const key = await enqueueRoute('draft-key-1');

    expect(key).toBe('draft-key-1');
    const rows = await window.pwaDb.getAll('queue:mutations');
    expect(rows.map((r) => [r.idempotency_key, r.kind])).toEqual([['draft-key-1', 'route-create']]);
  });

  it('never queues the same key twice', async () => {
    await enqueueRoute('draft-key-1');
    await enqueueRoute('draft-key-1');

    expect(await window.pwaDb.getAll('queue:mutations')).toHaveLength(1);
  });

  it('mints a key when the caller passes none, as it always did', async () => {
    const key = await window.pwaMutationQueue.enqueue({ method: 'POST', url: '/x/' });

    expect(typeof key).toBe('string');
    expect(key.length).toBeGreaterThan(0);
  });

  it('keeps the body and headers out of the enqueue telemetry', async () => {
    await enqueueRoute('draft-key-1');

    const event = await waitFor(async () =>
      (await window.pwaDb.getAll('queue:events')).find((e) => e.event === 'pwa.mutation.enqueued'),
    );
    expect(event.properties).toEqual({ method: 'POST', url: CREATE_URL, kind: 'route-create' });
  });
});

describe('list()', () => {
  it("lists the current principal's rows of one kind, without headers", async () => {
    await enqueueRoute('mine');
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: '/favourites/' });
    await window.pwaDb.put('queue:mutations', {
      idempotency_key: 'theirs',
      kind: 'route-create',
      principal: 'someone-else',
      status: 'queued',
      body: BODY,
    });

    const rows = await window.pwaMutationQueue.list({ kind: 'route-create' });

    expect(rows.map((r) => r.idempotency_key)).toEqual(['mine']);
    expect(rows[0]).toMatchObject({ kind: 'route-create', status: 'queued', body: BODY, last_status: null });
    expect('headers' in rows[0]).toBe(false);
  });

  it('announces every change to the set of rows', async () => {
    const seen = vi.fn();
    document.addEventListener('pwa:mutation-queue-changed', seen);

    await enqueueRoute('k');
    await window.pwaMutationQueue.discard('k');

    expect(seen).toHaveBeenCalledTimes(2);
    document.removeEventListener('pwa:mutation-queue-changed', seen);
  });
});

describe('a route replayed by the page', () => {
  it('acknowledges the key with the route uuid before deleting the row', async () => {
    answer(200, { uuid: 'route-uuid', bounds: [7, 46, 7, 46.001] });
    await enqueueRoute('k-ack');
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(await window.pwaDb.getAll('queue:mutations')).toEqual([]);
    const ack = await window.pwaMutationQueue.acknowledged('k-ack');
    expect(ack).toMatchObject({ kind: 'route-create', uuid: 'route-uuid' });
  });

  it('sends the caller key as the Idempotency-Key', async () => {
    const calls = answer(200, { uuid: 'u' });
    await enqueueRoute('k-header');
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(calls[0].headers['Idempotency-Key']).toBe('k-header');
  });

  it('tells the map and the panel the routes changed, flagged as an upload', async () => {
    answer(200, { uuid: 'u' });
    await enqueueRoute('k-event');
    const seen = [];
    const listener = (event) => seen.push(event.detail);
    document.addEventListener('snowdesk:routes-changed', listener);
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(seen).toEqual([{ uploaded: true }]);
    document.removeEventListener('snowdesk:routes-changed', listener);
  });

  it('acknowledges nothing for a row with no kind', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"uuid": "u"}', { status: 200 })));
    const key = await window.pwaMutationQueue.enqueue({ method: 'POST', url: '/favourites/' });
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(await window.pwaMutationQueue.acknowledged(key)).toBe(null);
  });
});

describe('a refusal is kept on the row', () => {
  it.each([
    [400, { error: 'That line is not a route.' }],
    [409, { error: 'route_limit_reached' }],
    [403, { error: 'authentication_required' }],
    [404, undefined],
  ])('a %i keeps its status and error, and the row waits', async (status, body) => {
    answer(status, body);
    await enqueueRoute('k-fail');
    setOnline(true);

    await window.pwaMutationQueue.drain();

    const [row] = await window.pwaMutationQueue.list({ kind: 'route-create' });
    expect(row.status).toBe('failed');
    expect(row.last_status).toBe(status);
    expect(row.last_error).toBe(body ? body.error : null);
  });

  it('names the row in the permanent-failure event', async () => {
    answer(409, { error: 'route_limit_reached' });
    await enqueueRoute('k-evt');
    const seen = [];
    const listener = (event) => seen.push(event.detail);
    document.addEventListener('pwa:mutation-failed-permanent', listener);
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(seen[0]).toMatchObject({
      kind: 'route-create',
      idempotency_key: 'k-evt',
      status: 409,
      error: 'route_limit_reached',
    });
    document.removeEventListener('pwa:mutation-failed-permanent', listener);
  });

  it('discard() deletes the failed row and clears the badge error', async () => {
    answer(400, { error: 'bad' });
    await enqueueRoute('k-discard');
    setOnline(true);
    await window.pwaMutationQueue.drain();

    expect(await window.pwaMutationQueue.discard('k-discard')).toBe(true);

    expect(await window.pwaDb.getAll('queue:mutations')).toEqual([]);
    expect(document.querySelector('[data-sync-badge]').classList.contains('hidden')).toBe(true);
  });

  it("discard() leaves another principal's row alone", async () => {
    await window.pwaDb.put('queue:mutations', {
      idempotency_key: 'theirs',
      kind: 'route-create',
      principal: 'someone-else',
      status: 'failed',
    });

    expect(await window.pwaMutationQueue.discard('theirs')).toBe(false);
    expect(await window.pwaDb.getAll('queue:mutations')).toHaveLength(1);
  });
});

describe('principal()', () => {
  it('reads the anonymous principal as null', () => {
    expect(window.pwaMutationQueue.principal()).toBe(null);
  });
});

describe("the idempotency middleware's in-flight 409", () => {
  it('reschedules the row: the original post is still landing', async () => {
    answer(409, { error: 'idempotency_in_flight' });
    await enqueueRoute('k-inflight');
    setOnline(true);

    await window.pwaMutationQueue.drain();

    const [row] = await window.pwaMutationQueue.list({ kind: 'route-create' });
    expect(row.status).toBe('retry-scheduled');
    expect(document.getElementById('mutation-queue-toast').classList.contains('hidden')).toBe(true);
  });

  it('fails the reused-key 409 for good', async () => {
    answer(409, { error: 'idempotency_key_reused' });
    await enqueueRoute('k-reused');
    setOnline(true);

    await window.pwaMutationQueue.drain();

    const [row] = await window.pwaMutationQueue.list({ kind: 'route-create' });
    expect(row).toMatchObject({ status: 'failed', last_error: 'idempotency_key_reused' });
  });
});

describe('a race with the worker drain', () => {
  it.each([
    ['a refusal', 400],
    ['a retry', 503],
  ])('never re-creates a row the worker landed while the page waited (%s)', async (_label, status) => {
    await enqueueRoute('k-race');
    const [stored] = await window.pwaDb.getAll('queue:mutations');
    const failed = vi.fn();
    document.addEventListener('pwa:mutation-failed-permanent', failed);
    // The worker wins: it acknowledges and deletes the row while the page
    // is still waiting on its own answer.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url) => {
        if (url !== CREATE_URL) return new Response(null, { status: 204 });
        await window.pwaDb.put('meta:app', {
          key: 'mutations.ack:k-race',
          value: { kind: 'route-create', uuid: 'u', at: Date.now() },
        });
        await window.pwaDb.delete('queue:mutations', stored.id);
        return new Response('{"error": "x"}', { status });
      }),
    );
    setOnline(true);

    await window.pwaMutationQueue.drain();

    expect(await window.pwaDb.getAll('queue:mutations')).toEqual([]);
    expect(failed).not.toHaveBeenCalled();
    document.removeEventListener('pwa:mutation-failed-permanent', failed);
  });
});
