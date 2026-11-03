/*
 * static/js/map_region_chip.js — the map's region chip (SNOW-1376).
 *
 * The DOM half of map_region_chip_core.js. After every camera move and
 * whenever what the slopes show could have changed, it asks the map which
 * region sits under the centre (`regions-fill` stays queryable at the
 * fill's trace and at 0 where slopes are painted), reads its painted level
 * from `window.pwaRegionLevel` and its painted-slopes state from the
 * feature state map.js writes, and draws #map-region-chip.
 *
 * A tap opens that region's sheet through the map's own
 * `snowdesk:region-open` event, the path a map panel's region row uses: it
 * reopens the sheet for the region already selected, selects it otherwise,
 * and, asked with `frame: false`, leaves the camera where the reader put it.
 *
 * A classic script loaded after map.js: it reads the shared `MAP`, as the
 * other surface modules do. Hidden while
 * the route rail is open by map.css, not here.
 */

(function regionChipInit() {
  const chip = document.getElementById('map-region-chip');
  const core = self.pwaRegionChipCore;
  // typeof, not !MAP: a bare read of an undeclared MAP would throw rather
  // than return, on any page that loads this without the map bundle.
  if (!chip || !core || typeof MAP === 'undefined' || !MAP || !self.pwaStrings) return;

  const levelEl = chip.querySelector('[data-region-chip-level]');
  const nameEl = chip.querySelector('[data-region-chip-name]');
  const detailEl = chip.querySelector('[data-region-chip-detail]');
  const STRINGS = self.pwaStrings.read('map-region-chip-strings-template', {
    slopes: 'slopes',
    'ch-only': 'Slopes are drawn in Switzerland only',
    filter: 'Your filter',
    open: 'Open the region',
    level: 'danger level {level}',
  });

  /** @type {?string} */
  let shownRegion = null;

  /**
   * The region feature under the map's centre, if any.
   *
   * @returns {?Object}
   */
  const regionAtCentre = () => {
    if (!MAP.getLayer('regions-fill')) return null;
    const centre = MAP.project(MAP.getCenter());
    return MAP.queryRenderedFeatures(centre, { layers: ['regions-fill'] })[0] || null;
  };

  /**
   * The Steep terrain filter's own summary line, as its menu row shows it.
   *
   * @returns {string}
   */
  const filterSummary = () => {
    const el = document.querySelector('[data-terrain-filter-summary]');
    const text = el && el.textContent ? el.textContent.trim() : '';
    return text ? `${STRINGS.filter} · ${text}` : STRINGS.filter;
  };

  /** Read the map and redraw the chip. */
  const update = () => {
    // SNOW-1394: Terrain mode holds the regions with the bulletins, so the
    // chip — a region and its level — has nothing to name.
    if (window.pwaMapMode && window.pwaMapMode.isTerrain()) {
      chip.hidden = true;
      shownRegion = null;
      return;
    }
    const feature = regionAtCentre();
    const props = (feature && feature.properties) || {};
    const state = core.chipState({
      zoom: MAP.getZoom(),
      regionId: props.id || null,
      name: props.name,
      country: props.country,
      rating: props.id && window.pwaRegionLevel ? window.pwaRegionLevel.forRegion(props.id) : null,
      slopesPainted: !!(feature && (MAP.getFeatureState({ source: 'regions', id: feature.id }) || {}).terrain),
      slopesOn: !!(window.pwaBulletinTerrain && window.pwaBulletinTerrain.isEnabled()),
      filterOn: !!(window.pwaTerrainFilter && window.pwaTerrainFilter.isEnabled()),
    });
    chip.hidden = state.hidden;
    if (state.hidden) {
      shownRegion = null;
      return;
    }
    shownRegion = state.regionId;
    levelEl.dataset.level = state.level;
    levelEl.textContent = state.digit;
    nameEl.textContent = state.name;
    const detail = state.detail === 'filter' ? filterSummary() : (STRINGS[state.detail] || '');
    detailEl.textContent = detail;
    detailEl.hidden = !detail;
    // The square is aria-hidden and this label replaces the text contents,
    // so it says everything the chip shows: the region, its level and the
    // slopes line.
    const label = [`${STRINGS.open}: ${state.name}`, STRINGS.level.replace('{level}', state.digit)];
    if (detail) label.push(detail);
    chip.setAttribute('aria-label', label.join(', '));
  };

  chip.addEventListener('click', () => {
    if (!shownRegion) return;
    document.dispatchEvent(new CustomEvent('snowdesk:region-open', {
      detail: { region_id: shownRegion, frame: false },
    }));
  });

  MAP.on('moveend', update);
  MAP.on('idle', update);
  for (const name of [
    'snowdesk:date-changed',
    'snowdesk:problem-terrain-changed',
    'snowdesk:terrain-filter-changed',
    'snowdesk:map-mode-changed',
  ]) {
    document.addEventListener(name, update);
  }
  update();

  window.pwaRegionChip = Object.freeze({ update });
})();
