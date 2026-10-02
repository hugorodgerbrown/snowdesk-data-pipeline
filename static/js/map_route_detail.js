/**
 * static/js/map_route_detail.js — the docked panel behind rail one's
 * "Terrain" menu item (SNOW-973; SNOW-1018).
 *
 * SNOW-1018: a tap on a saved route opens rail one, not this sheet. The
 * rail took the name, the figures and the profile; this sheet opens from
 * the rail's menu, over the rail, and holds the terrain lines.
 *
 * SNOW-1062 removed the day's bulletin reading from this sheet, and with
 * it the fetch, the offline copy and the re-fetch on a date change. The
 * day's danger is a map layer (SNOW-979), drawn on the ground around a
 * route rather than judged along it, so nothing this sheet shows depends
 * on the date.
 *
 * A route's detail was an anchored MapLibre popup until SNOW-973, and
 * five tickets of terrain reading had been poured into a 320px card. It is
 * now the shared map sheet, with the map flown to the track above it —
 * `public/partials/_route_detail_sheet.html` has the whole argument.
 *
 * WHERE THE BOUNDARY IS. map.js owns the map, binds the tap and BUILDS the
 * body — the terrain lines are its, because they read map state (the
 * slope core, MAP_STRINGS) that this module has no business knowing; it
 * hands the rail a function that calls `open` here when the menu item is
 * pressed. This module owns the SHEET: it clones the body and seats what
 * map.js built in it.
 *
 * They meet at `window.pwaRouteDetail.open({ node })` and nowhere else,
 * which is `map_weather_detail.js`'s own boundary and is why this file has
 * no reference to `map`, a layer id or a feature.
 *
 * REBUILT ON EVERY OPEN. `MapSheet.attach`'s teardown does
 * `el.innerHTML = ''` on every close, so nothing here may assume the body
 * from last time — the `<template>` is cloned per open, the way routes.js
 * clones the panel's.
 */

(function routeDetailInit() {
  'use strict';

  var sheetEl = document.getElementById('route-detail-sheet');
  var bodyTemplate = document.getElementById('route-detail-template');
  if (!sheetEl || !bodyTemplate || !window.MapSheet) return;

  var sheet = window.MapSheet.attach(sheetEl, {});

  /**
   * Open the sheet for one route.
   *
   * @param {{node: HTMLElement}} detail `node` is the terrain-line DOM
   *   map.js built.
   * @returns {boolean} Whether the sheet was opened.
   */
  function open(detail) {
    if (!detail || !detail.node) return false;

    sheet.open();

    var body = /** @type {HTMLTemplateElement} */ (
      bodyTemplate
    ).content.cloneNode(true);
    sheetEl.replaceChildren(body);
    var figuresSlot = sheetEl.querySelector('[data-route-detail-figures]');
    if (figuresSlot) figuresSlot.replaceChildren(detail.node);
    return true;
  }

  window.pwaRouteDetail = Object.freeze({
    open: open,
    close: sheet.close,
    isOpen: sheet.isOpen,
  });
}());
