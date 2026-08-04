/*
 * tests/js/test_pwa_version_check_escalation_clean.js — the 24h
 * cold-launch escalation, disowned branch (SNOW-618).
 *
 * The counterpart to ``test_pwa_version_check_escalation.js``: a stamp
 * past the 24h horizon, but a server that says this shell IS current.
 *
 * This is the branch that matters most. The stamp is written by
 * ``showSoftBanner``, and before the header-drift fix a banner could be
 * raised by a response replayed from a pre-deploy cache — so a stamp can
 * be evidence of nothing at all. Escalating it would lock a fully
 * up-to-date user out of the app with an undismissable modal, on a
 * cache artefact.
 *
 * Its own file for the same reason as its sibling: the decision runs at
 * import time against ``localStorage``, and ``forcedUpdateTriggered``
 * latches, so the two branches cannot share one module instance.
 */

import { describe, expect, it, vi } from 'vitest';

const CURRENT_BUILD = '2026.08.01';
const FIRST_SHOWN_KEY = 'pwa.update.first_shown_at';
const ESCALATION_MS = 24 * 60 * 60 * 1000;

document.head.innerHTML = `<meta name="pwa-app-version" content="${CURRENT_BUILD}">`;
document.body.innerHTML = `
  <div id="sw-update-banner" class="hidden"></div>
  <div id="pwa-update-modal" class="hidden">
    <button id="pwa-update-modal-reload" type="button">Reload now</button>
  </div>
`;

localStorage.setItem(FIRST_SHOWN_KEY, String(Date.now() - ESCALATION_MS - 3600_000));

const emit = vi.fn();
window.pwaTelemetry = { emit };
window.pwaResetLocalData = vi.fn(async () => {});

// The server disowns the stamp: this shell is the current build.
window.fetch = vi.fn(async (url) => {
  if (String(url).startsWith('/api/version')) {
    return new Response(
      JSON.stringify({ current: CURRENT_BUILD, min_supported: '' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
  }
  return new Response('{}', { status: 200 });
});

await import('../../static/js/pwa_version_check.js');

await new Promise((resolve) => setTimeout(resolve, 0));

describe('24h cold-launch escalation — server disowns the stamp', () => {
  it('does not open the blocking modal', () => {
    // An up-to-date user, locked out of the app by an undismissable modal,
    // on the strength of a stamp a phantom banner left behind.
    expect(
      document.getElementById('pwa-update-modal').classList.contains('hidden'),
    ).toBe(true);
    expect(document.documentElement.style.overflow).not.toBe('hidden');
  });

  it('clears the stamp so it can never escalate again', () => {
    expect(localStorage.getItem(FIRST_SHOWN_KEY)).toBeNull();
  });

  it('emits no forced-update telemetry', () => {
    expect(
      emit.mock.calls.filter((c) => c[0] === 'pwa.forced_update.triggered'),
    ).toHaveLength(0);
  });

  it('does not wipe local data', () => {
    expect(window.pwaResetLocalData).not.toHaveBeenCalled();
  });
});
