/*
 * static/js/sync_log.js — Manage-page sync-log panel (SNOW-482).
 *
 * Paints the ``#sync-log-list`` <ul>
 * (``accounts/templates/accounts/partials/_sync_log_body.html``) with the
 * newest rows from ``window.pwaDb.getSyncLog()`` on load. Only included
 * on the page when the ``sync_log`` waffle flag is active
 * (``accounts/templates/accounts/manage.html``), but self-guards on
 * ``window.pwaDb`` presence regardless — IndexedDB can be unavailable
 * (private mode, the SNOW-375 Reset Required state).
 *
 * Rows are plain ``{id, at, path}`` objects written by
 * ``static/js/pwa_offline.js``'s ``appendSyncLogEntry`` for every
 * qualifying same-origin, un-cached response. "Reset local data on this
 * device" (SNOW-378, ``pwa_reset.js``) wipes the whole IndexedDB
 * database, so the log clears along with everything else — no separate
 * clear affordance needed here.
 */

(function () {
  'use strict';

  const LIST_ID = 'sync-log-list';

  /**
   * Format an ISO timestamp as "HH:MM DD/MM" without pulling Intl.
   * Falls back to the raw string on parse failure.
   *
   * @param {string} value
   * @returns {string}
   */
  function formatShort(value) {
    const d = new Date(value);
    if (Number.isNaN(d.valueOf())) return value;
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())} ${pad(d.getDate())}/${pad(d.getMonth() + 1)}`;
  }

  /**
   * Replace the list's contents with a single muted placeholder row.
   *
   * @param {HTMLElement} list
   * @param {string} text
   */
  function renderPlaceholder(list, text) {
    list.textContent = '';
    const li = document.createElement('li');
    li.className = 'px-4 py-3 text-text-3';
    li.setAttribute('data-role', 'sync-log-placeholder');
    li.textContent = text;
    list.appendChild(li);
  }

  /**
   * Build one row for a ``log:sync`` entry.
   *
   * @param {{at: string, path: string}} entry
   * @returns {HTMLLIElement}
   */
  function buildRow(entry) {
    const li = document.createElement('li');
    li.className = 'flex items-center justify-between gap-3 px-4 py-3';
    li.setAttribute('data-testid', 'sync-log-row');

    const path = document.createElement('span');
    path.className = 'text-text-1 truncate';
    path.textContent = entry.path || '';

    const at = document.createElement('span');
    at.className = 'text-text-3 shrink-0 font-mono text-caption';
    at.textContent = formatShort(entry.at);

    li.appendChild(path);
    li.appendChild(at);
    return li;
  }

  /**
   * Read the sync log and paint it into ``#sync-log-list``, replacing
   * the "Loading…" placeholder from the template shell. Never throws —
   * an IndexedDB failure degrades to a muted placeholder row rather than
   * breaking the rest of the page.
   */
  async function render() {
    const list = document.getElementById(LIST_ID);
    if (!list) return;

    if (!window.pwaDb || typeof window.pwaDb.getSyncLog !== 'function') {
      renderPlaceholder(list, 'Sync log unavailable on this device.');
      return;
    }

    try {
      const rows = await window.pwaDb.getSyncLog();
      if (!rows || rows.length === 0) {
        renderPlaceholder(list, 'No syncs yet.');
        return;
      }
      list.textContent = '';
      rows.forEach((entry) => list.appendChild(buildRow(entry)));
    } catch (_err) {
      renderPlaceholder(list, 'Could not load the sync log.');
    }
  }

  // Deferred scripts execute in DOCUMENT ORDER, and this one's <script>
  // tag (inside the manage.html content block) appears BEFORE db.js's
  // (declared in base.html, after the content block) — so calling
  // render() eagerly here would race window.pwaDb into existence.
  // DOMContentLoaded fires only once every deferred script, db.js
  // included, has already run, which is what actually guarantees
  // window.pwaDb is defined by the time render() executes.
  document.addEventListener('DOMContentLoaded', render);
})();
