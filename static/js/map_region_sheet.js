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
 */

(function regionSheetInit() {
  'use strict';

  const sheet = document.getElementById('region-sheet');
  const mapEl = document.getElementById('map');
  if (!sheet || !mapEl || !window.MapSheet) return;

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

  const controller = window.MapSheet.attach(sheet);

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
  /** The selection's own name, for the peek painted from the page. */
  let regionName = '';
  /** @type {?string} */
  let dateKey = null;
  // Invalidates a fetch when a newer one starts, so a slow answer for the
  // region the reader has already moved on from never paints over the new one.
  let seq = 0;

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
  const paintBody = (html) => {
    sheet.innerHTML = html;
    mirrorAll();
    syncTerrainControls();
  };

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

  // The menu row and the map chip switch the same state.
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
  // and the href rides along so a resort with no pin still reaches its
  // page (map.js's listener falls back to it).
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
   * @returns {Promise<void>}
   */
  const load = () => {
    if (!regionId) return Promise.resolve();
    const mine = ++seq;
    // Lowercased here: the view's canonical URL is lowercase and answers
    // anything else with a redirect, which would cost a second round trip.
    let url = URL_TEMPLATE.replace('XX-0000', encodeURIComponent(regionId.toLowerCase()));
    if (dateKey) url += '?d=' + encodeURIComponent(dateKey);
    const kept = cachedBody(url);
    if (kept !== null) {
      paintBody(kept);
      return Promise.resolve();
    }
    const peeked = paintPeek();
    if (!peeked) paintLine(STRINGS.loading);
    return fetch(url, { headers: { 'HX-Request': 'true' } })
      .then((resp) => {
        if (!resp.ok) throw new Error('region sheet ' + resp.status);
        return resp.text();
      })
      .then((html) => {
        keepBody(url, html);
        if (mine !== seq || !controller.isOpen()) return;
        paintBody(html);
      })
      .catch(() => {
        if (mine !== seq || !controller.isOpen()) return;
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
    seq += 1;
    if (controller.isOpen()) controller.close();
  };

  document.addEventListener('snowdesk:region-selected', (ev) => {
    const detail = ev.detail || {};
    regionId = detail.region_id || null;
    regionName = detail.region_name || '';
    if (!regionId) {
      close();
      return;
    }
    if (controller.isOpen()) {
      load();
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
    load();
    controller.open();
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
      load();
      controller.open();
      return true;
    },
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    dateKey = ev.detail?.date || null;
    if (controller.isOpen()) load();
  });
}());
