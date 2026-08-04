/*
 * tests/js/test_pwa_version_check_escalation.js — the 24h cold-launch
 * escalation, confirmed branch (SNOW-618).
 *
 * Spec §3.9: the soft banner is sticky, but if it has been showing for
 * more than 24h without acceptance, the very next COLD LAUNCH shows the
 * blocking modal instead. That runs once at import time rather than on
 * every response, so a user who is actively browsing is not escalated
 * mid-session.
 *
 * Its own file because the decision is made against whatever
 * ``localStorage`` holds at import time — the stamp has to be planted
 * before the module loads, which a shared import cannot sequence. Same
 * one-file-per-pre-import-state convention as
 * ``test_mutation_queue_principal.js`` and its siblings.
 *
 * The clean branch — a stamp the server disowns — is
 * ``test_pwa_version_check_escalation_clean.js``. The two cannot share a
 * file: ``forcedUpdateTriggered`` latches once the modal opens.
 */

import { describe, expect, it, vi } from 'vitest';

const CURRENT_BUILD = '2026.08.01';
const NEWER_BUILD = '2026.08.02';
const FIRST_SHOWN_KEY = 'pwa.update.first_shown_at';
const ESCALATION_MS = 24 * 60 * 60 * 1000;

document.head.innerHTML = `<meta name="pwa-app-version" content="${CURRENT_BUILD}">`;
document.body.innerHTML = `
  <div id="sw-update-banner" class="hidden"></div>
  <div id="pwa-update-modal" class="hidden">
    <button id="pwa-update-modal-reload" type="button">Reload now</button>
  </div>
`;

// Planted before the import: a banner first shown 25 hours ago, i.e. past
// the escalation horizon.
localStorage.setItem(FIRST_SHOWN_KEY, String(Date.now() - ESCALATION_MS - 3600_000));

const versionCalls = [];
const emit = vi.fn();
window.pwaTelemetry = { emit };
window.pwaResetLocalData = vi.fn(async () => {});

// The server confirms the drift the stamp implies.
window.fetch = vi.fn(async (url) => {
  if (String(url).startsWith('/api/version')) {
    versionCalls.push(String(url));
    return new Response(
      JSON.stringify({ current: NEWER_BUILD, min_supported: '' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }
  return new Response('{}', { status: 200 });
});

await import('../../static/js/pwa_version_check.js');

// The escalation check is fire-and-forget at import time.
await new Promise((resolve) => setTimeout(resolve, 0));

describe('24h cold-launch escalation — server confirms the drift', () => {
  it('verifies the stamp against /api/version rather than trusting it', () => {
    // The stamp may have been planted by a stale-cache false positive, so
    // the server settles it before anything blocks. Trusting the stamp
    // alone is how a phantom banner became a phantom lockout.
    expect(versionCalls).toHaveLength(1);
  });

  it('opens the blocking modal', () => {
    expect(
      document.getElementById('pwa-update-modal').classList.contains('hidden'),
    ).toBe(false);
    expect(document.documentElement.style.overflow).toBe('hidden');
  });

  it('reports the escalation trigger, not the min-version one', () => {
    const forced = emit.mock.calls.filter(
      (c) => c[0] === 'pwa.forced_update.triggered',
    );
    expect(forced).toHaveLength(1);
    // The two paths into the modal are distinguishable in PostHog, which
    // is the only way to tell "we raised the floor" from "this user sat on
    // a banner for a day".
    expect(forced[0][1]).toEqual({ trigger: 'escalation' });
  });

  it('leaves the stamp in place', () => {
    // Nothing clears it on this branch: the drift is real, and the modal
    // is not dismissable, so the next launch escalating again is correct.
    expect(localStorage.getItem(FIRST_SHOWN_KEY)).not.toBeNull();
  });
});
