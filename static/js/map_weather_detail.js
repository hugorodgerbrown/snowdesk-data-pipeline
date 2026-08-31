/**
 * static/js/map_weather_detail.js — the weather sheet behind a symbol tap
 * (SNOW-761).
 *
 * The weather overlay draws one condition symbol per public Location. This
 * module is what happens when one is tapped: fetch that location's day from
 * `api:weather_detail`, put the server's HTML in the shared map sheet, and
 * open it.
 *
 * WHERE THE BOUNDARY IS. map.js owns the map and binds the tap, because the
 * layer is its; this module owns the sheet and knows nothing about MapLibre.
 * They meet at `window.pwaWeatherDetail.open(locationId, dateKey)` and
 * nowhere else — which is why this file has no reference to `map`, a layer
 * id, or a feature, and map.js has none to the sheet.
 *
 * The sheet itself is `window.MapSheet.attach`, the same controller the
 * favourite, report and route sheets use. That is not tidiness: it is where
 * Escape-to-close, click-outside, the `overlay:dismissed` teardown and — the
 * one that matters — registration with `window.pwaMapOverlays` all come
 * from. An overlay that registers nowhere stays open under the next one.
 *
 * NO CLIENT-SIDE RENDERING. Every figure in the sheet is rendered by Django
 * and arrives as HTML, including the SVG chart. The alternative — shipping
 * the numbers and drawing them here — would need the forecast a second time
 * on the wire, put translated strings in a file `makemessages` cannot see,
 * and duplicate a panel the resort page already renders server-side.
 */

(function () {
  'use strict';

  var mapEl = document.getElementById('map');
  var sheetEl = document.getElementById('weather-sheet');
  if (!mapEl || !sheetEl || !window.MapSheet) return;

  // Rendered via {% url 'api:weather_detail' location_id=0 %}; the id is a
  // placeholder to substitute, matching map.js's resort-popup handling.
  var URL_TEMPLATE = mapEl.dataset.weatherDetailUrl || '';

  // `read` takes the whole fallback map and returns the whole map back,
  // each key overridden by the template's translation when it has one — it
  // is not a per-key getter. The English literals stay here as the fallback
  // for a page that somehow renders without the template.
  var strings = window.pwaStrings
    ? window.pwaStrings.read('weather-detail-strings-template', {
      loading: 'Loading weather…',
      failed: "That weather couldn't be loaded — check your connection.",
    })
    : {
      loading: 'Loading weather…',
      failed: "That weather couldn't be loaded — check your connection.",
    };

  var sheet = window.MapSheet.attach(sheetEl, {});

  // Bumped on every open. A tap on a second symbol while the first is still
  // in flight must not have the first response land in the sheet on top of
  // it — mountain valleys are dense enough at low zoom that a mis-tap
  // followed by a correction is the normal case, not an edge one.
  var requestToken = 0;

  /**
   * Fill the sheet with a plain message, styled like the body it replaces.
   *
   * @param {string} message Text to show.
   */
  function showMessage(message) {
    var wrap = document.createElement('div');
    wrap.className = 'flex flex-col min-h-0 flex-1 px-4 py-4';
    var line = document.createElement('p');
    line.className = 'text-sm text-text-2';
    // textContent, not innerHTML: these strings come from a template the
    // page rendered, but nothing here needs markup and the narrower call
    // cannot grow into an injection later.
    line.textContent = message;
    wrap.appendChild(line);
    sheetEl.replaceChildren(wrap);
  }

  /**
   * Open the sheet for one location on one date.
   *
   * Opens IMMEDIATELY with a loading line rather than after the fetch
   * resolves. A tap that shows nothing until the network answers reads as a
   * tap that missed, and the second tap then closes what the first opened.
   *
   * @param {number|string} locationId Location primary key from the feed's
   *   `location_id` property.
   * @param {string} [dateKey] ISO date the map is showing. Omitted, the
   *   server answers for today.
   * @returns {Promise<boolean>} Whether the body was rendered.
   */
  async function open(locationId, dateKey) {
    if (locationId == null || !URL_TEMPLATE) return false;

    var token = ++requestToken;
    sheet.open();
    showMessage(strings.loading);

    var url = URL_TEMPLATE.replace(
      '/weather/0/detail/',
      '/weather/' + encodeURIComponent(String(locationId)) + '/detail/',
    );
    if (dateKey) url += '?date=' + encodeURIComponent(dateKey);

    try {
      var resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!resp.ok) throw new Error('weather detail ' + resp.status);
      var data = await resp.json();
      // A later tap won this race; its own response owns the sheet.
      if (token !== requestToken) return false;
      // Server-trusted HTML: rendered by Django templates with autoescape
      // on. The same basis on which map.js calls popup.setHTML.
      sheetEl.innerHTML = data.html;
      return true;
    } catch (err) {
      if (token !== requestToken) return false;
      showMessage(strings.failed);
      return false;
    }
  }

  window.pwaWeatherDetail = Object.freeze({
    open: open,
    close: sheet.close,
    isOpen: sheet.isOpen,
  });
}());
