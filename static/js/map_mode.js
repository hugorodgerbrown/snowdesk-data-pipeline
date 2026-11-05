/*
 * static/js/map_mode.js — the map's Conditions / Terrain toggle (SNOW-1394,
 * SNOW-1421). Conditions is the `dated` mode in code.
 *
 * Two controls for one mode. The first is in the date row, two segments:
 * the date, with its day steps either side, and a mountain. The active
 * segment carries the label and the inactive one collapses to an icon:
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
 * The second is the layers menu's header (SNOW-1421): a Conditions |
 * Terrain segmented control (`[data-map-mode-tab]`, each a button with
 * aria-pressed). A tap there changes the map live, exactly as the date row
 * does, and the two stay in step because both are drawn from one `mode`.
 *
 * What the mode does is the pure module's (map_mode_core.js); this one
 * applies it:
 *
 *   - SUPPRESSION, NEVER PREFERENCE. It calls the setSuppressed hook of
 *     every layer one mode has to itself (window.pwaBulletinsLayer,
 *     pwaWeatherLayer, pwaBulletinTerrain for Conditions; pwaTerrainFilter,
 *     pwaSkiTours, pwaRestZones for Terrain) with SUPPRESSION.MAP_MODE, and
 *     never a show() or hide(), so no stored choice moves. map.js read the
 *     boot mode itself before installing a layer, and ski_tours.js and
 *     rest_zones.js read it from map.js, so neither mode's first paint
 *     flashes the other's layers; this module calls the hooks only on a
 *     change.
 *   - THE LAYERS MENU. Each mode's own rows carry data-mode-only, and
 *     map.css hides the other mode's under <html data-map-mode>; nothing
 *     here touches a row. A tab tap also brings the menu back to its first
 *     level, which is what the tabs choose between.
 *   - THE URL AND THIS DEVICE. ?mode=terrain is written with every other
 *     parameter kept, and the mode is stored, so a link and a reload both
 *     reproduce it. A date write keeps it (map_shared.js writeUrlDateParam).
 *   - THE PAGE. <html data-map-mode> is what map.css reads — the collapsed
 *     segments, the hidden season scrubber, the place sheet's dated parts —
 *     and snowdesk:map-mode-changed is what map.js, the legend and the
 *     place sheet hear.
 *   - CONDITIONS ON TODAY (SNOW-1422). `toConditionsToday()` switches to
 *     Dated and moves the map to today, not the day the reader left. It is
 *     what map.js calls before it selects a region chosen in Terrain (a
 *     search pick, a sheet's region row, a hash), since a region is not a
 *     Terrain thing, and what the place sheet's Today row calls.
 *
 * The Today panel's sun is a panel, not a map surface, and stays in both.
 *
 * Loaded after map.js, map_day_nav.js, map_calendar.js and map_today.js.
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
      'back-to-date': 'Back to Conditions',
    })
    : { 'back-to-date': 'Back to Conditions' };

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

  /** The layers menu's Conditions | Terrain segments (SNOW-1421). */
  const tabs = /** @type {HTMLElement[]} */ (Array.from(document.querySelectorAll('[data-map-mode-tab]')));

  /**
   * Every layer one mode has to itself, and the hook that holds it. The
   * bulletins' hook holds their grouping outline (`l3`) too, and the ski
   * tours' both of SAC's parts. Read lazily: map.js, ski_tours.js and
   * rest_zones.js publish them, and the last two load after this file.
   */
  const HOOKS = Object.freeze([
    ['bulletins', () => window.pwaBulletinsLayer],
    ['weather', () => window.pwaWeatherLayer],
    ['problem_terrain', () => window.pwaBulletinTerrain],
    ['terrain_filter', () => window.pwaTerrainFilter],
    ['ski_tours', () => window.pwaSkiTours],
    ['rest_zones', () => window.pwaRestZones],
  ]);

  /**
   * Draw both controls for the current mode. Touches no layer and no row.
   *
   * @returns {void}
   */
  const render = () => {
    const terrain = mode === core.MODES.TERRAIN;
    document.documentElement.dataset.mapMode = mode;
    terrainButton.setAttribute('aria-pressed', terrain ? 'true' : 'false');
    for (const tab of tabs) {
      tab.setAttribute('aria-pressed', tab.dataset.mapModeTab === mode ? 'true' : 'false');
    }
    if (dateToggle) {
      dateToggle.setAttribute('aria-label', terrain ? STRINGS['back-to-date'] : dateLabel);
      // The calendar cannot open from Terrain, so it does not say it can.
      if (terrain) dateToggle.removeAttribute('aria-controls');
      else dateToggle.setAttribute('aria-controls', 'map-calendar');
    }
  };

  /**
   * Hold the other mode's own layers and release this one's, each through
   * its suppression hook. The holds go first, so the map never draws both
   * modes' layers at once on the way over.
   *
   * @param {string} target The mode now in force.
   * @returns {void}
   */
  const suppressFor = (target) => {
    const held = HOOKS.filter(([key]) => core.isSuppressed(key, target));
    const released = HOOKS.filter(([key]) => !core.isSuppressed(key, target));
    for (const [key, resolve] of held.concat(released)) {
      resolve()?.setSuppressed?.(SUPPRESSION, core.isSuppressed(key, target));
    }
  };

  /**
   * Move the map to today, through the request the "Today" control makes,
   * so the scrubber commits it where every day change is committed. Today
   * is the page's (`#season-scrubber[data-today]`), never the browser's.
   *
   * @returns {void}
   */
  const goToToday = () => {
    const scrubber = document.getElementById('season-scrubber');
    const today = scrubber ? scrubber.dataset.today || '' : '';
    if (!today) return;
    if (new URLSearchParams(location.search).get('d') === today) return;
    document.dispatchEvent(new CustomEvent('snowdesk:scrub-to', {
      detail: { date: today, source: 'today' },
    }));
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
    suppressFor(mode);
    render();
    writeStored(mode);
    history.replaceState(history.state, '', location.pathname + core.withMode(location.search, mode) + location.hash);
    document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode } }));
  };

  terrainButton.addEventListener('click', () => setMode(core.MODES.TERRAIN));

  for (const tab of tabs) {
    tab.addEventListener('click', (e) => {
      // Inside the menu: its outside-tap dismiss must not hear this.
      e.stopPropagation();
      setMode(tab.dataset.mapModeTab || '');
      // The tabs choose what the first level lists, so an options page
      // left open (the other mode's, perhaps) goes back to it.
      const menu = document.getElementById('basemap-menu');
      const back = menu && menu.querySelector('[data-layers-page]:not([hidden]) [data-layers-back]');
      if (back instanceof HTMLElement) back.click();
    });
  }

  // Capture phase, so these run before map_calendar.js's toggle handler and
  // can stop it.
  document.addEventListener('click', (e) => {
    if (mode !== core.MODES.TERRAIN) return;
    const target = /** @type {?Element} */ (e.target instanceof Element ? e.target : null);
    if (!target) return;
    if (dateToggle && target.closest('#map-calendar-toggle')) {
      e.preventDefault();
      e.stopImmediatePropagation();
      setMode(core.MODES.DATED);
    }
  }, true);

  /**
   * Switch to Conditions on TODAY, not the day the reader left (Hugo,
   * 4 Nov 2026: what Terrain offers of the day is current conditions).
   * Whatever is open stays open and repaints with the day.
   *
   * @returns {void}
   */
  const toConditionsToday = () => {
    setMode(core.MODES.DATED);
    goToToday();
  };

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
    toConditionsToday,
  });
})();
