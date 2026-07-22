/*
 * tests/js/test_mutation_queue_toast_dismiss.js — Vitest unit test for
 * static/js/mutation_queue.js's failure-toast dismiss transition
 * (SNOW-376, SNOW-496).
 *
 * Split out of test_mutation_queue.js: the "×" dismiss button's click
 * listener is wired by mutation_queue.js's own `_wireToast()` at IIFE-load
 * time against whichever `#mutation-queue-toast` element is present in the
 * DOM at that moment — unlike `_revealToast()` (which re-looks the element
 * up by id on every call), the CLICK LISTENER itself is bound once, to that
 * one element instance. This file builds the toast fixture BEFORE importing
 * the module (dynamic `import()`, one time only) so the listener binds to
 * the actual element this test drives.
 */

import { describe, expect, it } from 'vitest';

const MUTATION_URL = '/api/test-mutation';

document.body.innerHTML = `
  <span data-sync-badge class="hidden items-center"></span>
  <div id="mutation-queue-toast" class="hidden -translate-y-full opacity-0">
    <span>Some changes couldn't be saved and won't be retried.</span>
    <button type="button" data-action="dismiss">×</button>
  </div>
`;

// Plain assignment rather than vi.stubGlobal — this file has exactly one
// test and no need for vi's automatic restore, and a module-top-level
// vi.stubGlobal call (before any test/hook runs) does not reliably take
// effect before the dynamic imports below execute.
globalThis.fetch = async (url) => {
  if (url === MUTATION_URL) return new Response('', { status: 422 });
  return new Response(null, { status: 204 });
};

await import('../../static/js/db.js');
await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/telemetry.js');
await import('../../static/js/mutation_queue.js');

// Let the one-time, fire-and-forget _reconcilePrincipal() kicked off by
// _wireLifecycle() at the import above settle before this file's own
// enqueue() call — on a brand-new DB with no baseline principal, a
// reconcile pass that resolves AFTER a row has already landed would treat
// it as "principal_uninitialised" and wipe it out from under this test.
await new Promise((resolve) => setTimeout(resolve, 50));

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

describe('mutation-queue failure toast dismiss', () => {
  it('plays the slide-up transition before hiding — not in the same synchronous tick', async () => {
    await window.pwaMutationQueue.enqueue({ method: 'POST', url: MUTATION_URL });

    await waitFor(() => {
      const el = document.getElementById('mutation-queue-toast');
      return !!(el && !el.classList.contains('hidden'));
    });

    document
      .getElementById('mutation-queue-toast')
      .querySelector('[data-action="dismiss"]')
      .click();

    // Immediately after the click: still un-hidden and mid-transition
    // (transform reversed) — regression guard for an earlier version that
    // added `hidden` in the same tick, collapsing the animation to nothing.
    const toastEl = document.getElementById('mutation-queue-toast');
    expect(toastEl.classList.contains('hidden')).toBe(false);
    expect(toastEl.classList.contains('-translate-y-full')).toBe(true);

    // jsdom never fires `transitionend`, so this exercises the fallback
    // timer (TOAST_TRANSITION_MS + 50ms) rather than the CSS transition path.
    await waitFor(
      () => document.getElementById('mutation-queue-toast').classList.contains('hidden'),
      { timeoutMs: 2000 },
    );
  });
});
