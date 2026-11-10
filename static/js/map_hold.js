/*
 * static/js/map_hold.js — hold on the map to add a pin or report there
 * (SNOW-1485).
 *
 * A finger held still on the map for half a second (static/js/
 * map_hold_core.js decides that) drops the shared place pin under it; on a
 * desktop a right-click does the same. The map slides that spot under the
 * pin, clear of a small bar (#map-hold-bar, includes/_map_hold_bar.html):
 * "Move the map to fine-tune.", ×, Save pin and Report. The reader pans to
 * fine-tune, exactly as when placing from a panel's +, because the placing
 * IS window.PlacePicker's (static/js/place_picker.js).
 *
 * A hold, in order:
 *   1. the held screen point becomes lng/lat (``map.unproject``);
 *   2. the camera is snapshotted (centre, zoom, bearing, pitch);
 *   3. the bar opens (a MapSheet, so it takes part in Escape, outside
 *      dismissal and window.pwaMapOverlays' one-overlay-at-a-time), before
 *      the picker arms — the sheets it closes deactivate the picker in their
 *      own teardown, and must do that before it is armed, not after;
 *   4. ``PlacePicker.activate({ recenterTo, occludedBy: bar, onChange })``
 *      puts the point under the pin and clears the map down to the basemap
 *      (PlacementFocus).
 *
 * ONE SESSION, ONE TEARDOWN. ``session`` is null when idle, and every way
 * out goes through ``end(reason)``, which runs once — a second call finds no
 * session and does nothing:
 *   - × and Escape, outside dismissal, and another overlay opening over the
 *     bar all close it through MapSheet, whose teardown deactivates the
 *     picker (layers and padding back) and calls ``end('cancel')``, which
 *     then ``jumpTo``s the snapshot: the map is put back as it was;
 *   - Save pin and Report read the picker's last coordinate, run
 *     ``end('handoff')`` — the same close, with the camera left where the
 *     reader fine-tuned it — and then call
 *     ``window.pwaFavouritesSheet.createAt`` or
 *     ``window.pwaReportSheet.reportAt`` (lat, lon). The receiving sheet
 *     arms its own placement, or none, as it does today, and its gate
 *     answers a reader who cannot save or report.
 *
 * A HOLD WORKS ANYWHERE, a resort, a saved pin or a report included (Hugo,
 * 10 Nov 2026). The click a lifted finger sends after a hold is swallowed
 * once, by a capture-phase listener on the map container, so the object
 * under the finger does not open its sheet over the bar; a tap without a
 * hold reaches the map as before. A hold while the picker is already armed
 * (a pin being placed from a panel) is ignored.
 *
 * Loaded from static/js/bundles/map_surfaces.txt after place_picker.js and
 * map_sheet.js, before favourites.js and report.js. The map does not exist
 * when this runs (the map bundle loads after the surfaces), so it binds once
 * window.snowdeskMapState has one.
 */

