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
 *   - THE MAP, ONCE. #map-weather-window, beside the date control (under the
 *     top row on a phone), the first time it applies on this page — then it
 *     goes by itself after a few seconds, or on its ×. A line that came back
 *     on every day stepped through would be noise; the row keeps it.
 *
 * Reads ``window.pwaWeatherWindow`` (map.js) for whether the layer is on and
 * which days it covers, ``readUrlDateParam`` / ``formatDatePopup``
 * (map_shared.js) for the day. Strings from
 * ``map-weather-window-strings-template``.
 */
(function mapWeatherWindowInit() {
  'use strict';

  const bridge = window.pwaWeatherWindow;
  const scrubber = document.getElementById('season-scrubber');
  if (!bridge || !scrubber) return;

  const summary = document.querySelector('[data-weather-window-summary]');
  const notice = document.getElementById('map-weather-window');
  const noticeLabel = notice ? notice.querySelector('[data-weather-window-label]') : null;
  const noticeClose = notice ? notice.querySelector('[data-weather-window-close]') : null;

  const STRINGS = self.pwaStrings.read('map-weather-window-strings-template', {
    'forecast-only': 'Forecast only: %(first)s–%(last)s',
    'weather-on-map': 'Weather is forecast only: %(first)s–%(last)s',
  });

  // How long the map's line stays up before it goes by itself.
  const NOTICE_MS = 8000;

  const todayKey = scrubber.dataset.today || '';
  let shownKey = '';
  let noticeSpent = false;
  let noticeTimer = 0;

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

  /** Hide the map's line for good. */
  function dismiss() {
    if (noticeTimer) clearTimeout(noticeTimer);
    noticeTimer = 0;
    if (notice) notice.hidden = true;
  }

  /** Bring the row and the map's line into line with the day. */
  function apply() {
    const window_ = outsideWindow();
    if (summary) {
      summary.hidden = !window_;
      summary.textContent = window_ ? self.pwaStrings.interpolate(STRINGS['forecast-only'], window_) : '';
    }
    if (!notice || !noticeLabel) return;
    if (!window_) {
      dismiss();
      return;
    }
    if (noticeSpent) return;
    noticeSpent = true;
    noticeLabel.textContent = self.pwaStrings.interpolate(STRINGS['weather-on-map'], window_);
    notice.hidden = false;
    noticeTimer = setTimeout(dismiss, NOTICE_MS);
  }

  if (noticeClose) noticeClose.addEventListener('click', dismiss);

  document.addEventListener('snowdesk:date-changed', (e) => {
    shownKey = (e.detail && e.detail.date) || '';
    apply();
  });
  document.addEventListener('snowdesk:weather-window-loaded', apply);
  document.addEventListener('snowdesk:overlay-visibility-changed', apply);

  apply();
})();
