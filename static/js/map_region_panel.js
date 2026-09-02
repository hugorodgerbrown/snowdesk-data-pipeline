/*
 * static/js/map_region_panel.js — the region + date surface (SNOW-801).
 *
 * The #region-readout chip at the map's top-left is a disclosure button;
 * this module is what it discloses. The panel answers "what is this region,
 * on this day" in one place: the danger chip and geographic breadcrumb the
 * map popup used to carry, the bulletin link, and the resorts inside the
 * region.
 *
 * WHY A PANEL ON THE CHIP RATHER THAN A MARK ON THE MAP. Three map-anchored
 * treatments were tried and rejected — a chevron roundel, the EAWS danger
 * pictogram, and an (i) roundel, each on the region's centroid. The problem
 * was never the mark: a second affordance on the map led to a restatement of
 * what the chip already said (name, danger level, a way to the bulletin), so
 * nothing put on the map for it could feel earned. Hanging the surface off
 * the chip means the affordance already exists, is already discoverable, and
 * the panel has room to carry what the chip cannot.
 *
 * The view-bulletin roundel that used to sit beside the chip is gone: this
 * panel carries an "Open bulletin for <date>" link, so the roundel was a
 * second control to a single destination. The download roundel stays — it
 * has a destination of its own.
 *
 * Content comes from /api/region/<id>/summary/ — the same server-rendered,
 * autoescaped HTML the MapLibre popup used, so there is one definition of
 * what a region summary says — plus /api/resorts-by-region/ for the resort
 * list. Both are already fetched by the map for other reasons and are
 * cacheable, so opening the panel is usually free.
 *
 * NOT YET COVERED BY TESTS. There is no tests/js suite for this module, and
 * that is a gap rather than a decision — its logic (the render cache key,
 * the fetch sequencing, the open/close state) is exactly the kind jsdom can
 * hold, so it belongs in tests/js rather than tests/e2e. Tracked on SNOW-801.
 *
 * The content stops at the resorts list. Neighbouring regions' ratings and
 * the week's trend are the obvious next two; the ratings cache already holds
 * what the trend would need.
 */

