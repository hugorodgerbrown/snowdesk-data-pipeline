/*
 * tests/js/test_telemetry.js — Vitest unit tests for static/js/telemetry.js
 * (SNOW-385, SNOW-496).
 *
 * Ports tests/e2e/test_pwa_telemetry.py's 8 cases, minus two that need
 * their own file because telemetry.js memoises module-scope state on
 * first call for the life of the module (this file's one static import):
 *   - test_opt_in_default_persists_to_meta_app — `_optInCache` computes
 *     its default from `navigator.language` only on the FIRST `_optIn()`
 *     call ever made against this module; every test below explicitly
 *     calls `setOptIn()` first (matching the retired file's own pattern),
 *     which always overwrites the cache regardless of order — except this
 *     one case, which needs to BE that first call. See
 *     test_telemetry_optin_default.js.
 *   - test_master_switch_off_makes_emit_a_noop — `_enabledCache` reads
 *     the `pwa-telemetry-enabled` meta tag once, before any test in this
 *     file runs (there is no meta tag here, so it caches "enabled"). See
 *     test_telemetry_master_switch.js.
 *
 * Also folds in the telemetry-specific cases from
 * tests/e2e/test_pwa_client_signals.py that exercise telemetry.js's OWN
 * sample-rate/critical-event/enqueue logic directly, rather than another
 * module's wiring INTO telemetry.js (sw_register.js's message bridge,
 * pwa_install.js's funnel, pwa_version_check.js's escalation timer, and
 * pwa_client_version.js's header-stamping test other modules' behaviour,
 * not telemetry.js's — those stay in Playwright; see
 * test_pwa_client_signals.py's updated module docstring):
 *   - the freshness-indicator sample-rate gate (3 cases: fresh/stale/
 *     unsafe) — calls `emit()` directly with the same event
 *     name/properties `_freshness_indicator.html`'s inline script uses,
 *     rather than rendering the Django template (no Django here); the
 *     template-renders-the-right-call-with-the-right-args assertion isn't
 *     re-proven, only telemetry.js's own sampling/enqueue behaviour for
 *     those event names.
 *   - the storage-eviction heuristic (2 cases) — `db.js`'s own
 *     `_checkStorageEstimate()`, triggered by `window.pwaDb.open()`,
 *     calls `window.pwaTelemetry.emit('pwa.storage.evicted_probable', …)`;
 *     covered here (not in test_db.js) because the assertion is about the
 *     emitted envelope, which is telemetry.js's contract.
 *
 * `fetch` / `navigator.sendBeacon` are stubbed per-test (not in setup.js —
 * see its own comment) so each test can assert on what was actually sent.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/db.js';
import '../../static/js/telemetry.js';

const DB_NAME = 'snowdesk-pwa-v1';

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

function queueEvents() {
  return window.pwaDb.getAll('queue:events');
}

beforeEach(async () => {
  await deleteDb();
  navigator.sendBeacon = vi.fn(() => true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1. emit() writes to queue:events with the expected envelope shape.
// ---------------------------------------------------------------------------

describe('emit()', () => {
  it('enqueues the eight-field envelope when opted in', async () => {
    await window.pwaTelemetry.setOptIn(true);
    await window.pwaTelemetry.emit('pwa.install.prompted', { platform: 'web' });

    const rows = await queueEvents();
    const row = rows[0];
    expect(row.event).toBe('pwa.install.prompted');
    expect(row.properties).toEqual({ platform: 'web' });
    expect(typeof row.timestamp).toBe('string');
    expect(row.timestamp).not.toBe('');
    expect(typeof row.session_id).toBe('string');
    expect(row.session_id).not.toBe('');
    expect(row.install_state).toBe('browser');
    expect(Object.keys(row).sort()).toEqual(
      [
        'event',
        'timestamp',
        'client_version',
        'session_id',
        'user_id',
        'platform',
        'install_state',
        'sw_state',
        'connection',
        'properties',
        'id',
      ].sort(),
    );
  });
});

// ---------------------------------------------------------------------------
// 2. flush() POSTs the batch and clears the rows on 2xx.
// ---------------------------------------------------------------------------

describe('flush()', () => {
  it('sends the batch envelope and removes drained rows', async () => {
    const captured = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url, init) => {
        captured.push(JSON.parse(init.body));
        return new Response(null, { status: 204 });
      }),
    );

    await window.pwaTelemetry.setOptIn(true);
    await window.pwaTelemetry.emit('pwa.install.prompted');
    await window.pwaTelemetry.emit('pwa.install.completed');
    const before = await window.pwaDb.count('queue:events');
    await window.pwaTelemetry.flush();
    const after = await window.pwaDb.count('queue:events');

    expect(before).toBe(2);
    expect(after).toBe(0);
    expect(captured.length).toBe(1);
    expect(captured[0].events.length).toBe(2);
    const forwarded = captured[0].events.map((e) => e.event);
    expect(new Set(forwarded)).toEqual(new Set(['pwa.install.prompted', 'pwa.install.completed']));
  });
});

// ---------------------------------------------------------------------------
// 3. Critical event fires sendBeacon immediately with single-envelope shape.
// ---------------------------------------------------------------------------

describe('critical events', () => {
  it('fire navigator.sendBeacon immediately', async () => {
    await window.pwaTelemetry.setOptIn(true);
    await window.pwaTelemetry.emit('pwa.kill_switch.activated', { reason: 'test' });

    expect(navigator.sendBeacon).toHaveBeenCalledTimes(1);
    const [url] = navigator.sendBeacon.mock.calls[0];
    expect(url.endsWith('/api/telemetry')).toBe(true);

    const rows = await queueEvents();
    expect(rows[0].event).toBe('pwa.kill_switch.activated');
    expect(rows[0].properties).toEqual({ reason: 'test' });
  });
});

// ---------------------------------------------------------------------------
// 4. Opt-out — standard event dropped; critical event stripped of ids.
// ---------------------------------------------------------------------------

describe('opt-out', () => {
  it('drops a standard (non-critical) event entirely', async () => {
    await window.pwaTelemetry.setOptIn(false);
    await window.pwaTelemetry.emit('pwa.install.prompted');

    expect(await window.pwaDb.count('queue:events')).toBe(0);
  });

  it('still fires a critical event, with user_id/session_id stripped', async () => {
    await window.pwaTelemetry.setOptIn(false);
    await window.pwaTelemetry.emit('pwa.reset.user_initiated');

    const rows = await queueEvents();
    expect(rows.length).toBe(1);
    expect(rows[0].event).toBe('pwa.reset.user_initiated');
    expect(rows[0].user_id).toBeNull();
    expect(rows[0].session_id).toBeNull();
    expect(typeof rows[0].client_version).toBe('string');
    expect(['browser', 'standalone']).toContain(rows[0].install_state);
  });
});

// ---------------------------------------------------------------------------
// setOptIn()/isOptIn() write-through.
// ---------------------------------------------------------------------------

describe('setOptIn()', () => {
  it('persists to meta:app and isOptIn() reads it back', async () => {
    await window.pwaTelemetry.setOptIn(true);
    const stored = await window.pwaDb.get('meta:app', 'telemetry.opt_in');
    const read = await window.pwaTelemetry.isOptIn();

    expect(stored.value).toBe(true);
    expect(read).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Freshness-indicator sample rate (pwa.freshness.{fresh,stale,unsafe}).
// ---------------------------------------------------------------------------

describe('freshness events', () => {
  it.each(['fresh', 'stale', 'unsafe'])(
    'enqueues pwa.freshness.%s when the sample-rate gate is forced open',
    async (state) => {
      vi.spyOn(Math, 'random').mockReturnValue(0);
      await window.pwaTelemetry.setOptIn(true);

      await window.pwaTelemetry.emit(`pwa.freshness.${state}`, {
        generated_at: '2026-07-16T10:00:00Z',
      });

      const rows = await queueEvents();
      const row = rows.find((r) => r.event === `pwa.freshness.${state}`);
      expect(row).toBeDefined();
    },
  );
});

// ---------------------------------------------------------------------------
// pwa.storage.evicted_probable — db.js's cold-start heuristic.
// ---------------------------------------------------------------------------

describe('storage-eviction heuristic (db.js -> telemetry.js)', () => {
  it('flags an implausibly low storage quota', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    navigator.storage = { estimate: () => Promise.resolve({ quota: 1024, usage: 0 }) };
    await window.pwaTelemetry.setOptIn(true);

    await window.pwaDb.open();

    const row = await new Promise((resolve, reject) => {
      const deadline = Date.now() + 2000;
      (async function poll() {
        const rows = await queueEvents();
        const match = rows.find((r) => r.event === 'pwa.storage.evicted_probable');
        if (match) return resolve(match);
        if (Date.now() > deadline) return reject(new Error('evicted_probable never emitted'));
        setTimeout(poll, 5);
      })();
    });

    expect(row.properties.quota).toBe(1024);
    expect(row.properties.usage).toBe(0);
  });

  it('does not flag a plausible quota with no prior-install marker', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0);
    navigator.storage = {
      estimate: () => Promise.resolve({ quota: 1024 * 1024 * 1024, usage: 0 }),
    };
    await window.pwaTelemetry.setOptIn(true);

    await window.pwaDb.open();
    await new Promise((resolve) => setTimeout(resolve, 150));

    const rows = await queueEvents();
    expect(rows.find((r) => r.event === 'pwa.storage.evicted_probable')).toBeUndefined();
  });
});
