/*
 * tests/js/test_telemetry_master_switch.js — Vitest unit test for
 * static/js/telemetry.js's operator-level kill switch (SNOW-385, SNOW-496).
 *
 * Ports tests/e2e/test_pwa_telemetry.py's
 * test_master_switch_off_makes_emit_a_noop. Split out of test_telemetry.js
 * because `_enabled()` memoises `_enabledCache` from the
 * `pwa-telemetry-enabled` meta tag on its first call for the life of the
 * module — this file renders that meta tag as `content="0"` (mirroring
 * `settings.PWA_TELEMETRY_ENABLED=False`) BEFORE the one-time dynamic
 * import, so the whole pipeline is silenced from the first call.
 */

import { describe, expect, it } from 'vitest';

const meta = document.createElement('meta');
meta.setAttribute('name', 'pwa-telemetry-enabled');
meta.setAttribute('content', '0');
document.head.appendChild(meta);

await import('../../static/js/db.js');
await import('../../static/js/telemetry.js');

describe('master switch off', () => {
  it('makes both standard and critical emit() a no-op', async () => {
    const beacons = [];
    navigator.sendBeacon = (url) => {
      beacons.push(url);
      return true;
    };

    // Opt IN so opt-out can't be the reason nothing enqueues.
    await window.pwaTelemetry.setOptIn(true);
    await window.pwaTelemetry.emit('pwa.install.prompted');
    await window.pwaTelemetry.emit('pwa.kill_switch.activated');

    const depth = await window.pwaDb.count('queue:events');
    expect(depth).toBe(0);
    expect(beacons.length).toBe(0);
  });
});
