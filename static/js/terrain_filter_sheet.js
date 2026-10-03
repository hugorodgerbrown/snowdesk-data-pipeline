/*
 * static/js/terrain_filter_sheet.js — the terrain filter's sheet, map chip and
 * menu-row summary (SNOW-978).
 *
 * The filter's only control. It talks to one thing — the
 * ``window.pwaTerrainFilter`` bridge map.js publishes
 * ({isEnabled, show, hide, getFilter, setFilter, availability}) — and knows
 * nothing about MapLibre, tiles or storage: the bridge owns all three.
 *
 * Three surfaces, all driven from the bridge's state:
 *
 *   - the sheet (#map-terrain-filter-sheet), opened by the layers menu's
 *     "Terrain filter…" row and by the chip. MapSheet.attach empties it on
 *     every close, so its body is cloned from
 *     #map-terrain-filter-template on every open;
 *   - the chip on the map (#map-terrain-filter-chip), visible while the
 *     filter is on, repeating it in words: "N, NE, NW · 35°+ · above 2,400 m";
 *   - the menu row's second line ([data-terrain-filter-summary]), the short
 *     form: "3 aspects · 35°+";
 *   - and the legend's terrain-filter section, shown while the filter is on.
 *
 * Every word it writes comes from #map-terrain-filter-strings-template via
 * ``window.pwaStrings.read`` — makemessages never scans JavaScript — with the
 * English literals below as the fallback.
 *
 * map.js announces every change on ``snowdesk:terrain-filter-changed``
 * (on/off, a new filter, the camera moving into or out of a state where the
 * filter can show anything), and this file repaints from it.
 */

