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
 * They meet at `window.pwaWeatherDetail.open(shortId, dateKey)` and
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
 * and duplicate a panel the location forecast page already renders server-side.
 *
 * PAUSED DURING PLAYBACK (SNOW-1239). A timeline frame changes the day five
 * times a second; the sheet used to fetch on every one and cancel none. While
 * the timeline plays it stays open under a "Playback in progress" band
 * (window.pwaSheetPlayback, sheet_playback_core.js), asks for nothing, and
 * loads once, for the day playback stopped on, when it stops. A newer ask,
 * and every close, aborts the one in flight.
 */

(function () {
  'use strict';

  var mapEl = document.getElementById('map');
  var sheetEl = document.getElementById('weather-sheet');
  var playback = window.pwaSheetPlayback;
  if (!mapEl || !sheetEl || !window.MapSheet || !playback) return;

  // Rendered via {% url 'api:weather_detail' short_id='__SHORTID__' %}; the
  // placeholder is substituted per tap, matching map_place_sheet.js's
  // resort handling. The feed's `short_id` is opaque, never a pk (SNOW-797).
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

  // One request in flight. A tap on a second symbol while the first is still
  // in flight must not have the first response land in the sheet on top of
  // it — mountain valleys are dense enough at low zoom that a mis-tap
  // followed by a correction is the normal case, not an edge one. A close
  // by any route (onTeardown) aborts it too (SNOW-1239).
  var guard = playback.createGuard();

  // SNOW-1239: a day the sheet skipped while the timeline played, to load
  // once when it stops. The day itself is `shownDate`.
  var pendingRefresh = false;
  /** @type {?string} */
  var shownDate = null;

  var sheet = window.MapSheet.attach(sheetEl, {
    onTeardown: function () {
      guard.invalidate();
      pendingRefresh = false;
      playback.markPaused(sheetEl, false);
    },
  });

  // SNOW-1195: the location the open sheet is showing, so a change of day
  // can reload it. The calendar no longer closes this sheet on its way in
  // (it is an object sheet), so without this the map would move to the new
  // day under a sheet still showing the old one.
  var shownShortId = null;

  // The tapped symbol's [lng, lat], for the leader line (sheet_leader.js,
  // Hugo, 24 Oct 2026). Kept across a change of day: it is the same place.
  var shownLngLat = null;

  /**
   * Fill the sheet with a plain message, styled like the body it replaces.
   *
   * @param {string} message Text to show.
   */
  function showMessage(message) {
    var wrap = document.createElement('div');
    // SNOW-1101: the line is the whole peek, so a phone sheet that opens
    // on it settles at its height rather than at the fallback peek.
    wrap.setAttribute('data-sheet-peek', '');
    wrap.className = 'py-2';
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
   * @param {string} shortId Location short id from the feed's `short_id`
   *   property.
   * @param {string} [dateKey] ISO date the map is showing. Omitted, the
   *   server answers for today.
   * @param {Array<number>} [lngLat] The symbol's [lng, lat], for the leader
   *   line. Omitted, no line is drawn.
   * @returns {Promise<boolean>} Whether the body was rendered.
   */
  async function open(shortId, dateKey, lngLat) {
    if (shortId == null || !URL_TEMPLATE) return false;

    shownShortId = shortId;
    shownLngLat = Array.isArray(lngLat) ? lngLat : null;
    shownDate = dateKey || null;
    // SNOW-1239: opened during playback, the sheet shows the band alone and
    // asks for nothing until the timeline stops.
    if (playback.isPlaying()) {
      guard.invalidate();
      pendingRefresh = true;
      sheetEl.replaceChildren();
      playback.markPaused(sheetEl, true);
      sheet.open();
      document.dispatchEvent(new CustomEvent('snowdesk:sheet-leader-point'));
      return false;
    }
    // Paint first: the sheet measures its peek as it opens, so the loading
    // line must already be there or it opens at the fallback height.
    showMessage(strings.loading);
    sheet.open();
    document.dispatchEvent(new CustomEvent('snowdesk:sheet-leader-point'));
    return load(shortId, dateKey);
  }

  /**
   * Fetch one location's day into the sheet, keeping only the latest ask.
   *
   * @param {string} shortId Location short id.
   * @param {string} [dateKey] ISO date; omitted, the server answers for today.
   * @returns {Promise<boolean>} Whether the body was rendered.
   */
  async function load(shortId, dateKey) {
    var ask = guard.begin();
    var url = URL_TEMPLATE.replace('__SHORTID__', encodeURIComponent(String(shortId)));
    if (dateKey) url += '?date=' + encodeURIComponent(dateKey);

    try {
      var resp = await fetch(url, { headers: { Accept: 'application/json' }, signal: ask.signal });
      if (!resp.ok) throw new Error('weather detail ' + resp.status);
      var data = await resp.json();
      // A later tap won this race, or the sheet closed; either way this
      // answer owns nothing.
      if (!ask.isCurrent()) return false;
      // Server-trusted HTML: rendered by Django templates with autoescape
      // on. The same basis on which map.js calls popup.setHTML.
      sheetEl.innerHTML = data.html;
      return true;
    } catch (err) {
      if (!ask.isCurrent() || playback.isAbort(err)) return false;
      showMessage(strings.failed);
      return false;
    }
  }

  // SNOW-1195: reload the open sheet for the new day, as the region and
  // place sheets do. No loading line: the old day stays readable until the
  // new one lands, and a fast run of days keeps only the last response.
  // SNOW-1239: a timelapse frame during playback only records the day.
  document.addEventListener('snowdesk:date-changed', function (ev) {
    if (shownShortId == null || !sheet.isOpen()) return;
    var detail = ev.detail || {};
    shownDate = detail.date || null;
    if (!playback.shouldReload(detail.source, playback.isPlaying())) {
      pendingRefresh = true;
      guard.invalidate();
      playback.markPaused(sheetEl, true);
      return;
    }
    // A day picked by hand ends playback; it arrives before the stop, so
    // the stop that follows has nothing left to load.
    pendingRefresh = false;
    resume();
  });

  /**
   * Lift the band and load the shown location for the shown day. A sheet
   * opened during playback holds nothing but the band, so it says it is
   * loading rather than standing empty.
   *
   * @returns {Promise<boolean>} Whether the body was rendered.
   */
  function resume() {
    playback.markPaused(sheetEl, false);
    if (!sheetEl.querySelector('[data-sheet-peek]')) showMessage(strings.loading);
    return load(shownShortId, shownDate || undefined);
  }

  // SNOW-1239: playback starting pauses the open sheet; stopping lifts the
  // band and loads the day it stopped on, once.
  document.addEventListener('snowdesk:timelapse-state', function (ev) {
    if (shownShortId == null || !sheet.isOpen()) return;
    if (ev.detail && ev.detail.playing) {
      // Whatever was in flight is for a day playback is about to leave.
      guard.invalidate();
      pendingRefresh = true;
      playback.markPaused(sheetEl, true);
      return;
    }
    if (!pendingRefresh) {
      playback.markPaused(sheetEl, false);
      return;
    }
    pendingRefresh = false;
    resume();
  });

  window.pwaWeatherDetail = Object.freeze({
    open: open,
    /** @returns {?Array<number>} The open sheet's [lng, lat], or null. */
    point: function () { return sheet.isOpen() ? shownLngLat : null; },
    close: sheet.close,
    isOpen: sheet.isOpen,
  });
}());
