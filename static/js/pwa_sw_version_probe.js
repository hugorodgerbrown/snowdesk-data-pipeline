/*
 * static/js/pwa_sw_version_probe.js — Live SW-version probe for the
 * /_sw-version/ staff debug page (SNOW-517, SNOW-1027).
 *
 * Progressive enhancement only. templates/_debug/sw_version.html already
 * server-renders the deployed CACHE_VERSION and APP_VERSION into
 * [data-testid="sw-deployed-version"] / [data-testid="sw-app-version"] —
 * that baseline works with JS disabled. This script fills in the LIVE
 * values:
 *
 *   * [data-testid="sw-live-version"]: the worker controlling this page.
 *   * [data-testid="sw-workers"] (SNOW-1027): every worker the registration
 *     holds (active, waiting, installing) with the CACHE_VERSION each one
 *     reports. DevTools labels workers with Chrome's own counter (#66397,
 *     #66402), which says nothing about which build each is. After a
 *     deploy there are often two, and this is where a person reads which is
 *     which.
 *
 * Each worker is asked over the `shell-identity` MessageChannel message
 * sw.js answers (see its `message` listener), so replies cannot be confused
 * between workers. A worker that does not answer within the budget shows
 * as "no answer".
 *
 * The list re-renders on `updatefound`, on each new worker's `statechange`
 * and on `controllerchange`, so a waiting worker taking over is visible
 * without a reload. Its copy is read from the page's
 * ``#sw-worker-strings`` template rather than written here, so this file
 * carries no user-facing literals (bin/i18n-lint).
 */

'use strict';

(function () {
  const LIVE_VERSION_SELECTOR = '[data-testid="sw-live-version"]';
  const DEPLOYED_SELECTOR = '[data-testid="sw-deployed-version"]';
  const BODY_SELECTOR = '[data-sw-workers-body]';
  const ROW_TEMPLATE_ID = 'sw-worker-row-template';
  const STRINGS_TEMPLATE_ID = 'sw-worker-strings';
  const SHELL_TIMEOUT_MS = 2000;

  // The registration's three slots, in the order a worker moves through them.
  // Each key is also the key of its label in the strings template.
  const SLOTS = ['active', 'waiting', 'installing'];

  /**
   * Read one piece of copy from the page's strings template.
   *
   * @param {string} key
   * @returns {string} The string, or the key itself when the page has none.
   */
  function string(key) {
    const template = document.getElementById(STRINGS_TEMPLATE_ID);
    const el = template && template.content.querySelector(`[data-string="${key}"]`);
    return el ? el.textContent.trim() : key;
  }

  /**
   * Ask one worker which shell it holds.
   *
   * @param {ServiceWorker} worker
   * @returns {Promise<string>} The CACHE_VERSION, or '' on no reply.
   */
  function workerShell(worker) {
    return new Promise((resolve) => {
      if (typeof MessageChannel !== 'function') {
        resolve('');
        return;
      }
      const channel = new MessageChannel();
      let settled = false;
      const settle = (value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        try {
          channel.port1.close();
        } catch (_err) {
          // Non-fatal.
        }
        resolve(value);
      };
      const timer = setTimeout(() => settle(''), SHELL_TIMEOUT_MS);
      channel.port1.onmessage = (event) => {
        const data = event.data;
        settle(data && data.type === 'shell-identity' ? String(data.cache || '') : '');
      };
      try {
        worker.postMessage({ type: 'shell-identity' }, [channel.port2]);
      } catch (_err) {
        settle('');
      }
    });
  }

  /**
   * Build one table row from the page's row template.
   *
   * @param {{label: string, state: string, shell: string, note: string}} row
   * @returns {DocumentFragment | null}
   */
  function buildRow(row) {
    const template = document.getElementById(ROW_TEMPLATE_ID);
    if (!template) return null;
    const fragment = template.content.cloneNode(true);
    fragment.querySelector('[data-field="slot"]').textContent = row.label;
    fragment.querySelector('[data-field="state"]').textContent = row.state;
    fragment.querySelector('[data-field="shell"]').textContent = row.shell;
    fragment.querySelector('[data-field="note"]').textContent = row.note;
    return fragment;
  }

  /**
   * Describe how one worker relates to the page and the deploy.
   *
   * @param {ServiceWorker} worker
   * @param {string} shell
   * @param {string} deployed
   * @returns {string}
   */
  function noteFor(worker, shell, deployed) {
    const notes = [];
    if (worker === navigator.serviceWorker.controller) {
      notes.push(string('controls'));
    }
    if (shell && shell === deployed) {
      notes.push(string('deployed'));
    }
    return notes.length ? ' (' + notes.join(', ') + ')' : '';
  }

  /** Re-read the registration and redraw every live value. */
  async function render() {
    const body = document.querySelector(BODY_SELECTOR);
    const deployed = (document.querySelector(DEPLOYED_SELECTOR)?.textContent || '').trim();
    let registration = null;
    try {
      registration = await navigator.serviceWorker.getRegistration();
    } catch (_err) {
      // Leave the server-rendered placeholders.
    }

    const controller = navigator.serviceWorker.controller;
    const live = document.querySelector(LIVE_VERSION_SELECTOR);
    if (live && controller) live.textContent = (await workerShell(controller)) || '—';

    if (!body || !registration) return;
    const present = SLOTS.filter((slot) => registration[slot]);
    const rows = await Promise.all(
      present.map(async (slot) => {
        const worker = registration[slot];
        const shell = await workerShell(worker);
        return {
          label: string(slot),
          state: worker.state,
          shell: shell || string('no-answer'),
          note: noteFor(worker, shell, deployed),
        };
      }),
    );
    if (!rows.length) return;
    body.replaceChildren();
    rows.forEach((row) => {
      const fragment = buildRow(row);
      if (fragment) body.appendChild(fragment);
    });
  }

  if (!navigator.serviceWorker) return;

  navigator.serviceWorker.addEventListener('controllerchange', render);
  navigator.serviceWorker
    .getRegistration()
    .then((registration) => {
      if (!registration) return;
      registration.addEventListener('updatefound', () => {
        registration.installing?.addEventListener('statechange', render);
        render();
      });
      [registration.installing, registration.waiting].forEach((worker) =>
        worker?.addEventListener('statechange', render),
      );
    })
    .catch(() => {});
  render();

  // Exposed for the Vitest harness, which drives render() directly.
  window.pwaSwVersionProbe = Object.freeze({ render: render });
})();
