/*
 * static/js/map_weather_window.js — say when the weather layer has nothing
 * to show for the day on the map (SNOW-1196).
 *
 * The weather payload is the forecast window, today onward. On any day
 * outside it — every past day, which is most of a bulletin season — the
 * switched-on layer drew nothing and said nothing, so it read as broken.
 *
 * The layers menu's weather row says it: its detail line reads "Forecast
 * only: {first}–{last}" for as long as the layer is on and the day is
 * outside the window. That is where someone looking for the layer looks.
 * SNOW-1196 also said it on the map, as a pill; SNOW-1206 moved that behind
 * the date row's bell, and SNOW-1333 dropped it when the bell became the
 * list of bulletin alerts (Hugo, 2026-10-30).
 *
 * Reads ``window.pwaWeatherWindow`` (map.js) for whether the layer is on and
 * which days it covers, and ``readUrlDateParam`` / ``formatDatePopup``
 * (map_shared.js) for the day. Strings from
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
  });

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

  /** Bring the row into line with the day. */
  function apply() {
    const window_ = outsideWindow();
    if (summary) {
      summary.hidden = !window_;
      summary.textContent = window_ ? self.pwaStrings.interpolate(STRINGS['forecast-only'], window_) : '';
    }
  }

  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    apply();
  });
  document.addEventListener('snowdesk:weather-window-loaded', apply);
  document.addEventListener('snowdesk:overlay-visibility-changed', apply);

  apply();
})();
