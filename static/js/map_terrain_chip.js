/*
 * static/js/map_terrain_chip.js — the problem-terrain layer names itself on
 * the map while it paints (SNOW-1196), and keeps its key and its layers-menu
 * row's second line in step.
 *
 * The layer is switched by the layers menu's "Highlight problem terrain"
 * row and by the region sheet's switch. Close the sheet and the map stayed
 * coloured red, orange and grey by a layer nothing on screen named. The
 * chip, #map-terrain-chip, is the one line that does while the layer is on:
 *
 *   - its LABEL: "Problem terrain · {day}", because what it paints is that
 *     day's bulletins and the day is the thing that changes it;
 *   - KEY: opens the legend card, whose problem-terrain section is the key,
 *     and brings that section into view;
 *   - ×: switches the layer off, the way the sheet's switch does.
 *
 * Beside the date control on a desktop, under the top row on a phone
 * (map_calendar.js moves it with the date control).
 *
 * It also shows the legend's problem-terrain section
 * ([data-problem-terrain-legend]) while the layer is on, and writes the menu
 * row's second line ([data-problem-terrain-detail]): what the row does, or
 * (SNOW-1195) that the day has no bulletin to paint.
 *
 * Reads ``window.pwaBulletinTerrain`` (map.js) and ``readUrlDateParam`` /
 * ``formatDatePopup`` (map_shared.js). Strings from
 * ``map-terrain-chip-strings-template``.
 */
(function mapTerrainChipInit() {
  'use strict';

  const chip = document.getElementById('map-terrain-chip');
  const scrubber = document.getElementById('season-scrubber');
  if (!chip || !scrubber) return;

  const label = chip.querySelector('[data-terrain-chip-label]');
  const keyButton = chip.querySelector('[data-terrain-chip-key]');
  const offButton = chip.querySelector('[data-terrain-chip-off]');

  const STRINGS = self.pwaStrings.read('map-terrain-chip-strings-template', {
    label: 'Problem terrain · %(day)s',
    detail: 'Replaces the region colour from zoom 12',
    'no-bulletin': 'No bulletin for this day',
  });

  const todayKey = scrubber.dataset.today || '';
  let shownKey = '';

  /** @returns {string} The day the map is showing. */
  function currentKey() {
    return shownKey || readUrlDateParam() || todayKey;
  }

  /** @returns {boolean} Whether the problem-terrain layer is painting. */
  function painting() {
    const bridge = window.pwaBulletinTerrain;
    return !!(bridge && bridge.isEnabled());
  }

  /** Show the chip and the key while the layer paints, naming the day. */
  function apply() {
    const on = painting();
    chip.hidden = !on;
    if (on && label) {
      label.textContent = self.pwaStrings.interpolate(STRINGS.label, {
        day: formatDatePopup(currentKey()),
      });
    }
    const legend = document.querySelector('[data-problem-terrain-legend]');
    if (legend) legend.hidden = !on;
    const detail = document.querySelector('[data-problem-terrain-detail]');
    if (detail) {
      const bridge = window.pwaBulletinTerrain;
      const empty = !!(bridge && typeof bridge.dayEmpty === 'function' && bridge.dayEmpty());
      detail.textContent = empty ? STRINGS['no-bulletin'] : STRINGS.detail;
    }
  }

  /** Open the legend card and bring the problem-terrain key into view. */
  function openKey() {
    const legend = document.getElementById('map-legend');
    const toggle = document.getElementById('map-legend-toggle');
    if (legend && toggle && legend.dataset.state !== 'expanded') toggle.click();
    const section = document.querySelector('[data-problem-terrain-legend]');
    if (section && typeof section.scrollIntoView === 'function') {
      section.scrollIntoView({ block: 'nearest' });
    }
  }

  if (keyButton) {
    keyButton.addEventListener('click', (e) => {
      // The legend toggle's own outside-click dismiss must not see this.
      e.stopPropagation();
      openKey();
    });
  }
  if (offButton) {
    offButton.addEventListener('click', () => {
      if (window.pwaBulletinTerrain) window.pwaBulletinTerrain.setEnabled(false);
    });
  }

  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    apply();
  });
  document.addEventListener('snowdesk:problem-terrain-changed', apply);

  apply();
})();
