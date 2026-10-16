/*
 * static/js/map_region_sheet.js — the region sheet a tap opens on a phone
 * (SNOW-1098).
 *
 * Tapping a region selected it and showed nothing about it: the popup that
 * used to answer the tap covered the terrain the reader had just tapped, and
 * SNOW-895 removed it. On a phone the tap now opens #region-sheet at PEEK —
 * name, danger level, date, pin and download — which leaves that terrain in
 * view; dragging up reveals the problems and resorts (HALF) and the bulletin
 * link (FULL). The detents themselves are SNOW-1097's, bound by
 * `window.MapSheet.attach` because the partial declares them.
 *
 * WHEN IT OPENS. Only for a selection map.js marks `via: 'tap'`, and only
 * below the `sm` breakpoint. A search pick, a deep link and a pinned-region
 * row select a region too, and a sheet thrown over the map by any of those
 * would be a surface nobody asked for. On a desktop a tap keeps today's
 * behaviour; the chip's region panel, which keeps its summary at every
 * width, is the way in there.
 *
 * WHILE IT IS OPEN it follows the map: another region selected (by any
 * route) repaints it for that region at the detent it is resting at, a date
 * change repaints it for the new day, and deselecting the region closes it.
 *
 * THE PIN AND DOWNLOAD BUTTONS IN ITS HEADER ARE PROXIES for the two
 * roundels in the ribbon header (#map-region-pin-control,
 * #map-download-control). A press presses the roundel, and the roundel's
 * state — pressed, disabled, its label — is copied onto the proxy whenever
 * it changes. Each verb keeps one owner (map_region_panel.js and
 * map_region_download.js), so the sheet's star cannot disagree with the
 * header's.
 */

(function regionSheetInit() {
  'use strict';

  const sheet = document.getElementById('region-sheet');
  const mapEl = document.getElementById('map');
  if (!sheet || !mapEl || !window.MapSheet) return;

  const URL_TEMPLATE = mapEl.dataset.regionSheetUrl || '';
  if (!URL_TEMPLATE) return;

  // Tailwind's `sm` — the width at which the inset panel replaces the sheet.
  const DESKTOP_QUERY = '(min-width: 640px)';

  // Attributes a proxy copies from the roundel it stands for.
  const MIRRORED = ['aria-pressed', 'aria-disabled', 'aria-label', 'title'];

  const STRINGS = self.pwaStrings.read('region-sheet-strings-template', {
    loading: 'Loading region details…',
    failed: 'Region details are unavailable offline.',
  });

  const controller = window.MapSheet.attach(sheet);

  /** @type {?string} */
  let regionId = null;
  /** @type {?string} */
  let dateKey = null;
  // Invalidates a fetch when a newer one starts, so a slow answer for the
  // region the reader has already moved on from never paints over the new one.
  let seq = 0;

  const isPhone = () =>
    typeof window.matchMedia !== 'function' || !window.matchMedia(DESKTOP_QUERY).matches;

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
    MIRRORED.forEach((name) => {
      const value = source.getAttribute(name);
      if (value === null) proxy.removeAttribute(name);
      else proxy.setAttribute(name, value);
    });
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
        attributeFilter: MIRRORED,
      });
    });
  }

  sheet.addEventListener('click', (ev) => {
    const proxy = ev.target.closest?.('[data-region-sheet-proxy]');
    if (!proxy || !sheet.contains(proxy)) return;
    ev.preventDefault();
    if (proxy.getAttribute('aria-disabled') === 'true') return;
    document.getElementById(proxy.dataset.regionSheetProxy)?.click();
  });

  /**
   * Fetch and paint the sheet's body for the current region and date.
   *
   * Over a plain fetch rather than htmx.ajax so a response that lost the
   * race to a newer selection can be dropped before it paints. The body is
   * server-rendered with autoescaping on (`_region_sheet_body.html`), the
   * same grounds every other sheet on the map injects HTML on.
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
    if (!sheet.querySelector('[data-region-sheet-proxy]')) paintLine(STRINGS.loading);
    return fetch(url, { headers: { 'HX-Request': 'true' } })
      .then((resp) => {
        if (!resp.ok) throw new Error('region sheet ' + resp.status);
        return resp.text();
      })
      .then((html) => {
        if (mine !== seq || !controller.isOpen()) return;
        sheet.innerHTML = html;
        mirrorAll();
      })
      .catch(() => {
        if (mine !== seq || !controller.isOpen()) return;
        paintLine(STRINGS.failed);
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
    if (!regionId) {
      close();
      return;
    }
    if (controller.isOpen()) {
      load();
      return;
    }
    if (detail.via !== 'tap' || !isPhone()) return;
    controller.open();
    load();
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    dateKey = ev.detail?.date || null;
    if (controller.isOpen()) load();
  });

  // A phone turned to a width where the inset panel takes over: the sheet
  // has no desktop shape, so it closes rather than stranding a docked sheet
  // on a desktop layout.
  window.addEventListener('resize', () => {
    if (controller.isOpen() && !isPhone()) close();
  });
}());
