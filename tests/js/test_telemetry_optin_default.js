/*
 * tests/js/test_telemetry_optin_default.js — Vitest unit test for
 * static/js/telemetry.js's opt-in default computation (SNOW-385, SNOW-496).
 *
 * Ports tests/e2e/test_pwa_telemetry.py's
 * test_opt_in_default_persists_to_meta_app. Split out of test_telemetry.js
 * because `_optInCache` computes its default from `navigator.language`
 * only on the very first `_optIn()` call ever made against a given
 * telemetry.js instance — every test in test_telemetry.js explicitly calls
 * `setOptIn()` first (which always overwrites the cache), so this needs to
 * be the module's first-ever opt-in read, in its own file.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/db.js';
import '../../static/js/telemetry.js';

describe('isOptIn() default', () => {
  it('computes a default on first call and persists it to meta:app', async () => {
    await window.pwaTelemetry.isOptIn();
    const stored = await window.pwaDb.get('meta:app', 'telemetry.opt_in');

    expect(stored).toBeDefined();
    expect(stored.key).toBe('telemetry.opt_in');
    expect(typeof stored.value).toBe('boolean');
  });
});
