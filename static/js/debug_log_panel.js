/*
 * static/js/debug_log_panel.js — the on-page reader for window.pwaDebugLog
 * (SNOW-812).
 *
 * Paints ``#debug-log-list`` (templates/includes/_debug_log_panel.html)
 * from the recorder in static/js/debug_log.js, and owns the four controls
 * beside it: Record, the source filter, Copy and Clear.
 *
 * Loaded only where that partial is rendered — behind the ``debug_log``
 * waffle flag, i.e. for GRP_DEBUG — so it self-guards on the markup being
 * present and otherwise returns at once.
 *
 * Why a panel and not the console: the whole point is a phone with no
 * devtools attached, usually offline, often mid-download. Copy is
 * therefore not a convenience — it is the only way a trace gets off the
 * device that produced it.
 *
 * Repaint policy
 * --------------
 * The recorder notifies on every line, and a burst emits hundreds. Each
 * notification schedules a repaint on the next animation frame rather
 * than painting inline, so a burst costs one render. Nothing repaints at
 * all while the panel is collapsed except the pill's count — the reader
 * is not looking, and the point of this module is to observe the page's
 * behaviour without changing it.
 */

(function () {
  'use strict';

  const root = document.getElementById('debug-log');
  if (!root || !window.pwaDebugLog) return;

  const handle = document.getElementById('debug-log-handle');
  const body = document.getElementById('debug-log-body');
  const list = document.getElementById('debug-log-list');
  const countEl = document.getElementById('debug-log-count');
  const statusEl = document.getElementById('debug-log-status');
  const enabledBox = document.getElementById('debug-log-enabled');
  const filterEl = document.getElementById('debug-log-filter');

  // SNOW-620: server-translated copy, English literals as the fallback.
  const STRINGS = self.pwaStrings.read('debug-log-strings-template', {
    off: 'not recording',
    empty: 'Nothing recorded yet.',
    copied: 'copied',
    'copy-failed': 'copy failed',
  });

  // Whether the panel is expanded. Per-device and remembered, so a reload
  // mid-investigation — which is half of what an investigation here
  // consists of — comes back with the panel where it was.
  const OPEN_KEY = 'pwa.debugLog.open';

  let rafToken = null;

  /**
   * Colour a row by outcome, not by source. Reading a trace is scanning
   * for the line that went wrong, and 'miss'/'error'/'found: false' is
   * what that looks like across every producer here.
   *
   * @param {object} row
   * @returns {string} A design-token text class.
   */
  function toneClass(row) {
    const d = row.detail || {};
    const bad =
      d.error === true ||
      d.found === false ||
      d.usable === false ||
      d.result === 'miss' ||
      d.result === 'unclassified' ||
      (typeof d.failed === 'number' && d.failed > 0);
    if (bad) return 'text-status-error-text';
    const good =
      d.result === 'hit' || d.result === 'hit-after-reenumerate' || d.found === true;
    return good ? 'text-status-success-text' : 'text-text-2';
  }

  /**
   * Render one row as a list item: timestamp, source, event, then the
   * detail as `k=v` pairs. Built with textContent throughout — the detail
   * values include URLs and region ids that arrive from a server payload,
   * and this panel is not a reason to hand-roll an escaping bug.
   *
   * @param {object} row
   * @returns {HTMLElement}
   */
  function renderRow(row) {
    const li = document.createElement('li');
    li.className = `whitespace-pre border-b border-border px-2 py-0.5 ${toneClass(row)}`;
    const d = new Date(row.at);
    const pad = (n, w) => String(n).padStart(w || 2, '0');
    const ts = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(
      d.getMilliseconds(),
      3,
    )}`;
    const detail = row.detail
      ? Object.entries(row.detail)
          .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
          .join(' ')
      : '';
    li.textContent = `${ts} ${String(row.src).padEnd(6)} ${String(row.evt).padEnd(20)} ${detail}`;
    return li;
  }

  /**
   * The rows to show: everything, or one source. `entries()` is already
   * newest-first, which is the order a reader wants on a phone — the line
   * describing what just happened is at the top without scrolling.
   *
   * @returns {Array<object>}
   */
  function visibleRows() {
    const rows = window.pwaDebugLog.entries();
    const want = filterEl ? filterEl.value : '';
    return want ? rows.filter((row) => row.src === want) : rows;
  }

  /**
   * Repaint the list and the pill's count.
   */
  function paint() {
    rafToken = null;
    const rows = visibleRows();
    if (countEl) countEl.textContent = String(rows.length);
    if (statusEl) {
      statusEl.textContent = window.pwaDebugLog.isEnabled() ? '' : STRINGS.off;
    }
    if (body.hidden) return;
    list.textContent = '';
    if (rows.length === 0) {
      const li = document.createElement('li');
      li.className = 'px-2 py-1 text-text-3';
      li.textContent = STRINGS.empty;
      list.appendChild(li);
      return;
    }
    const frag = document.createDocumentFragment();
    for (const row of rows) frag.appendChild(renderRow(row));
    list.appendChild(frag);
  }

  /**
   * Coalesce repaints onto the next frame — see the module header.
   */
  function schedulePaint() {
    if (rafToken !== null) return;
    rafToken = requestAnimationFrame(paint);
  }

  /**
   * Expand or collapse the panel, remembering the choice.
   *
   * Opening loads the persisted trace from IndexedDB first: the lines
   * that explain a bug are usually the ones from before the reload that
   * made you go looking for the panel.
   *
   * @param {boolean} open
   */
  function setOpen(open) {
    body.hidden = !open;
    handle.setAttribute('aria-expanded', open ? 'true' : 'false');
    try {
      localStorage.setItem(OPEN_KEY, open ? '1' : '0');
    } catch (_err) {
      // Storage unavailable — the panel just won't be remembered.
    }
    if (open) schedulePaint();
  }

  handle.addEventListener('click', () => setOpen(body.hidden));
  document.getElementById('debug-log-close')?.addEventListener('click', () => setOpen(false));
  filterEl?.addEventListener('change', schedulePaint);

  enabledBox?.addEventListener('change', () => {
    window.pwaDebugLog.setEnabled(enabledBox.checked);
    schedulePaint();
  });

  document.getElementById('debug-log-clear')?.addEventListener('click', () => {
    window.pwaDebugLog.clear();
    schedulePaint();
  });

  document.getElementById('debug-log-copy')?.addEventListener('click', () => {
    const text = window.pwaDebugLog.format(visibleRows());
    // navigator.clipboard is unavailable on an insecure origin and can
    // reject on a permission refusal. Both are worth saying out loud
    // rather than leaving the button looking like it worked.
    const done = (msg) => {
      if (statusEl) statusEl.textContent = msg;
      setTimeout(schedulePaint, 2000);
    };
    try {
      navigator.clipboard
        .writeText(text)
        .then(() => done(STRINGS.copied))
        .catch(() => done(STRINGS['copy-failed']));
    } catch (_err) {
      done(STRINGS['copy-failed']);
    }
  });

  window.pwaDebugLog.subscribe(schedulePaint);

  // Restore the toggle's own state, then the panel's.
  if (enabledBox) enabledBox.checked = window.pwaDebugLog.isEnabled();
  let open = false;
  try {
    open = localStorage.getItem(OPEN_KEY) === '1';
  } catch (_err) {
    // Storage unavailable — start collapsed.
  }
  setOpen(open);

  // Fold the persisted trace in behind whatever is already in memory, so
  // a reload does not start the reader from a blank panel. Rows recorded
  // this page load are already in the recorder's ring; these are the
  // older ones, and they only need painting, not re-recording.
  window.pwaDebugLog.history().then((rows) => {
    if (rows.length === 0) return;
    // Nothing to merge into — the recorder owns its ring and must not be
    // fed its own history back (that would double every line and break
    // the trim). Persisted rows are shown only when the live ring is
    // empty, which is exactly the just-reloaded case this is for.
    if (window.pwaDebugLog.entries().length > 0) return;
    list.textContent = '';
    const frag = document.createDocumentFragment();
    for (const row of rows) frag.appendChild(renderRow(row));
    if (!body.hidden) list.appendChild(frag);
    if (countEl) countEl.textContent = String(rows.length);
  });
})();
