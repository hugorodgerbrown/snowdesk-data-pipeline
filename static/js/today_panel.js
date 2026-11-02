/*
 * static/js/today_panel.js — the Today panel, behind the sun roundel in the
 * map's date row (SNOW-1366).
 *
 * Today's bulletin danger for the regions the reader has pinned, with their
 * pinned resorts nested under them and each resort's weather — always
 * today, whatever day the map shows. Hugo, 2 Nov 2026: today's readings
 * were being laid over other surfaces (a "Today" block on the Favourites
 * panel, danger tiles in search); make Today a surface of its own.
 *
 *   - THE ROUNDEL TOGGLES. #map-today-panel-toggle opens the panel and a
 *     second tap closes it, as every roundel does. The panel docks in the
 *     left column under search on a desktop, beside its trigger
 *     (_overlay_sheet.html side="left"), and full width on a phone.
 *   - THE LIST LOADS ON EVERY OPEN. favourites:today answers the rows, over
 *     HTMX, into [data-today-rows]; the body is re-cloned from
 *     #today-sheet-template each time, so a closed panel keeps nothing.
 *     A failed load says so, never "you have pinned nothing".
 *   - A ROW OPENS ITS PLACE. Rows are includes/_place_link_row.html links
 *     carrying data-panel-open; static/js/map_panel_trail.js opens the
 *     region or resort sheet and gives it a back pill naming Today, which
 *     comes back through window.pwaTodayPanel.open(). This panel closes
 *     itself out of the way when a row is followed: one panel at a time.
 *   - A ROW OPENS ITS PLACE ON TODAY. The list is today's, so a row followed
 *     while the map is on another day first moves the map to today, through
 *     ``snowdesk:scrub-to`` as map_today.js's "Today" does. Otherwise the
 *     sheet a row opens would show the map's day: a level of 3 on the row
 *     and "No bulletin on this date" in the sheet it opened. The commit is
 *     synchronous, so it lands before map_panel_trail.js opens the sheet.
 *   - THE DOT IS THE BELL'S. The sun took the place of SNOW-1333's bell,
 *     which listed the account's alerts of the past week; Today shows the
 *     same places as they are now, so the list went (Hugo, 2 Nov 2026).
 *     For an account with alerts on, the roundel carries `data-alerts-url`
 *     (/account/alerts/recent/), and its red dot shows while the newest
 *     alert was sent after this device last opened Today — per device, in
 *     localStorage, under the bell's own key, so a reader who had seen an
 *     alert in the bell does not see it come back here. Opening Today
 *     clears it. Offline or refused, the dot stays hidden.
 *
 * Rendered only for a signed-in reader (public/partials/_today_surface.html),
 * so this module returns at once for a visitor.
 */
