/*
 * static/js/terrain_filter_sheet.js — the terrain filter's sheet, menu-row
 * second line and legend section (SNOW-978, SNOW-1109).
 *
 * It talks to one thing — the ``window.pwaTerrainFilter`` bridge map.js
 * publishes ({isEnabled, show, hide, getFilter, setFilter, availability}) —
 * and knows nothing about MapLibre, tiles or storage: the bridge owns all
 * three.
 *
 * Three surfaces, all driven from the bridge's state:
 *
 *   - the sheet (#map-terrain-filter-sheet), the standard map panel, opened
 *     by the adjust button beside the layers menu's terrain filter row.
 *     The row itself is a checkbox that turns the filter on and off
 *     (map_basemap_picker.js drives it through the same bridge); opening
 *     the sheet while the filter is off turns it on first, because a sheet
 *     of settings for a layer that is not drawn changes nothing visible.
 *     MapSheet.attach empties the sheet on every close, so its body is
 *     cloned from #map-terrain-filter-template on every open;
 *   - the menu row's second line ([data-terrain-filter-summary]): hidden
 *     while the filter is off; while it is on, the short form ("3 aspects ·
 *     35°+") when it is drawing, or why it is not ("zoom in", "outside
 *     coverage") — the sheet no longer carries that line;
 *   - and the legend's terrain-filter section, shown while the filter is on,
 *     whose last line ([data-terrain-filter-legend-summary]) is the long
 *     form: "N, NE, NW · 35°+ · above 2,400 m".
 *
 * SNOW-1109 compacted the sheet: two mode tiles over the terrain-filter-mode
 * radios, a five-class slope ramp, an aspect rose, an elevation mountain and
 * two elevation lists. The ramp's tap rule, the lists' contents and the
 * mountain's band are terrain_filter_core.js's tapSlopeClass,
 * elevationOptions and elevationBand; this file only reads and writes the
 * DOM. Every colour is a CSS class or rule (src/css/main.css's .terrain-*).
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
    'elevation-all': 'All elevations',
    'elevation-line-above': 'Above %(min)s m',
    'elevation-line-below': 'Below %(max)s m',
    'elevation-any': 'Any',
    'elevation-option': '%(value)s m',
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

  /** Thousands separators in the page's own language. */
  const lang = document.documentElement.lang || undefined;
  const formatNumber = (n) => n.toLocaleString(lang);
  const fill = (template, values) => self.pwaStrings.interpolate(template, values);

  /**
   * The mountain's drawing box: the peak's and the base's y in its viewBox.
   * The template draws the triangle 62,8 8,112 116,112.
   */
  const MOUNTAIN_PEAK_Y = 8;
  const MOUNTAIN_BASE_Y = 112;

  /**
   * Show each mode's key (and the legend's heading for it) only while that
   * mode is set.
   *
   * @param {ParentNode} root Where to look.
   * @param {string} mode The filter's mode.
   * @returns {void}
   */
  const syncModeKeys = (root, mode) => {
    for (const el of root.querySelectorAll('[data-terrain-filter-key]')) {
      el.hidden = el.dataset.terrainFilterKey !== mode;
    }
  };

  /**
   * The aspect line: every selected compass point, or "All" / "No aspects".
   *
   * @param {object} filter The filter.
   * @returns {string}
   */
  const aspectLine = (filter) => {
    if (filter.aspects.length === core.OCTANTS.length) return STRINGS['aspects-all'];
    if (filter.aspects.length === 0) return STRINGS['aspects-none'];
    return filter.aspects.map((a) => STRINGS[`compass-${a}`] || core.OCTANTS[a]).join(', ');
  };

  /**
   * The slope heading's range: "35–45°" or "35°+".
   *
   * @param {object} filter The filter.
   * @returns {string}
   */
  const slopeLine = (filter) => (filter.maxSlope === null
    ? fill(STRINGS['slope-min'], { min: filter.minSlope })
    : fill(STRINGS['slope-range'], { min: filter.minSlope, max: filter.maxSlope }));

  /**
   * The elevation line: "All elevations", "Above 2,400 m", "Below 3,600 m"
   * or "2,400–3,600 m".
   *
   * @param {object} filter The filter.
   * @returns {string}
   */
  const elevationLine = (filter) => {
    const lo = filter.minElevation;
    const hi = filter.maxElevation;
    if (lo !== null && hi !== null) {
      return fill(STRINGS['elevation-range'], { min: formatNumber(lo), max: formatNumber(hi) });
    }
    if (lo !== null) return fill(STRINGS['elevation-line-above'], { min: formatNumber(lo) });
    if (hi !== null) return fill(STRINGS['elevation-line-below'], { max: formatNumber(hi) });
    return STRINGS['elevation-all'];
  };

  /**
   * Rewrite one elevation list's options, keeping its leading "Any".
   *
   * @param {HTMLSelectElement|null} select The list.
   * @param {number[]} values The heights to offer.
   * @param {number|null} current The stored bound.
   * @returns {void}
   */
  const fillElevationList = (select, values, current) => {
    if (!select) return;
    const any = document.createElement('option');
    any.value = '';
    any.textContent = STRINGS['elevation-any'];
    const options = values.map((v) => {
      const option = document.createElement('option');
      option.value = String(v);
      option.textContent = fill(STRINGS['elevation-option'], { value: formatNumber(v) });
      return option;
    });
    select.replaceChildren(any, ...options);
    select.value = current === null ? '' : String(current);
  };

  /**
   * Put the elevation band onto the mountain.
   *
   * @param {object} filter The filter.
   * @param {string} label The elevation line, for the picture's name.
   * @returns {void}
   */
  const drawMountain = (filter, label) => {
    const mountain = sheetEl.querySelector('[data-terrain-filter-mountain]');
    if (!mountain) return;
    mountain.setAttribute('aria-label', label);
    const span = MOUNTAIN_BASE_Y - MOUNTAIN_PEAK_Y;
    const band = core.elevationBand(filter);
    const top = MOUNTAIN_PEAK_Y + band.top * span;
    const bottom = MOUNTAIN_PEAK_Y + band.bottom * span;
    const rect = mountain.querySelector('[data-terrain-filter-band]');
    if (rect) {
      rect.setAttribute('y', top.toFixed(2));
      rect.setAttribute('height', Math.max(0, bottom - top).toFixed(2));
    }
    const lines = { max: [filter.maxElevation, top], min: [filter.minElevation, bottom] };
    for (const [which, [bound, y]] of Object.entries(lines)) {
      const line = mountain.querySelector(`[data-terrain-filter-bound="${which}"]`);
      if (!line) continue;
      line.toggleAttribute('hidden', bound === null);
      line.setAttribute('y1', y.toFixed(2));
      line.setAttribute('y2', y.toFixed(2));
    }
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
    for (const radio of sheetEl.querySelectorAll('input[name="terrain-filter-mode"]')) {
      radio.checked = radio.value === filter.mode;
    }
    for (const wedge of sheetEl.querySelectorAll('[data-octant]')) {
      const on = filter.aspects.includes(Number(wedge.dataset.octant));
      wedge.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    const all = sheetEl.querySelector('[data-terrain-filter-all-aspects]');
    if (all) {
      all.setAttribute('aria-pressed', filter.aspects.length === core.OCTANTS.length ? 'true' : 'false');
    }
    const ramp = sheetEl.querySelector('[data-terrain-filter-ramp]');
    if (ramp) {
      ramp.dataset.mode = filter.mode;
      const { lo, hi } = core.slopeClassRange(filter);
      for (const tile of ramp.querySelectorAll('[data-slope-class]')) {
        const index = Number(tile.dataset.slopeClass);
        tile.setAttribute('aria-pressed', index >= lo && index <= hi ? 'true' : 'false');
      }
    }
    fillElevationList(
      sheetEl.querySelector('[data-terrain-filter-min-elevation]'),
      core.elevationOptions(filter, 'min'),
      filter.minElevation,
    );
    fillElevationList(
      sheetEl.querySelector('[data-terrain-filter-max-elevation]'),
      core.elevationOptions(filter, 'max'),
      filter.maxElevation,
    );
    const lines = {
      '[data-terrain-filter-aspect-line]': aspectLine(filter),
      '[data-terrain-filter-slope-line]': slopeLine(filter),
      '[data-terrain-filter-elevation-line]': elevationLine(filter),
    };
    for (const [selector, text] of Object.entries(lines)) {
      const el = sheetEl.querySelector(selector);
      if (el) el.textContent = text;
    }
    drawMountain(filter, elevationLine(filter));
  };

  /**
   * Hand the bridge the current filter with some fields replaced.
   *
   * @param {object} changes The fields that moved.
   * @returns {void}
   */
  const commit = (changes) => {
    const b = bridge();
    if (!b) return;
    b.setFilter(Object.assign(b.getFilter(), changes));
  };

  /**
   * The aspects the rose's wedges have pressed.
   *
   * @returns {number[]}
   */
  const pressedAspects = () => Array.from(sheetEl.querySelectorAll('[data-octant][aria-pressed="true"]'))
    .map((wedge) => Number(wedge.dataset.octant));

  /**
   * Make an SVG toggle answer Enter and Space as a button does.
   *
   * @param {Element} el The toggle.
   * @param {function(): void} action What a press does.
   * @returns {void}
   */
  const bindPress = (el, action) => {
    el.addEventListener('click', action);
    el.addEventListener('keydown', (event) => {
      const key = /** @type {KeyboardEvent} */ (event).key;
      if (key !== 'Enter' && key !== ' ') return;
      event.preventDefault();
      action();
    });
  };

  /**
   * Wire the freshly cloned body's controls.
   *
   * @returns {void}
   */
  const bindSheet = () => {
    for (const wedge of sheetEl.querySelectorAll('[data-octant]')) {
      bindPress(wedge, () => {
        const on = wedge.getAttribute('aria-pressed') !== 'true';
        wedge.setAttribute('aria-pressed', on ? 'true' : 'false');
        commit({ aspects: pressedAspects() });
      });
    }
    const all = sheetEl.querySelector('[data-terrain-filter-all-aspects]');
    if (all) {
      bindPress(all, () => {
        const b = bridge();
        if (!b) return;
        const everyOn = b.getFilter().aspects.length === core.OCTANTS.length;
        commit({ aspects: everyOn ? [] : core.OCTANTS.map((_name, i) => i) });
      });
    }
    for (const tile of sheetEl.querySelectorAll('[data-slope-class]')) {
      tile.addEventListener('click', () => {
        const b = bridge();
        if (!b) return;
        commit(core.tapSlopeClass(b.getFilter(), Number(tile.dataset.slopeClass)));
      });
    }
    for (const radio of sheetEl.querySelectorAll('input[name="terrain-filter-mode"]')) {
      radio.addEventListener('change', () => commit({ mode: radio.value }));
    }
    const elevation = (selector, field) => {
      const select = sheetEl.querySelector(selector);
      if (!select) return;
      select.addEventListener('change', () => {
        commit({ [field]: select.value === '' ? null : Number(select.value) });
      });
    };
    elevation('[data-terrain-filter-min-elevation]', 'minElevation');
    elevation('[data-terrain-filter-max-elevation]', 'maxElevation');
  };

  /**
   * Open the sheet with a fresh body, turning the filter on if it is off.
   *
   * @returns {void}
   */
  const open = () => {
    const b = bridge();
    if (!b) return;
    if (!b.isEnabled()) b.show();
    sheetEl.replaceChildren(template.content.cloneNode(true));
    bindSheet();
    sheet.open();
    renderSheet();
  };

  /**
   * The menu row's second line: the short summary while the filter draws,
   * or why it does not.
   *
   * @param {object} b The bridge.
   * @param {object} filter The filter.
   * @returns {string}
   */
  const rowLine = (b, filter) => {
    const state = b.availability();
    if (state === 'zoom-in') return STRINGS['availability-zoom-in'];
    if (state === 'out-of-coverage') return STRINGS['availability-out-of-coverage'];
    return core.summarise(filter, STRINGS, { short: true, formatNumber: formatNumber });
  };

  /**
   * Repaint the menu row's second line and the legend section.
   *
   * @returns {void}
   */
  const renderChrome = () => {
    const b = bridge();
    const enabled = !!(b && b.isEnabled());
    const filter = b ? b.getFilter() : core.normaliseFilter(null);

    const summary = document.querySelector('[data-terrain-filter-summary]');
    if (summary) {
      summary.hidden = !enabled;
      summary.textContent = enabled && b ? rowLine(b, filter) : '';
    }
    const legend = document.querySelector('[data-terrain-filter-legend]');
    if (legend) legend.hidden = !enabled;
    const legendSummary = document.querySelector('[data-terrain-filter-legend-summary]');
    if (legendSummary) {
      legendSummary.textContent = enabled
        ? core.summarise(filter, STRINGS, { formatNumber: formatNumber })
        : '';
    }
    syncModeKeys(document, filter.mode);
  };

  document.addEventListener('snowdesk:terrain-filter-changed', () => {
    renderChrome();
    renderSheet();
  });

  window.pwaTerrainFilterSheet = Object.freeze({
    open: open,
    isOpen: () => sheet.isOpen(),
  });

  renderChrome();
})();
