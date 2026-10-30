/*
 * tests/js/test_mutation_queue_route_discard_notice.js — the notice the
 * service worker leaves when its drain guard discards a route drawn
 * offline (SNOW-1262).
 *
 * The worker's guard runs with no tab open, so it cannot tell anyone; it
 * writes ``mutations.discard-notice`` to ``meta:app`` instead
 * (test_sw.js covers the write). This is the other half: the next page
 * load shows the same toast the page's own reconcile would, and deletes
 * the notice so it is shown once. It also prunes acknowledgements older
 * than a week while it is there.
 */

import { describe, expect, it } from 'vitest';

const DISCARDED = 'A route drawn offline under another account was discarded.';

document.body.innerHTML = `
  <div id="mutation-queue-toast" class="hidden -translate-y-full opacity-0">
    <span data-toast-body>Some changes couldn't be saved and won't be retried.</span>
    <button type="button" data-action="dismiss">×</button>
  </div>
  <template id="mutation-queue-strings-template">
    <span data-string="route-discarded">${DISCARDED}</span>
  </template>`;

await import('../../static/js/i18n_strings.js');
await import('../../static/js/db.js');
const NOW = Date.now();
await window.pwaDb.put('meta:app', { key: 'mutations.principal', value: null });
await window.pwaDb.put('meta:app', {
  key: 'mutations.discard-notice',
  value: { kinds: ['route-create'], at: NOW },
});
await window.pwaDb.put('meta:app', {
  key: 'mutations.ack:old',
  value: { kind: 'route-create', uuid: 'u', at: NOW - 8 * 24 * 60 * 60 * 1000 },
});
await window.pwaDb.put('meta:app', {
  key: 'mutations.ack:fresh',
  value: { kind: 'route-create', uuid: 'u', at: NOW },
});

await import('../../static/js/mutation_queue_core.js');
await import('../../static/js/telemetry.js');
await import('../../static/js/mutation_queue.js');

async function waitFor(predicate, { timeoutMs = 2000, intervalMs = 5 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('condition never became true');
}

describe("the worker's discard notice", () => {
  it('is shown on the next load, then deleted', async () => {
    const toast = document.getElementById('mutation-queue-toast');
    await waitFor(() => !toast.classList.contains('hidden'));
    expect(toast.querySelector('[data-toast-body]').textContent).toBe(DISCARDED);

    await waitFor(async () => !(await window.pwaDb.get('meta:app', 'mutations.discard-notice')));
  });

  it('prunes an acknowledgement past its week and keeps a fresh one', async () => {
    await waitFor(async () => !(await window.pwaDb.get('meta:app', 'mutations.ack:old')));

    expect(await window.pwaMutationQueue.acknowledged('fresh')).toMatchObject({ uuid: 'u' });
  });
});