(function () {
  'use strict';

  const core = window.pwaMapHoldCore;
  const bar = document.getElementById('map-hold-bar');
  const template = /** @type {HTMLTemplateElement|null} */ (
    document.getElementById('map-hold-bar-template')
  );
  if (!core || !bar || !template || !window.MapSheet) return;

  /**
   * @typedef {{
   *   camera: { center: [number, number], zoom: number, bearing: number, pitch: number },
   *   lat: number|null,
   *   lon: number|null,
   * }} HoldSession
   */

  /** @type {HoldSession|null} The hold in progress, or null when idle. */
  let session = null;

  /** @type {any} The map, once bound. */
  let boundMap = null;

  const controller = window.MapSheet.attach(bar, {
    // Every close of the bar (×, Escape, a click outside it, another
    // overlay opening) runs this, after MapSheet has deactivated the picker.
    onTeardown: function () {
      end('cancel');
    },
  });

  /** @returns {any} The map, or null until map.js has made one. */
  function getMap() {
    return (window.snowdeskMapState && window.snowdeskMapState.map) || null;
  }

  /**
   * Leave the hold, once. ``cancel`` puts the camera back where it was
   * before the hold; ``handoff`` leaves it where the reader fine-tuned it.
   *
   * @param {'cancel'|'handoff'} reason
   * @returns {void}
   */
  function end(reason) {
    const ending = session;
    if (!ending) return;
    session = null;
    // Closing runs MapSheet's teardown: the picker is deactivated there
    // (layers and padding back) before the camera moves below, and its call
    // back into end() finds no session.
    if (controller.isOpen()) controller.close();
    if (reason === 'cancel' && boundMap) boundMap.jumpTo(ending.camera);
  }

  /**
   * A hold happened at ``point`` (container pixels): drop the pin there.
   *
   * @param {{ x: number, y: number }} point
   * @returns {void}
   */
  function startHold(point) {
    const map = boundMap;
    if (!map || session) return;
    if (window.PlacePicker && window.PlacePicker.isActive()) return;

    const at = map.unproject([point.x, point.y]);
    const centre = map.getCenter();
    session = {
      camera: {
        center: [centre.lng, centre.lat],
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
      },
      lat: at.lat,
      lon: at.lng,
    };
    const current = session;

    bar.replaceChildren(template.content.cloneNode(true));
    controller.open();

    window.PlacePicker?.activate({
      recenterTo: [at.lng, at.lat],
      occludedBy: bar,
      onChange: function (lat, lon) {
        current.lat = lat;
        current.lon = lon;
      },
    });
  }

  // Save pin and Report. stopPropagation, because the bar's body is gone
  // (MapSheet empties it on close) by the time a document listener would
  // see this click: the receiving sheet's own click-outside check would
  // find the button outside it and close the sheet it has just opened.
  bar.addEventListener('click', function (event) {
    const target = /** @type {HTMLElement} */ (event.target);
    const button = target && target.closest && target.closest('[data-hold-action]');
    if (!button || !session) return;
    event.stopPropagation();
    const action = button.getAttribute('data-hold-action');
    const lat = session.lat;
    const lon = session.lon;
    end('handoff');
    if (lat == null || lon == null) return;
    if (action === 'save-pin') window.pwaFavouritesSheet?.createAt(lat, lon);
    else if (action === 'report') window.pwaReportSheet?.reportAt(lat, lon);
  });

  const detector = core.createDetector({ onHold: startHold });

  /**
   * The touch points of a MapLibre touch event, in container pixels.
   *
   * @param {any} event
   * @returns {Array<{ x: number, y: number }>}
   */
  function pointsOf(event) {
    return (event && event.points) || [];
  }

  /**
   * How many fingers are still down once this touch event has run.
   *
   * @param {any} event - A MapLibre touch event.
   * @returns {number}
   */
  function remainingTouches(event) {
    const touches = event && event.originalEvent && event.originalEvent.touches;
    return touches ? touches.length : 0;
  }

  /**
   * Wire the map's touches, gestures and right-click to the detector, once
   * the map exists.
   *
   * @returns {void}
   */
  function bindMap() {
    const map = getMap();
    if (!map) {
      window.setTimeout(bindMap, 200);
      return;
    }
    boundMap = map;
    map.on('touchstart', function (event) {
      detector.touchStart(pointsOf(event));
    });
    map.on('touchmove', function (event) {
      detector.touchMove(pointsOf(event));
    });
    map.on('touchend', function (event) {
      detector.touchEnd(remainingTouches(event));
    });
    map.on('touchcancel', function (event) {
      detector.touchEnd(remainingTouches(event));
    });
    map.on('dragstart', function () {
      detector.cancel();
    });
    map.on('zoomstart', function () {
      detector.cancel();
    });
    // MapLibre already drops the contextmenu that ends a right-drag rotate,
    // and prevents the browser's own menu while anything listens.
    map.on('contextmenu', function (event) {
      if (event && event.originalEvent) event.originalEvent.preventDefault();
      detector.contextMenu(event.point);
    });
    // The click that follows a touch hold, swallowed before MapLibre (on the
    // canvas container, inside this element) can turn it into a tap on
    // whatever object was under the finger.
    map.getContainer().addEventListener(
      'click',
      function (event) {
        if (!detector.takeClick()) return;
        event.stopPropagation();
        event.preventDefault();
      },
      true,
    );
  }
  bindMap();
}());
