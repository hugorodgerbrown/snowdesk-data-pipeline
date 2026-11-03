/*
 * static/js/map_mode.js — the map's Dated / Terrain toggle (SNOW-1394).
 *
 * One control in the date row, two segments: the date, with its day steps
 * either side, and a mountain. The active segment carries the label and the
 * inactive one collapses to an icon:
 *
 *     Dated:    [ ‹ Today › | ⛰ ]       Terrain:  [ 📅 | ⛰ Terrain ]
 *
 * Built from the controls the date row already has rather than beside
 * them: #map-day-prev / #map-day-next stay map_day_nav.js's, the date stays
 * #map-calendar-toggle (map_calendar.js opens the calendar from it), and
 * this module adds #map-mode-terrain after them. In Terrain the date
 * collapses to its calendar glyph, and a tap on it comes back to the dated
 * map on the day the reader left, rather than opening the calendar.
 *
 * What the mode does is the pure module's (map_mode_core.js); this one
 * applies it:
 *
 *   - SUPPRESSION, NEVER PREFERENCE. It calls each dated overlay's
 *     setSuppressed hook (window.pwaBulletinsLayer, pwaWeatherLayer,
 *     pwaCommunityReportsOverlay, pwaBulletinTerrain) with
 *     SUPPRESSION.MAP_MODE, and never a show() or hide(), so no stored
 *     choice moves. map.js read the boot mode itself before installing a
 *     layer, so a page opened on ?mode=terrain never flashes the dated
 *     layers; this module calls the hooks only on a change.
 *   - THE LAYERS MENU. A dated row shows disabled in Terrain, and a tap on
 *     it does nothing (Hugo, 2 Nov 2026). Caught in the capture phase, so
 *     the picker's own handler never hears it.
 *   - THE URL AND THIS DEVICE. ?mode=terrain is written with every other
 *     parameter kept, and the mode is stored, so a link and a reload both
 *     reproduce it. A date write keeps it (map_shared.js writeUrlDateParam).
 *   - THE PAGE. <html data-map-mode> is what map.css reads — the collapsed
 *     segments, the hidden season scrubber — and snowdesk:map-mode-changed
 *     is what the region chip and map.js's region selection hear.
 *
 * The Today panel's sun is a panel, not a map surface, and stays in both.
 *
 * Loaded after map.js, map_day_nav.js, map_calendar.js and map_today.js,
 * and before map_region_chip.js, which reads window.pwaMapMode.
 */

(function mapModeInit() {
  'use strict';

  const core = self.pwaMapModeCore;
  const terrainButton = document.getElementById('map-mode-terrain');
  if (!core || !terrainButton) return;

  const SUPPRESSION = self.pwaLayerVisibilityCore
    ? self.pwaLayerVisibilityCore.SUPPRESSION.MAP_MODE
    : 'map-mode';
  const dateToggle = document.getElementById('map-calendar-toggle');
  const dateLabel = dateToggle ? dateToggle.getAttribute('aria-label') || '' : '';
  const STRINGS = self.pwaStrings
    ? self.pwaStrings.read('map-mode-strings-template', {
      'back-to-date': 'Back to the dated map',
    })
    : { 'back-to-date': 'Back to the dated map' };

  /**
   * Read the stored mode; private mode can throw.
   *
   * @returns {?string}
   */
  const readStored = () => {
    try {
      return localStorage.getItem(core.STORAGE_KEY);
    } catch (_err) {
      return null;
    }
  };

  /**
   * Store the mode; a refused write still applies for this page.
   *
   * @param {string} mode
   * @returns {void}
   */
  const writeStored = (mode) => {
    try {
      localStorage.setItem(core.STORAGE_KEY, mode);
    } catch (_err) {
      // Private mode — the mode still holds until the page goes.
    }
  };

  let mode = core.readMode(location.search, readStored());

  /**
   * The dated rows of the layers menu, as they are now. Queried each time:
   * the menu is server-rendered once, but a row a page lacks is simply
   * absent.
   *
   * @returns {Element[]}
   */
  const datedRows = () => {
    const menu = document.getElementById('basemap-menu');
    if (!menu) return [];
    return core.DATED_ROW_SELECTORS.flatMap((selector) => Array.from(menu.querySelectorAll(selector)));
  };

  /**
   * Draw the control and the menu for the current mode. Touches no layer.
   *
   * @returns {void}
   */
  const render = () => {
    const terrain = mode === core.MODES.TERRAIN;
    document.documentElement.dataset.mapMode = mode;
    terrainButton.setAttribute('aria-pressed', terrain ? 'true' : 'false');
    if (dateToggle) {
      dateToggle.setAttribute('aria-label', terrain ? STRINGS['back-to-date'] : dateLabel);
      // The calendar cannot open from Terrain, so it does not say it can.
      if (terrain) dateToggle.removeAttribute('aria-controls');
      else dateToggle.setAttribute('aria-controls', 'map-calendar');
    }
    for (const row of datedRows()) {
      if (terrain) row.setAttribute('aria-disabled', 'true');
      else row.removeAttribute('aria-disabled');
      const item = row.closest('li');
      if (item) item.toggleAttribute('data-map-mode-disabled', terrain);
    }
  };

  /**
   * Hold or release every dated overlay through its suppression hook.
   *
   * @param {boolean} active
   * @returns {void}
   */
  const suppressDated = (active) => {
    window.pwaBulletinsLayer?.setSuppressed(SUPPRESSION, active);
    window.pwaWeatherLayer?.setSuppressed(SUPPRESSION, active);
    window.pwaCommunityReportsOverlay?.setSuppressed?.(SUPPRESSION, active);
    window.pwaBulletinTerrain?.setSuppressed?.(SUPPRESSION, active);
  };

  /**
   * Change mode: the layers, the control, the URL and this device.
   *
   * @param {string} next
   * @returns {void}
   */
  const setMode = (next) => {
    const target = core.parseMode(next);
    if (target === mode) return;
    mode = target;
    // An open calendar closes on its own: the tap that got here was outside
    // it (map_calendar.js's outside-tap dismiss).
    suppressDated(mode === core.MODES.TERRAIN);
    render();
    writeStored(mode);
    history.replaceState(history.state, '', location.pathname + core.withMode(location.search, mode) + location.hash);
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode } }));
  };

  terrainButton.addEventListener('click', () => setMode(core.MODES.TERRAIN));

  // Capture phase, so these run before map_calendar.js's toggle handler and
  // the layers picker's row handler, and can stop them.
  document.addEventListener('click', (e) => {
    if (mode !== core.MODES.TERRAIN) return;
    const target = /** @type {?Element} */ (e.target instanceof Element ? e.target : null);
    if (!target) return;
    if (dateToggle && target.closest('#map-calendar-toggle')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      setMode(core.MODES.DATED);
      return;
    }
    if (target.closest('#basemap-menu [data-map-mode-disabled]')) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  // A boot ?mode= is written through to this device, so the next bare
  // visit opens where the link left the reader. And the other way: a bare
  // URL opened in a stored Terrain gets ?mode=terrain written into it, so a
  // link copied from the address bar reproduces what the reader sees.
  writeStored(mode);
  const bootSearch = core.withMode(location.search, mode);
  if (bootSearch !== location.search) {
    history.replaceState(history.state, '', location.pathname + bootSearch + location.hash);
  }
  render();

  window.pwaMapMode = Object.freeze({
    get: () => mode,
    set: setMode,
    isTerrain: () => mode === core.MODES.TERRAIN,
  });
})();