(function regionPanelInit() {
  'use strict';

  const chip = document.getElementById('region-readout');
  const panel = document.getElementById('region-panel');
  const mapEl = document.getElementById('map');
  const ribbon = document.getElementById('season-ribbon');
  if (!chip || !panel || !mapEl) return;

  const SUMMARY_URL_TEMPLATE = mapEl.dataset.regionSummaryUrl || '';
  const RESORTS_URL = mapEl.dataset.resortsByRegionUrl || '/api/resorts-by-region/';
  const OVERLAY_NAME = 'region-panel';

  // The region and date the panel is currently showing, so a re-open with
  // nothing changed does not refetch, and a change while open repaints.
  let currentRegionId = null;
  let currentDate = null;
  let renderedKey = null;
  let resortsByRegion = null;
  // Invalidates an in-flight fetch when a newer one starts, mirroring
  // openRegionPopup's summarySeq guard in map.js.
  let seq = 0;

  const isOpen = () => !panel.hidden;

  /**
   * Close the panel and return the chip to its collapsed state.
   *
   * @returns {void}
   */
  const close = () => {
    if (!isOpen()) return;
    panel.hidden = true;
    chip.setAttribute('aria-expanded', 'false');
    ribbon?.classList.remove('is-open');
  };

  // Server-translated strings. `makemessages` does not scan JavaScript, so a
  // literal here would ship as English to every locale — the i18n-lint rule.
  // The fallbacks are not decoration: a page that renders this module without
  // the template must still put words on screen.
  const STRINGS = self.pwaStrings.read('region-panel-strings-template', {
    resorts: 'Resorts in this region',
    unavailable: 'Region details are unavailable offline.',
  });

  /**
   * Render the panel body for one region and date.
   *
   * @param {string} regionId The EAWS region id.
   * @param {?string} dateKey The displayed date (YYYY-MM-DD), or null.
   * @returns {Promise<void>}
   */
  const render = async (regionId, dateKey) => {
    const key = `${regionId}|${dateKey || ''}`;
    if (key === renderedKey) return;

    const mine = ++seq;
    let summaryHtml = '';
    try {
      let url = SUMMARY_URL_TEMPLATE.replace('XX-0000', encodeURIComponent(regionId));
      if (dateKey) url += '?d=' + encodeURIComponent(dateKey);
      const resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (mine !== seq) return;
      if (resp.ok) summaryHtml = (await resp.json()).html || '';
    } catch {
      // Offline, or the endpoint is unreachable. The panel still opens and
      // says so rather than hanging on an empty box.
    }
    if (mine !== seq) return;

    if (resortsByRegion === null) {
      try {
        const resp = await fetch(RESORTS_URL, { headers: { Accept: 'application/json' } });
        resortsByRegion = resp.ok ? await resp.json() : {};
      } catch {
        resortsByRegion = {};
      }
    }
    if (mine !== seq) return;

    panel.textContent = '';

    if (summaryHtml) {
      // Server-rendered by Django templates with autoescaping on — the same
      // HTML map.js hands to Popup.setHTML, and safe on the same grounds.
      const section = document.createElement('div');
      section.className = 'region-panel-section';
      section.innerHTML = summaryHtml;
      // The summary's own header is the danger chip and the region name —
      // exactly what the ribbon above is already showing. Dropping it is
      // what stops the surface saying the region's name twice. A real seam
      // in the server template (public/_region_tooltip.html), not a guess at
      // the markup: everything else in there (breadcrumb, bulletin link,
      // editorial) is content the ribbon has no room for.
      section.querySelector('.region-tooltip-header')?.remove();
      panel.appendChild(section);
    } else {
      const p = document.createElement('p');
      p.className = 'region-panel-empty';
      p.textContent = STRINGS.unavailable;
      panel.appendChild(p);
    }

    const resorts = (resortsByRegion && resortsByRegion[regionId]) || [];
    if (resorts.length) {
      const section = document.createElement('div');
      section.className = 'region-panel-section';
      const eyebrow = document.createElement('p');
      eyebrow.className = 'region-panel-eyebrow';
      eyebrow.textContent = STRINGS.resorts;
      const list = document.createElement('ul');
      list.className = 'region-panel-resorts';
      resorts.forEach((name) => {
        const li = document.createElement('li');
        li.textContent = name;
        list.appendChild(li);
      });
      section.appendChild(eyebrow);
      section.appendChild(list);
      panel.appendChild(section);
    }

    renderedKey = key;
  };

  /**
   * Open the panel for the currently selected region.
   *
   * @returns {void}
   */
  const open = () => {
    if (!currentRegionId) return;
    // Close every other map overlay first, so the two are never both on
    // screen for a frame. The registry owns the matrix; this surface only
    // states that it is opening.
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
    panel.hidden = false;
    chip.setAttribute('aria-expanded', 'true');
    ribbon?.classList.add('is-open');
    render(currentRegionId, currentDate);
  };

  chip.addEventListener('click', (ev) => {
    // Without this the opening click reads as a click-away to the document
    // handler below and the panel closes on the same gesture that opened it.
    ev.stopPropagation();
    if (isOpen()) close(); else open();
  });

  // Outside click and Escape. Both only ever close THIS surface; the region
  // selection, the ribbon and the hash are untouched.
  //
  // A click on the MAP is not "outside". It arrives before the
  // snowdesk:region-selected it causes, so treating it as a dismissal
  // closed the panel on the very gesture that picked the region it should
  // have been updating. The map drives the panel through the selection
  // event instead: selecting a region repaints it, deselecting closes it.
  document.addEventListener('click', (ev) => {
    if (!isOpen()) return;
    if (panel.contains(ev.target)) return;
    if (mapEl.contains(ev.target)) return;
    close();
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && isOpen()) {
      close();
      chip.focus();
    }
  });

  // A region with nothing selected has nothing to disclose. map.js already
  // renders the chip's own "No region selected" state; this keeps the
  // control honest about being inert in it.
  const syncEnabled = () => {
    chip.disabled = !currentRegionId;
  };

  document.addEventListener('snowdesk:region-selected', (ev) => {
    currentRegionId = ev.detail?.region_id || null;
    renderedKey = null;
    syncEnabled();
    if (!currentRegionId) {
      close();
      return;
    }
    if (isOpen()) render(currentRegionId, currentDate);
  });

  document.addEventListener('snowdesk:date-changed', (ev) => {
    currentDate = ev.detail?.date || null;
    renderedKey = null;
    if (isOpen() && currentRegionId) render(currentRegionId, currentDate);
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen, close });
  syncEnabled();
}());
