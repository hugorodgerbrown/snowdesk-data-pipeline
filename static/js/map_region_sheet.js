/*
 * static/js/map_region_sheet.js — the region sheet a selection opens
 * (SNOW-1098; on every width since SNOW-1173).
 *
 * Tapping a region selected it and showed nothing about it: the popup that
 * used to answer the tap covered the terrain the reader had just tapped, and
 * SNOW-895 removed it. On a phone the tap now opens #region-sheet at PEEK —
 * name, danger level, date, pin and download — which leaves that terrain in
 * view; dragging up reveals the problems and resorts (HALF) and the bulletin
 * link (FULL). The detents themselves are SNOW-1097's, bound by
 * `window.MapSheet.attach` because the partial declares them.
 *
 * WHEN IT OPENS. For every selection, on every width — a tap, a search
 * pick, a pinned region, a deep link (Hugo, 16 Oct for the phone; 21 Oct,
 * SNOW-1173, for the desktop). The region chip is not drawn on any width
 * (map.css hides the ribbon; on a desktop search, at the top-left, names
 * the selection instead), so the sheet is the surface that says what is
 * selected. At `sm` and up the same sheet is the inset panel the place and
 * weather sheets already are (no detents, see
 * docs/decisions/sheet-detents-are-phone-only.md); the chip's dropdown,
 * which carried a second copy of the region's summary, is gone. A desktop
 * region tap used to open
 * nothing while a resort or weather tap opened this panel, which read as
 * two different maps.
 *
 * THE RIBBON STAYS IN THE DOM, undrawn on every width since SNOW-1173,
 * because the proxies in the sheet's header press its pin and download
 * roundels, which keep owning the verbs. The root carries
 * `data-region-sheet` while the sheet is open.
 *
 * WHILE IT IS OPEN it follows the map: another region selected (by any
 * route) repaints it for that region at the detent it is resting at, a date
 * change repaints it for the new day, and deselecting the region closes it.
 *
 * THE PIN AND DOWNLOAD BUTTONS IN ITS HEADER ARE PROXIES for the two
 * roundels in the ribbon header (#map-region-pin-control,
 * #map-download-control). A press presses the roundel, and the proxy is
 * redrawn AS the roundel whenever it changes: its classes, its state
 * attributes (data-pin-state, data-download-state, the basemap key), its
 * inline style (the --download-progress fill), its ARIA and its glyphs. The
 * ribbon is hidden while the sheet is open, so the proxy is the only place
 * a filled star, a rising download fill or an error glyph can show. Each verb keeps one owner (map_region_panel.js and
 * map_region_download.js), so the sheet's star cannot disagree with the
 * header's.
 *
 * THE PEEK PAINTS AT ONCE, AND A BODY IS FETCHED ONCE (2026-10-19). Every
 * selection used to wait on a round trip before the peek said anything,
 * though its name, the day's level and the day are already on the page,
 * and every look at the same region fetched the same body again. Now:
 *
 *   - the peek row is cloned from `#region-sheet-peek-template` (the same
 *     partial the body renders, `_region_sheet_peek.html`) and filled from
 *     the selection event and `window.pwaRegionLevel`, so a tap shows the
 *     region at once, offline too; the body then replaces it without the
 *     row moving;
 *   - a fetched body is kept in memory by its URL (region and day) for
 *     BODY_TTL_MS, so going back to a region, or scrubbing back to a day,
 *     paints it with no request. The view sends a matching Cache-Control
 *     (apps/public/region_sheet.py), so the browser's own cache answers a
 *     reload: a past day's body for a day, today's for ten minutes, because
 *     bulletins are amended during the day.
 *
 * PAUSED DURING PLAYBACK (SNOW-1239). While the timeline plays the sheet
 * stays open under a "Playback in progress" band
 * (window.pwaSheetPlayback, sheet_playback_core.js) and fetches nothing:
 * a frame only records its day, and a selection made meanwhile paints what
 * the page already holds (a kept body, or the peek row). It loads once,
 * for the day playback stopped on, when it stops. A newer ask, and every
 * close, aborts the one in flight; a kept body is only ever an answer
 * that landed.
 */

