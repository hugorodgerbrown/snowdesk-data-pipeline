/*
 * static/js/map_alerts.js — the account's bulletin alerts, behind the bell in
 * the map's date row (SNOW-1333).
 *
 * The bell used to hold notes about the day on the map (SNOW-1206). Hugo:
 * the bell with the red dot should be a list of the alerts, not anything
 * else. So #map-alerts lists the messages the account's pushes and emails
 * carried over the past week, newest first, each linking where its push
 * did:
 *
 *   - RENDERED ONLY FOR AN ACCOUNT WITH ALERTS ON. The server leaves the
 *     bell out of the page otherwise, so a visitor makes no request.
 *   - SHOWN ONLY WHEN THERE IS ONE. An empty week, or a failed read, leaves
 *     the bell hidden — nothing on the row stands for an empty list.
 *   - A DOT FOR WHAT IS NEW. The dot shows while the newest alert was sent
 *     after this device last opened the list, and clears when it opens.
 *     Per device, in localStorage: the simplest record that holds.
 *   - THE CARD OPENS BESIDE IT. #map-alerts-card, above the bell on a
 *     desktop, under it on a phone (map_calendar.js moves the bell into
 *     the top row with the date control). It registers with
 *     window.pwaMapOverlays and spares the object sheets, as the calendar
 *     does.
 *
 * Reads ``data-alerts-url`` (/account/alerts/recent/), which answers
 * ``{ alerts: [{ title, body, url, sent_at, sent }] }``. The worker leaves
 * it network-only, so offline the bell stays as the page drew it: hidden.
 */
(function mapAlertsInit() {
  'use strict';

  const root = document.getElementById('map-alerts');
  const toggle = document.getElementById('map-alerts-toggle');
  const card = document.getElementById('map-alerts-card');
  if (!root || !toggle || !card) return;

  const dot = toggle.querySelector('[data-alerts-dot]');
  const list = card.querySelector('[data-alerts-list]');
  const url = root.dataset.alertsUrl || '';

  const OVERLAY_NAME = 'alerts';
  const SEEN_KEY = 'snowdesk:alerts-seen';
  // The selection column's own query (map_overlay_bounds.js): from here a
  // region, place or weather sheet docks down the map's left side.
  const desktop = typeof window.matchMedia === 'function' ? window.matchMedia('(min-width: 640px)') : null;

  // When the newest alert in the list was sent, as an ISO string.
  let newest = '';

  /** @returns {string} When this device last opened the list, or "". */
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

  /** @returns {boolean} */
  function isOpen() {
    return !card.hidden;
  }

  /**
   * One alert as a list item: a link holding its title, body and when it
   * was sent.
   *
   * @param {{ title: string, body: string, url: string, sent: string }} alert
   * @returns {HTMLLIElement}
   */
  function buildItem(alert) {
    const item = document.createElement('li');
    item.className = 'map-alerts-item';
    const link = document.createElement('a');
    link.className = 'map-alerts-link';
    link.href = alert.url || '/map/';
    const title = document.createElement('p');
    title.className = 'map-alerts-item-title';
    title.textContent = alert.title;
    link.append(title);
    if (alert.body) {
      const body = document.createElement('p');
      body.className = 'map-alerts-item-detail';
      body.textContent = alert.body;
      link.append(body);
    }
    if (alert.sent) {
      const sent = document.createElement('p');
      sent.className = 'map-alerts-item-sent';
      sent.textContent = alert.sent;
      link.append(sent);
    }
    item.append(link);
    return item;
  }

  /**
   * Draw the bell, its dot and the list.
   *
   * @param {Array<{ title: string, body: string, url: string, sent_at: string, sent: string }>} alerts
   */
  function render(alerts) {
    root.hidden = alerts.length === 0;
    if (!alerts.length) return;
    newest = alerts[0].sent_at || '';
    if (list) list.replaceChildren(...alerts.map(buildItem));
    if (dot) dot.hidden = !newest || newest <= readSeen();
  }

  /**
   * Keep the card clear of a left-hand sheet on a desktop. The bell sits in
   * the date row, which stays put while the selection column covers the
   * stack above it; the card opening up from the bell would land on the
   * sheet, so it starts where the column ends.
   */
  function place() {
    card.style.left = '';
    if (!desktop || !desktop.matches) return;
    const sheet = document.querySelector("[data-sheet-side='left']:not([hidden])");
    if (!sheet) return;
    const shift = sheet.getBoundingClientRect().right + 8 - root.getBoundingClientRect().left;
    if (shift > 0) card.style.left = `${Math.round(shift)}px`;
  }

  /** Open the card, clearing the dot. */
  function open() {
    window.pwaMapOverlays?.opening(OVERLAY_NAME, { spareObjectSheets: true });
    card.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    if (newest) writeSeen(newest);
    if (dot) dot.hidden = true;
    place();
  }

  /** Close the card. */
  function close() {
    if (card.hidden) return;
    card.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
  }

  /** Read the list from the server; leave the bell hidden on any failure. */
  async function load() {
    if (!url) return;
    try {
      const res = await fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!res.ok) return;
      const data = await res.json();
      render(Array.isArray(data && data.alerts) ? data.alerts : []);
    } catch {
      // Offline or refused: the bell stays as the page drew it.
    }
  }

  toggle.addEventListener('click', (e) => {
    // The other flyouts' outside-click dismiss must not read this as a
    // press away from them, and ours must not read it as one from us.
    e.stopPropagation();
    if (isOpen()) close();
    else open();
  });
  document.addEventListener('click', (e) => {
    if (isOpen() && e.target instanceof Node && !root.contains(e.target)) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    toggle.focus();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen: isOpen, close: close });

  load();
})();
