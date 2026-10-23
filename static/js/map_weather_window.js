/*
 * static/js/map_weather_window.js — say when the weather layer has nothing
 * to show for the day on the map (SNOW-1196).
 *
 * The weather payload is the forecast window, today onward. On any day
 * outside it — every past day, which is most of a bulletin season — the
 * switched-on layer drew nothing and said nothing, so it read as broken.
 *
 * Two places say it now:
 *
 *   - THE LAYERS MENU'S WEATHER ROW. Its detail line reads "Forecast only:
 *     {first}–{last}" for as long as the layer is on and the day is outside
 *     the window. That is where someone looking for the layer looks.
 *   - THE DAY'S NOTES. A note in map_day_notes.js's list, for as long as it
 *     holds. SNOW-1196 drew it once on the map, as a pill beside the date
 *     control that went by itself after 8 s; SNOW-1206 moved it behind the
 *     notes bell, whose dot is what says something new is there.
 *
 * Reads ``window.pwaWeatherWindow`` (map.js) for whether the layer is on and
 * which days it covers, ``readUrlDateParam`` / ``formatDatePopup``
 * (map_shared.js) for the day, and writes ``window.pwaDayNotes``
 * (map_day_notes.js, loaded first). Strings from
 * ``map-weather-window-strings-template``.
 */
(function mapWeatherWindowInit() {
  'use strict';

  const bridge = window.pwaWeatherWindow;
  const scrubber = document.getElementById('season-scrubber');
  if (!bridge || !scrubber) return;

  const summary = document.querySelector('[data-weather-window-summary]');

  const STRINGS = self.pwaStrings.read('map-weather-window-strings-template', {
    'forecast-only': 'Forecast only: %(first)s–%(last)s',
    'note-title': 'Weather is a forecast',
    'note-detail': 'It covers %(first)s–%(last)s, so this day has none.',
  });

  // The note's id in window.pwaDayNotes, and its place: after the bulletin.
  const NOTE_ID = 'weather';
  const NOTE_RANK = 2;

  const todayKey = scrubber.dataset.today || '';
  let shownKey = '';

  /** @returns {string} The day the map is showing. */
  function currentKey() {
    return shownKey || readUrlDateParam() || todayKey;
  }

  /**
   * The window to name, or null when the day is inside it or the layer
   * is off or not loaded.
   *
   * @returns {?{ first: string, last: string }}
   */
  function outsideWindow() {
    if (!bridge.isOn()) return null;
    const dates = bridge.dates();
    if (!dates.length) return null;
    if (dates.indexOf(currentKey()) !== -1) return null;
    return { first: formatDatePopup(dates[0]), last: formatDatePopup(dates[dates.length - 1]) };
  }

  /** Bring the row and the day's note into line with the day. */
  function apply() {
    const window_ = outsideWindow();
    if (summary) {
      summary.hidden = !window_;
      summary.textContent = window_ ? self.pwaStrings.interpolate(STRINGS['forecast-only'], window_) : '';
    }
    const notes = window.pwaDayNotes;
    if (!notes) return;
    notes.set(
      NOTE_ID,
      window_
        ? {
            title: STRINGS['note-title'],
            detail: self.pwaStrings.interpolate(STRINGS['note-detail'], window_),
            rank: NOTE_RANK,
          }
        : null
    );
  }

  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    apply();
  });
  document.addEventListener('snowdesk:weather-window-loaded', apply);
  document.addEventListener('snowdesk:overlay-visibility-changed', apply);

  apply();
})();