(function todayPanelInit() {
  'use strict';

  const toggle = document.getElementById('map-today-panel-toggle');
  const sheet = document.getElementById('today-sheet');
  const template = document.getElementById('today-sheet-template');
  if (!toggle || !sheet || !(template instanceof HTMLTemplateElement)) return;
  if (!window.MapSheet) return;

  var FALLBACKS = {
    error: 'Today could not be loaded. Check your connection and try again.',
  };
  const STRINGS = window.pwaStrings
    ? window.pwaStrings.read('today-strings-template', FALLBACKS)
    : FALLBACKS;

  const dot = toggle.querySelector('[data-today-dot]');
  const alertsUrl = toggle.dataset.alertsUrl || '';
  // The bell's key (SNOW-1333), kept so what was seen there stays seen.
  const SEEN_KEY = 'snowdesk:alerts-seen';
  // When the newest alert was sent, as an ISO string; "" for none.
  let newest = '';

  /** @returns {string} When this device last opened Today, or "". */
  function readSeen() {
    try {
      return window.localStorage.getItem(SEEN_KEY) || '';
    } catch {
      return '';
    }
  }

  /** @param {string} value */
  function writeSeen(value) {
    try {
      window.localStorage.setItem(SEEN_KEY, value);
    } catch {
      // Storage refused (private window): the dot simply comes back.
    }
  }

  /** Show the dot while an alert is newer than the last opening. */
  function paintDot() {
    if (dot) dot.hidden = !newest || newest <= readSeen();
  }

  /** Read the newest alert's time; leave the dot hidden on any failure. */
  async function loadAlerts() {
    if (!alertsUrl) return;
    try {
      const res = await fetch(alertsUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) return;
      const data = await res.json();
      const alerts = Array.isArray(data && data.alerts) ? data.alerts : [];
      newest = alerts.length ? alerts[0].sent_at || '' : '';
      paintDot();
    } catch {
      // Offline or refused: no dot.
    }
  }

  const controller = window.MapSheet.attach(sheet, {
    triggerSelector: '#map-today-panel-toggle',
    onTeardown: function () {
      sheet.replaceChildren();
      toggle.setAttribute('aria-expanded', 'false');
    },
  });

  /** The rows container of the body now in the sheet, if any.
   * @returns {HTMLElement|null}
   */
  function rowsEl() {
    return sheet.querySelector('[data-today-rows]');
  }

  /** Fill the sheet from its template and load today's rows.
   * @returns {void}
   */
  function render() {
    sheet.replaceChildren(template.content.cloneNode(true));
    const rows = rowsEl();
    const url = rows ? rows.dataset.todayUrl || '' : '';
    if (!rows || !url || typeof htmx === 'undefined') return;
    htmx.ajax('GET', url, { target: rows, swap: 'innerHTML' });
  }

  /** Open the panel on today's list.
   * @returns {void}
   */
  function open() {
    controller.open();
    toggle.setAttribute('aria-expanded', 'true');
    if (newest) writeSeen(newest);
    paintDot();
    render();
  }

  /** Close the panel.
   * @returns {void}
   */
  function close() {
    controller.close();
  }

  toggle.addEventListener('click', function () {
    if (controller.isOpen()) {
      close();
      return;
    }
    open();
  });

  // Today as the page knows it (#season-scrubber[data-today]), and the
  // map's day, from ``?d=`` until the scrubber's first commit says otherwise.
  const scrubber = document.getElementById('season-scrubber');
  const today = (scrubber && scrubber.dataset.today) || '';
  let mapDate = today;
  try {
    mapDate = new URLSearchParams(location.search).get('d') || mapDate;
  } catch {
    // Keep today.
  }
  document.addEventListener('snowdesk:date-changed', function (event) {
    const detail = /** @type {CustomEvent} */ (event).detail;
    mapDate = (detail && detail.date) || mapDate;
  });

  // Capture phase, so the map is on today before the row's sheet opens.
  sheet.addEventListener(
    'click',
    function (event) {
      const target = /** @type {HTMLElement} */ (event.target);
      if (!target || !target.closest || !target.closest('a[data-panel-open]')) return;
      if (!today || mapDate === today) return;
      document.dispatchEvent(
        new CustomEvent('snowdesk:scrub-to', {
          detail: { date: today, source: 'today-panel' },
        })
      );
    },
    true
  );

  // A followed row hands over to the place it names; map_panel_trail.js has
  // already read the row (in the capture phase) and opens the place in the
  // bubble phase, so stepping out of the way here loses nothing.
  sheet.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('a[data-panel-open]')) close();
  });

  // No reply (offline) or a non-2xx reply: say the list failed rather than
  // leave the loading line up, or fall through to the empty state, which
  // would tell the reader they have pinned nothing.
  for (const name of ['htmx:responseError', 'htmx:sendError']) {
    document.addEventListener(name, function (event) {
      const rows = rowsEl();
      const detail = /** @type {CustomEvent} */ (event).detail;
      if (!rows || !detail || detail.target !== rows) return;
      const p = document.createElement('p');
      p.className = 'text-sm text-text-2';
      p.textContent = STRINGS.error;
      rows.replaceChildren(p);
    });
  }

  loadAlerts();

  window.pwaTodayPanel = Object.freeze({
    open: open,
    close: close,
    isOpen: controller.isOpen,
  });
}());