(function terrainFilterSheetInit() {
  'use strict';

  const sheetEl = document.getElementById('map-terrain-filter-sheet');
  const template = document.getElementById('map-terrain-filter-template');
  const core = self.pwaTerrainFilterCore;
  if (!sheetEl || !template || !core || !window.MapSheet) return;

  const STRINGS = self.pwaStrings.read('map-terrain-filter-strings-template', {
    'availability-ok': 'Matching ground is highlighted.',
    'availability-off': 'The filter is off.',
    'availability-zoom-in': 'Zoom in further to see the filter.',
    'availability-out-of-coverage': 'No terrain data here — the filter covers Switzerland only.',
    'aspects-all': 'All aspects',
    'aspects-none': 'No aspects',
    'aspects-one': '1 aspect',
    'aspects-count': '%(count)s aspects',
    'slope-min': '%(min)s°+',
    'slope-range': '%(min)s–%(max)s°',
    'elevation-above': 'above %(min)s m',
    'elevation-below': 'below %(max)s m',
    'elevation-range': '%(min)s–%(max)s m',
    'compass-0': 'N',
    'compass-1': 'NE',
    'compass-2': 'E',
    'compass-3': 'SE',
    'compass-4': 'S',
    'compass-5': 'SW',
    'compass-6': 'W',
    'compass-7': 'NW',
  });

  /** The bridge, read lazily: map.js publishes it from inside its IIFE. */
  const bridge = () => window.pwaTerrainFilter;

  const sheet = window.MapSheet.attach(sheetEl, {
    triggerSelector: '[data-terrain-filter-open]',
  });

  const chip = document.getElementById('map-terrain-filter-chip');

  /** Thousands separators in the page's own language. */
  const lang = document.documentElement.lang || undefined;
  const formatNumber = (n) => n.toLocaleString(lang);

  /**
   * The availability line's text for the current state.
   *
   * @returns {string}
   */
  const availabilityText = () => {
    const b = bridge();
    if (!b || !b.isEnabled()) return STRINGS['availability-off'];
    const state = b.availability();
    if (state === 'zoom-in') return STRINGS['availability-zoom-in'];
    if (state === 'out-of-coverage') return STRINGS['availability-out-of-coverage'];
    return STRINGS['availability-ok'];
  };

  /**
   * Put the bridge's state into the open sheet's controls.
   *
   * @returns {void}
   */
  const renderSheet = () => {
    const b = bridge();
    if (!b || !sheet.isOpen()) return;
    const filter = b.getFilter();
    const toggle = sheetEl.querySelector('#map-terrain-filter-enabled');
    if (toggle) toggle.checked = b.isEnabled();
    for (const button of sheetEl.querySelectorAll('[data-octant]')) {
      const on = filter.aspects.includes(Number(button.dataset.octant));
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    const minSlope = sheetEl.querySelector('[data-terrain-filter-min-slope]');
    if (minSlope) minSlope.value = String(filter.minSlope);
    const maxSlope = sheetEl.querySelector('[data-terrain-filter-max-slope]');
    if (maxSlope) {
      maxSlope.value = filter.maxSlope === null ? '' : String(filter.maxSlope);
      // A maximum at or under the minimum is meaningless; grey those out.
      for (const option of maxSlope.options) {
        option.disabled = option.value !== '' && Number(option.value) <= filter.minSlope;
      }
    }
    const minElevation = sheetEl.querySelector('[data-terrain-filter-min-elevation]');
    if (minElevation) minElevation.value = filter.minElevation === null ? '' : String(filter.minElevation);
    const maxElevation = sheetEl.querySelector('[data-terrain-filter-max-elevation]');
    if (maxElevation) maxElevation.value = filter.maxElevation === null ? '' : String(filter.maxElevation);
    const availability = sheetEl.querySelector('[data-terrain-filter-availability]');
    if (availability) availability.textContent = availabilityText();
  };

  /**
   * Read the controls back into a filter and hand it to the bridge.
   *
   * @returns {void}
   */
  const commit = () => {
    const b = bridge();
    if (!b) return;
    const aspects = Array.from(sheetEl.querySelectorAll('[data-octant][aria-pressed="true"]'))
      .map((button) => Number(button.dataset.octant));
    const value = (selector) => {
      const el = sheetEl.querySelector(selector);
      return el && el.value !== '' ? Number(el.value) : null;
    };
    b.setFilter({
      aspects: aspects,
      minSlope: value('[data-terrain-filter-min-slope]'),
      maxSlope: value('[data-terrain-filter-max-slope]'),
      minElevation: value('[data-terrain-filter-min-elevation]'),
      maxElevation: value('[data-terrain-filter-max-elevation]'),
    });
  };

  /**
   * Wire the freshly cloned body's controls.
   *
   * @returns {void}
   */
  const bindSheet = () => {
    const toggle = sheetEl.querySelector('#map-terrain-filter-enabled');
    if (toggle) {
      toggle.addEventListener('change', () => {
        const b = bridge();
        if (!b) return;
        if (toggle.checked) b.show();
        else b.hide();
      });
    }
    for (const button of sheetEl.querySelectorAll('[data-octant]')) {
      button.addEventListener('click', () => {
        const on = button.getAttribute('aria-pressed') !== 'true';
        button.setAttribute('aria-pressed', on ? 'true' : 'false');
        commit();
      });
    }
    for (const control of sheetEl.querySelectorAll('select, input[type="number"]')) {
      control.addEventListener('change', commit);
    }
  };

  /**
   * Open the sheet with a fresh body.
   *
   * @returns {void}
   */
  const open = () => {
    if (!bridge()) return;
    sheetEl.replaceChildren(template.content.cloneNode(true));
    bindSheet();
    sheet.open();
    renderSheet();
  };

  /**
   * Repaint the chip, the menu row's summary and the legend section.
   *
   * @returns {void}
   */
  const renderChrome = () => {
    const b = bridge();
    const enabled = !!(b && b.isEnabled());
    const filter = b ? b.getFilter() : core.normaliseFilter(null);

    if (chip) {
      chip.hidden = !enabled;
      const label = chip.querySelector('[data-terrain-filter-chip-text]');
      if (label) label.textContent = core.summarise(filter, STRINGS, { formatNumber: formatNumber });
    }
    const summary = document.querySelector('[data-terrain-filter-summary]');
    if (summary) {
      summary.hidden = !enabled;
      summary.textContent = enabled
        ? core.summarise(filter, STRINGS, { short: true, formatNumber: formatNumber })
        : '';
    }
    const legend = document.querySelector('[data-terrain-filter-legend]');
    if (legend) legend.hidden = !enabled;
  };

  document.addEventListener('snowdesk:terrain-filter-changed', () => {
    renderChrome();
    renderSheet();
  });

  if (chip) {
    chip.addEventListener('click', (event) => {
      // The chip sits over the map; the map's own click handlers must not
      // read the tap as a tap on the ground beneath it.
      event.stopPropagation();
      open();
    });
  }

  window.pwaTerrainFilterSheet = Object.freeze({
    open: open,
    isOpen: () => sheet.isOpen(),
  });

  renderChrome();
})();