(function regionSheetInit() {
  'use strict';

  const sheet = document.getElementById('region-sheet');
  const mapEl = document.getElementById('map');
  const playback = window.pwaSheetPlayback;
  if (!sheet || !mapEl || !window.MapSheet || !playback) return;

  const URL_TEMPLATE = mapEl.dataset.regionSheetUrl || '';
  if (!URL_TEMPLATE) return;

  /**
   * Whether a proxy copies this attribute from the roundel it stands for:
   * its ARIA and title, its inline style, and every data-* state attribute.
   *
   * @param {string} name
   * @returns {boolean}
   */
  const isMirrored = (name) =>
    name.startsWith('aria-') || name.startsWith('data-') || name === 'title' || name === 'style';

  const STRINGS = self.pwaStrings.read('region-sheet-strings-template', {
    loading: 'Loading region details…',
    failed: 'Region details are unavailable offline.',
  });

  // The digit a danger tile shows for each level, as the server's
  // `danger_level_digit` filter writes it.
  const LEVEL_DIGITS = { low: '1', moderate: '2', considerable: '3', high: '4', very_high: '5' };

  // How long a fetched body is reused without asking again. Matches the
  // view's max-age for today; a past day's body would keep for longer, but
  // the browser's cache already holds it for that.
  const BODY_TTL_MS = 10 * 60 * 1000;
  // Enough for a long session of browsing; the oldest goes first.
  const BODY_CACHE_SIZE = 50;
  /** @type {Map<string, {html: string, at: number}>} Bodies by URL. */
  const bodies = new Map();

  // SNOW-1295: WHEN THE READER'S OWN REPORTS CHANGE. A body carries the
  // region's "On the ground" section, so one kept from before a report was
  // filed (or removed) says "No reports" beside a pin that says otherwise.
  // A change drops every kept body and stamps the time into the URL as
  // `r=`, which the browser's HTTP cache (ten minutes, private) has never
  // seen. The stamp is kept in localStorage so a reload asks fresh too.
  const REPORTS_EPOCH_KEY = 'snowdesk.reports.epoch';
  let reportsEpoch = '';
  try {
    reportsEpoch = localStorage.getItem(REPORTS_EPOCH_KEY) || '';
  } catch (_) {
    // Storage blocked: the stamp holds for this page only.
  }

  // Invalidates a fetch when a newer one starts, so a slow answer for the
  // region the reader has already moved on from never paints over the new
  // one, and aborts it on a close by any route (onTeardown).
  const guard = playback.createGuard();
  // SNOW-1239: a load the sheet skipped while the timeline played, to make
  // once when it stops.
  let pendingRefresh = false;

  const controller = window.MapSheet.attach(sheet, {
    onTeardown: () => {
      guard.invalidate();
      pendingRefresh = false;
      playback.markPaused(sheet, false);
    },
  });

  // The sheet closes by several routes this module does not own (Escape,
  // the ×, a drag below peek, another overlay opening), so the root's mark
  // follows the sheet's own `hidden` rather than this module's calls.
  const markRoot = () => {
    const open = !sheet.hasAttribute('hidden');
    document.documentElement.toggleAttribute('data-region-sheet', open);
  };
  if (typeof MutationObserver === 'function') {
    new MutationObserver(markRoot).observe(sheet, {
      attributes: true,
      attributeFilter: ['hidden'],
    });
  }

  /** @type {?string} */
  let regionId = null;
  /** Whether the last load is waiting on the network for the body. */
  let awaiting = false;
  /** The selection's own name, for the peek painted from the page. */
  let regionName = '';
  /** @type {?string} */
  let dateKey = null;

  /**
   * Replace the sheet's body with one line of text.
   *
   * @param {string} text
   * @returns {void}
   */
  const paintLine = (text) => {
    const p = document.createElement('p');
    p.className = 'px-2 text-sm text-text-2';
    p.setAttribute('data-sheet-peek', '');
    p.textContent = text;
    sheet.replaceChildren(p);
  };

  /**
   * Today as ``YYYY-MM-DD``, on the device's calendar.
   *
   * @returns {string}
   */
  const todayKey = () => {
    const now = new Date();
    const pad = (/** @type {number} */ n) => String(n).padStart(2, '0');
    return now.getFullYear() + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate());
  };

  /**
   * A day as the date control and the server's date line write it,
   * "21 Oct 2026" (Django's ``j M Y``) — in that order whatever the
   * locale's own, since an "en" Intl puts the month first. Formatted in UTC
   * so the calendar day never shifts with the device's zone.
   *
   * @param {string} isoDay ``YYYY-MM-DD``.
   * @returns {string}
   */
  const formatDay = (isoDay) => {
    try {
      const parts = new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
      }).formatToParts(new Date(isoDay + 'T00:00:00Z'));
      const part = (/** @type {string} */ type) =>
        (parts.find((p) => p.type === type)?.value || '').replace(/\.$/, '');
      return [part('day'), part('month'), part('year')].filter(Boolean).join(' ');
    } catch (_err) {
      return isoDay;
    }
  };

  /**
   * Fill a blank peek row from the page: the selection's name, the level
   * the map is painting for the day, and the day itself — highlighted, as
   * the server's line is, when it is not today.
   *
   * @param {Element} row A clone of the template's row.
   * @returns {void}
   */
  const fillPeek = (row) => {
    const slot = (name) => row.querySelector('[data-region-sheet-slot="' + name + '"]');
    const level = window.pwaRegionLevel?.forRegion(regionId) || null;
    const tile = slot('rating');
    if (tile && level && LEVEL_DIGITS[level]) {
      tile.setAttribute('data-level', level);
      tile.textContent = LEVEL_DIGITS[level];
    }
    const name = slot('name');
    if (name) {
      name.textContent = regionName || regionId || '';
      name.setAttribute('title', regionName || regionId || '');
    }
    const today = todayKey();
    const day = dateKey || today;
    const dayEl = row.querySelector('[data-sheet-date-day]');
    if (dayEl) dayEl.textContent = formatDay(day);
    row.querySelector('.sheet-date-row')?.toggleAttribute('data-another-day', day !== today);
  };

  /**
   * Paint the peek row from the page, in place of whatever the sheet held.
   *
   * @returns {boolean} False when the page has no template to paint from.
   */
  const paintPeek = () => {
    const template = /** @type {HTMLTemplateElement|null} */ (
      document.getElementById('region-sheet-peek-template')
    );
    const source = template?.content?.firstElementChild;
    if (!source) return false;
    const row = source.cloneNode(true);
    fillPeek(/** @type {Element} */ (row));
    sheet.replaceChildren(row);
    mirrorAll();
    return true;
  };

  /**
   * Repaint the open body's peek row for the selection now, in place, and
   * dim the rest of the body until the new one replaces it (Hugo, 27 Oct
   * 2026): the sheet keeps its height, and the name, level and day change
   * at once, where emptying it to the peek row used to drop it to a sliver
   * and raise it again when the body landed.
   *
   * @returns {boolean} False when there is no open body to repaint.
   */
  const swapPeekInPlace = () => {
    if (!controller.isOpen()) return false;
    const live = sheet.querySelector('[data-testid="region-sheet-body"] [data-sheet-peek]')?.parentElement;
    const template = /** @type {HTMLTemplateElement|null} */ (
      document.getElementById('region-sheet-peek-template')
    );
    const source = template?.content?.firstElementChild;
    if (!live || !source) return false;
    const row = /** @type {Element} */ (source.cloneNode(true));
    fillPeek(row);
    live.replaceWith(row);
    mirrorAll();
    controller.pending({ keep: row });
    return true;
  };

  /**
   * A body fetched for this URL within BODY_TTL_MS, or null.
   *
   * @param {string} url
   * @returns {?string}
   */
  const cachedBody = (url) => {
    const hit = bodies.get(url);
    if (!hit) return null;
    if (Date.now() - hit.at > BODY_TTL_MS) {
      bodies.delete(url);
      return null;
    }
    return hit.html;
  };

  /**
   * @param {string} url
   * @param {string} html
   * @returns {void}
   */
  const keepBody = (url, html) => {
    bodies.delete(url);
    bodies.set(url, { html, at: Date.now() });
    while (bodies.size > BODY_CACHE_SIZE) bodies.delete(bodies.keys().next().value);
  };

  /**
   * @param {string} html
   * @returns {void}
   */
  const paintBody = (html, fresh = false) => {
    sheet.innerHTML = html;
    mirrorAll();
    syncTerrainControls();
    applyObservationFocus(fresh);
  };

  // SNOW-1291: A TAP ON A REPORT'S PIN. map.js selects the report's region
  // and asks, with `snowdesk:observation-focus`, for that report's row in
  // the body's "On the ground" section. The body is usually not painted
  // yet — the peek paints first and the body lands later — so the request
  // is KEPT and applied after the paint that carries the row: open the
  // section if it is a closed past-day panel, mark the row
  // (`data-focused`), raise a phone sheet to half and scroll the row into
  // view. It applies once, and is dropped when another region is selected,
  // so a later visit to the region does not jump to a stale report. It is
  // applied only to an OPEN sheet: a kept body paints before the sheet
  // opens, and a closed sheet's detent call is a no-op.
  /** @type {{submission: string, regionId: string}|null} */
  let observationFocus = null;
  // The sheet's detent height transition, plus a frame.
  const DETENT_SETTLE_MS = 320;

  /**
   * @param {boolean} [fresh] - The body was just fetched, so a row it lacks
   *   is not coming: show the section instead (SNOW-1295).
   * @returns {void}
   */
  const applyObservationFocus = (fresh = false) => {
    if (!observationFocus || observationFocus.regionId !== regionId) return;
    // A closed sheet ignores a detent, and may still hold a body the open
    // is about to repaint: wait for the open (called again just after it).
    if (!controller.isOpen()) return;
    // A uuid from our own feed; quotes and backslashes stripped all the same,
    // so nothing in it can break out of the attribute selector.
    const submission = observationFocus.submission.replace(/["\\]/g, '');
    const row = sheet.querySelector(`[data-observation-submission="${submission}"]`);
    if (!row) {
      if (!fresh) return;
      // The report is not in the region's window (filed a moment ago on a
      // device whose clock is ahead, say): the tap still lands somewhere,
      // on the section, at half, rather than on nothing.
      observationFocus = null;
      const section = sheet.querySelector('[data-on-the-ground]');
      controller.detent('half');
      if (section) setTimeout(() => section.scrollIntoView({ block: 'start', behavior: 'smooth' }), DETENT_SETTLE_MS);
      return;
    }
    observationFocus = null;
    const panel = row.closest('details');
    if (panel) panel.open = true;
    for (const el of sheet.querySelectorAll('[data-focused]')) el.removeAttribute('data-focused');
    row.setAttribute('data-focused', '');
    controller.detent('half');
    // A phone sheet rising from peek is still mid-transition (300ms of height
    // in static/css/map.css), so a scroll now measures the old height and
    // leaves the row below the fold. Scroll once the height has settled.
    setTimeout(() => row.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), DETENT_SETTLE_MS);
  };

  document.addEventListener('snowdesk:observation-focus', (ev) => {
    const detail = ev.detail || {};
    if (!detail.submission || !detail.region_id) return;
    observationFocus = { submission: String(detail.submission), regionId: String(detail.region_id) };
    // SNOW-1295: a pin can be newer than any body kept for its region, so a
    // tap always reads the region afresh.
    bodies.clear();
    // The body for the region may already be up (its sheet was open). If it
    // lacks the row, read it again: the open sheet is otherwise left as it
    // was, which read as the tap doing nothing.
    applyObservationFocus();
    if (observationFocus && controller.isOpen() && detail.region_id === regionId) load();
  });

  document.addEventListener('snowdesk:reports-changed', () => {
    bodies.clear();
    reportsEpoch = String(Date.now());
    try {
      localStorage.setItem(REPORTS_EPOCH_KEY, reportsEpoch);
    } catch (_) {
      // Storage blocked: the stamp holds for this page only.
    }
    if (controller.isOpen() && regionId && !playback.isPlaying()) load();
  });

  // SNOW-979: THE PROBLEM-TERRAIN SWITCH. A body for a region inside the
  // terrain tiles, on a day with a bulletin, carries the "Highlight
  // problem terrain" strip (public/partials/_region_sheet_terrain.html).
  // Since SNOW-1194 the layer paints every region at once from map.js, and
  // this switch writes the same state as the layers menu's row of the same
  // name — so closing the sheet or moving to another region changes nothing
  // on the map. This owns the switch, the key under it and the zoom line; map.js's
  // window.pwaBulletinTerrain owns the state.
  const TERRAIN_TOGGLE_ID = 'region-sheet-terrain-toggle';

  /**
   * Bring the switch, its key and the zoom line in line with the layer.
   * A cloned or cached body's checkbox starts unchecked, so this runs after
   * every paint as well as on every announcement.
   *
   * @param {string} [availability] From the announcement, when there is one.
   * @returns {void}
   */
  const syncTerrainControls = (availability) => {
    const input = /** @type {HTMLInputElement|null} */ (sheet.querySelector('#' + TERRAIN_TOGGLE_ID));
    if (!input) return;
    const bridge = window.pwaBulletinTerrain;
    const on = !!(bridge && bridge.isEnabled());
    input.checked = on;
    const key = sheet.querySelector('[data-region-sheet-terrain-key]');
    if (key) key.hidden = !on;
    const zoom = sheet.querySelector('[data-testid="region-sheet-terrain"] [data-panel-overlay-toggle-detail]');
    const where = availability || (bridge ? bridge.availability() : 'ok');
    if (zoom) zoom.hidden = !(on && where === 'zoom-in');
  };

  sheet.addEventListener('change', (ev) => {
    const target = /** @type {HTMLInputElement} */ (ev.target);
    if (!target || target.id !== TERRAIN_TOGGLE_ID || !window.pwaBulletinTerrain) return;
    window.pwaBulletinTerrain.setEnabled(target.checked, regionId || undefined);
  });

  // The menu row switches the same state.
  document.addEventListener('snowdesk:problem-terrain-changed', () => {
    syncTerrainControls();
  });
  // The camera moves the zoom line; the terrain filter announces where the
  // tiles can draw, which is where this layer can too.
  document.addEventListener('snowdesk:terrain-filter-changed', (ev) => {
    syncTerrainControls(ev.detail?.availability);
  });

  /**
   * Copy one roundel's state onto its proxy in the sheet.
   *
   * @param {HTMLElement} proxy
   * @returns {void}
   */
  const mirror = (proxy) => {
    const source = document.getElementById(proxy.dataset.regionSheetProxy || '');
    if (!source) {
      proxy.hidden = true;
      return;
    }
    Array.from(proxy.attributes).forEach(({ name }) => {
      if (name !== 'data-region-sheet-proxy' && isMirrored(name) && !source.hasAttribute(name)) {
        proxy.removeAttribute(name);
      }
    });
    Array.from(source.attributes).forEach(({ name, value }) => {
      if (isMirrored(name)) proxy.setAttribute(name, value);
    });
    // The roundel's classes carry its shape and every state rule in map.css
    // (.map-download-control[data-download-state="busy"]::before, …), so the
    // proxy takes them whole rather than a copy of each state's look.
    proxy.className = source.className;
    // Its glyphs too: the download roundel swaps between four, and they are
    // server-rendered markup with no ids, so a copy cannot collide.
    if (proxy.innerHTML !== source.innerHTML) proxy.innerHTML = source.innerHTML;
  };

  const mirrorAll = () => {
    sheet.querySelectorAll('[data-region-sheet-proxy]').forEach((proxy) => {
      mirror(/** @type {HTMLElement} */ (proxy));
    });
  };

  // A roundel's state changes after the press it answers (the pin toggles
  // on the response, the download runs for a while), so the proxies follow
  // the roundels rather than being set once at paint.
  if (typeof MutationObserver === 'function') {
    ['map-region-pin-control', 'map-download-control'].forEach((id) => {
      const source = document.getElementById(id);
      if (!source) return;
      new MutationObserver(mirrorAll).observe(source, {
        attributes: true,
        childList: true,
        subtree: true,
      });
    });
  }

  // SNOW-1161: a resort named in the sheet opens in its own sheet — the map
  // keeps the reader — rather than navigating to the resort page. A
  // modified click (new tab, new window) keeps the link's own behaviour,
  // and the href rides along for a map that cannot open the sheet
  // (map.js's listener falls back to it). A resort with no pin opens in
  // the sheet too, without one.
  sheet.addEventListener('click', (ev) => {
    const link = ev.target.closest?.('a[data-resort-slug]');
    if (!link || !sheet.contains(link)) return;
    if (ev.button !== 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    document.dispatchEvent(new CustomEvent('snowdesk:resort-open', {
      detail: { slug: link.getAttribute('data-resort-slug'), href: link.href },
    }));
  });

  sheet.addEventListener('click', (ev) => {
    const proxy = ev.target.closest?.('[data-region-sheet-proxy]');
    if (!proxy || !sheet.contains(proxy)) return;
    ev.preventDefault();
    if (proxy.getAttribute('aria-disabled') === 'true') return;
    document.getElementById(proxy.dataset.regionSheetProxy)?.click();
  });

  /**
   * Paint the sheet for the current region and date: a kept body at once,
   * or the peek from the page at once and the body when it arrives.
   *
   * Over a plain fetch rather than htmx.ajax so a response that lost the
   * race to a newer selection can be dropped before it paints. The body is
   * server-rendered with autoescaping on (`_region_sheet_body.html`), the
   * same grounds every other sheet on the map injects HTML on. The peek is
   * filled with textContent and attributes only.
   *
   * PAUSED (SNOW-1239), it paints only what needs no request — a kept
   * body, or the peek — under the band, and leaves the fetch for the stop.
   *
   * An open sheet keeps its body up, dimmed under a repainted peek row,
   * until the new body lands (``swapPeekInPlace``).
   *
   * @param {{paused?: boolean}} [opts]
   * @returns {Promise<void>}
   */
  const load = (opts = {}) => {
    if (!regionId) return Promise.resolve();
    // Lowercased here: the view's canonical URL is lowercase and answers
    // anything else with a redirect, which would cost a second round trip.
    let url = URL_TEMPLATE.replace('XX-0000', encodeURIComponent(regionId.toLowerCase()));
    const params = new URLSearchParams();
    if (dateKey) params.set('d', dateKey);
    if (reportsEpoch) params.set('r', reportsEpoch);
    if (params.toString()) url += '?' + params.toString();
    const kept = cachedBody(url);
    if (opts.paused) pendingRefresh = true;
    awaiting = false;
    if (kept !== null) {
      guard.invalidate();
      paintBody(kept);
      if (opts.paused) playback.markPaused(sheet, true);
      return Promise.resolve();
    }
    if (opts.paused) {
      guard.invalidate();
      if (!paintPeek()) sheet.replaceChildren();
      playback.markPaused(sheet, true);
      return Promise.resolve();
    }
    const ask = guard.begin();
    awaiting = true;
    const swapped = swapPeekInPlace();
    const peeked = swapped || paintPeek();
    if (!peeked) paintLine(STRINGS.loading);
    return fetch(url, { headers: { 'HX-Request': 'true' }, signal: ask.signal })
      .then((resp) => {
        if (!resp.ok) throw new Error('region sheet ' + resp.status);
        return resp.text();
      })
      .then((html) => {
        // Kept even when superseded: it landed, and it is that region's day.
        keepBody(url, html);
        if (!ask.isCurrent() || !controller.isOpen()) return;
        paintBody(html, true);
      })
      .catch((err) => {
        if (!ask.isCurrent() || !controller.isOpen() || playback.isAbort(err)) return;
        // The body left up under the new peek row was the last region's or
        // day's: take it down to the peek row alone.
        if (swapped) paintPeek();
        // The peek the page painted is still true: keep it, and say below
        // it that the rest could not be fetched.
        if (!peeked) {
          paintLine(STRINGS.failed);
          return;
        }
        const p = document.createElement('p');
        p.className = 'mt-4 px-2 text-sm text-text-2';
        p.textContent = STRINGS.failed;
        sheet.appendChild(p);
      });
  };

  /** @returns {void} */
  const close = () => {
    guard.invalidate();
    if (controller.isOpen()) controller.close();
  };

  document.addEventListener('snowdesk:region-selected', (ev) => {
    const detail = ev.detail || {};
    regionId = detail.region_id || null;
    regionName = detail.region_name || '';
    if (observationFocus && observationFocus.regionId !== regionId) observationFocus = null;
    if (!regionId) {
      close();
      return;
    }
    // SNOW-1239: during playback, paint without a request.
    const paused = playback.isPlaying();
    if (controller.isOpen()) {
      load({ paused });
      return;
    }
    // The sheet opens however the region was picked — a tap, a search pick,
    // a pinned region, a deep link — and on every width (SNOW-1173). On a
    // phone it is the one surface that names the selection; on a desktop it
    // is the inset panel a resort or a weather point opens, so a region tap
    // answers the way theirs do.
    // Load first: it paints the peek row synchronously (from the page, or
    // a kept body), so PEEK is measured from that row rather than the
    // empty-sheet fallback, and the sheet does not open tall and drop.
    load({ paused });
    controller.open({ awaiting });
    applyObservationFocus();
  });

  // REOPENING (2026-10-19). The sheet can close while the region stays
  // selected (its ×, a drag below peek, a route opening over it). Past z10 a
  // tap on a region changes nothing, so a selected region whose sheet was
  // closed there was outlined on the map with no way back to it. map.js's
  // tap dispatcher asks this first for a tap on the region already
  // selected, and search asks it when the region already selected is
  // picked again (SNOW-1173).
  window.pwaRegionSheet = Object.freeze({
    /**
     * Open the sheet again for the region still selected.
     *
     * @returns {boolean} Whether it opened: false with no selection, or
     *   with the sheet already open.
     */
    reopen: () => {
      if (!regionId || controller.isOpen()) return false;
      load({ paused: playback.isPlaying() });
      controller.open({ awaiting });
      applyObservationFocus();
      return true;
    },
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    const detail = ev.detail || {};
    dateKey = detail.date || null;
    if (!controller.isOpen()) return;
    // SNOW-1239: a timelapse frame during playback only records the day.
    if (!playback.shouldReload(detail.source, playback.isPlaying())) {
      pendingRefresh = true;
      guard.invalidate();
      playback.markPaused(sheet, true);
      return;
    }
    // A day picked by hand ends playback; it arrives before the stop, so
    // the stop that follows has nothing left to load.
    pendingRefresh = false;
    playback.markPaused(sheet, false);
    load();
  });

  // SNOW-1239: playback starting pauses the open sheet; stopping lifts the
  // band and loads the day it stopped on, once.
  document.addEventListener('snowdesk:timelapse-state', (ev) => {
    if (!controller.isOpen() || !regionId) return;
    if (ev.detail?.playing) {
      guard.invalidate();
      pendingRefresh = true;
      playback.markPaused(sheet, true);
      return;
    }
    playback.markPaused(sheet, false);
    if (!pendingRefresh) return;
    pendingRefresh = false;
    load();
  });
}());
