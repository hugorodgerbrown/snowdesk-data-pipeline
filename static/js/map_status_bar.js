/*
 * static/js/map_status_bar.js — the map status bar (SNOW-1392).
 *
 * The DOM half of map_status_bar_core.js. After every camera move, and
 * whenever a switch that changes the painted surface moves, it reads the
 * map — the zoom, the region under the centre and its level, whether the
 * bulletin layer is drawn, the slopes and Steep terrain switches, the
 * basemap — and draws #map-status-bar beside the region chip.
 *
 * Read-only: it names what is painted and points at zooming in, and
 * changes nothing. Behind the map_status_bar flag, so the element is
 * absent and this returns at once outside it.
 *
 * A classic script loaded after map.js: it reads the shared `MAP`, as the
 * region chip does.
 */

(function statusBarInit() {
  const bar = document.getElementById('map-status-bar');
  const core = self.pwaStatusBarCore;
  // typeof, not !MAP: a bare read of an undeclared MAP would throw.
  if (!bar || !core || typeof MAP === 'undefined' || !MAP || !self.pwaStrings) return;
  const map = MAP;

  const STRINGS = self.pwaStrings.read('map-status-bar-strings-template', {
    region: 'Region danger',
    'region-fill': 'colour fill',
    'region-edge': 'at the edges',
    slopes: 'Problem slopes',
    'slopes-detail': 'from the bulletin',
    'slopes-off': 'Problem slopes off',
    'slopes-ch-only': 'Problem slopes: Switzerland only',
    filter: 'Steep terrain',
    'filter-detail': 'your filter',
    'zoom-slopes': "Zoom in for the bulletin's slopes",
    'zoom-filter': 'Zoom in for Steep terrain',
    label: 'The map shows',
  });

  /**
   * Each segment kind's words: its label and the quieter second half.
   *
   * @type {Object<string, [string, string]>}
   */
  const WORDS = {
    'region-fill': [STRINGS.region, STRINGS['region-fill']],
    'region-edge': [STRINGS.region, STRINGS['region-edge']],
    slopes: [STRINGS.slopes, STRINGS['slopes-detail']],
    'slopes-off': [STRINGS['slopes-off'], ''],
    'slopes-ch-only': [STRINGS['slopes-ch-only'], ''],
    filter: [STRINGS.filter, STRINGS['filter-detail']],
  };

  /** What the bar last drew, so an unchanged map writes nothing. */
  let drawn = '';

  /**
   * The region feature under the map's centre, if any.
   *
   * @returns {?Object}
   */
  const regionAtCentre = () => {
    if (!map.getLayer('regions-fill')) return null;
    return map.queryRenderedFeatures(map.project(map.getCenter()), { layers: ['regions-fill'] })[0] || null;
  };

  /**
   * The checked basemap row's label.
   *
   * @returns {string}
   */
  const basemapLabel = () => {
    const row = document.querySelector('#basemap-menu .basemap-menu-item[data-basemap-key][aria-checked="true"]');
    return row && row.textContent ? row.textContent.trim() : '';
  };

  /**
   * Whether the bulletin layer is drawn: the edge band follows the opacity
   * step's visibility (map.js), where the fill stays installed at 0.
   *
   * @returns {boolean}
   */
  const bulletinOn = () =>
    !!map.getLayer('regions-edge') && map.getLayoutProperty('regions-edge', 'visibility') !== 'none';

  /**
   * One segment's element.
   *
   * @param {{kind: string, level: string, muted: boolean, text?: string}} segment
   * @returns {HTMLElement}
   */
  const segmentEl = (segment) => {
    const el = document.createElement('span');
    el.className = 'map-status-segment';
    el.dataset.kind = segment.kind;
    if (segment.muted) el.dataset.muted = 'true';
    const swatch = document.createElement('span');
    swatch.className = `map-status-swatch map-status-swatch--${segment.kind}`;
    if (segment.kind !== 'basemap') {
      swatch.classList.add('danger-tile');
      swatch.dataset.level = segment.level;
    }
    swatch.setAttribute('aria-hidden', 'true');
    el.appendChild(swatch);
    const [label, detail] = segment.kind === 'basemap' ? [segment.text || '', ''] : WORDS[segment.kind];
    const labelEl = document.createElement('span');
    labelEl.className = 'map-status-label';
    labelEl.textContent = label;
    el.appendChild(labelEl);
    if (detail) {
      const detailEl = document.createElement('span');
      detailEl.className = 'map-status-detail';
      detailEl.textContent = ` ${detail}`;
      el.appendChild(detailEl);
    }
    return el;
  };

  /** Read the map and redraw the bar. */
  const update = () => {
    const feature = regionAtCentre();
    const props = (feature && feature.properties) || {};
    const state = core.statusState({
      zoom: map.getZoom(),
      bulletinOn: bulletinOn(),
      level: props.id && window.pwaRegionLevel ? window.pwaRegionLevel.forRegion(props.id) : null,
      country: props.country || null,
      slopesEligible: !!window.pwaBulletinTerrain,
      slopesOn: !!(window.pwaBulletinTerrain && window.pwaBulletinTerrain.isEnabled()),
      filterOn: !!(window.pwaTerrainFilter && window.pwaTerrainFilter.isEnabled()),
      basemap: basemapLabel(),
    });
    const key = JSON.stringify(state);
    if (key === drawn) return;
    drawn = key;
    bar.replaceChildren(...state.segments.map(segmentEl));
    if (state.hint) {
      const hint = document.createElement('span');
      hint.className = 'map-status-hint';
      hint.textContent = STRINGS[state.hint];
      bar.appendChild(hint);
    }
    bar.hidden = !bar.childElementCount;
    bar.setAttribute('aria-label', STRINGS.label);
  };

  map.on('moveend', update);
  map.on('idle', update);
  for (const name of [
    'snowdesk:date-changed',
    'snowdesk:problem-terrain-changed',
    'snowdesk:terrain-filter-changed',
    'snowdesk:overlay-visibility-changed',
    'snowdesk:basemap-changed',
  ]) {
    document.addEventListener(name, update);
  }
  update();

  window.pwaStatusBar = Object.freeze({ update });
})();
