/**
 * static/js/map_route_detail.js — the docked panel behind a tap on a saved
 * route (SNOW-973).
 *
 * A route's detail was an anchored MapLibre popup until this ticket, and
 * five tickets of terrain reading had been poured into a 320px card. It is
 * now the shared map sheet, with the map flown to the track above it —
 * `public/partials/_route_detail_sheet.html` has the whole argument.
 *
 * WHERE THE BOUNDARY IS. map.js owns the map, binds the tap and BUILDS the
 * figures — the distance line, the terrain lines, the elevation profile and
 * the pending share's Save control are all its, unchanged from the popup,
 * because they read map state (the routes GeoJSON cache, the slope core,
 * MAP_STRINGS) that this module has no business knowing. This module owns
 * the SHEET: it clones the body, seats what map.js built in it, and fetches
 * the one thing the popup never had room for — what each region's bulletin
 * says about this line, on the day the map is showing.
 *
 * They meet at `window.pwaRouteDetail.open({ node, uuid, day })` and nowhere
 * else, which is `map_weather_detail.js`'s own boundary and is why this file
 * has no reference to `map`, a layer id or a feature.
 *
 * REBUILT ON EVERY OPEN. `MapSheet.attach`'s teardown does
 * `el.innerHTML = ''` on every close, so nothing here may assume the body
 * from last time — the `<template>` is cloned per open, the way routes.js
 * clones the panel's.
 *
 * The bulletin half is fetched rather than swapped over htmx for one
 * reason: the response's freshness headers. A bulletin reading expires, and
 * `routes_bulletin_offline.js` stores each one under the
 * `X-Data-Generated-At` / `X-Data-Unsafe-After` pair the `Response` carries,
 * so a reading held past its horizon is reported expired instead of
 * repainted as current. An htmx swap would need a server-rendered sidecar
 * to carry the same pair.
 *
 * THE DAY IS THE MAP'S, not today. A trip is planned for a date; a route is
 * not, so the day comes from the surface asking — map.js passes whatever
 * the scrubber is showing, and it is part of both the request and the cache
 * key, which is what stops a cached reading being painted under a date it
 * does not belong to.
 */

(function routeDetailInit() {
  'use strict';

  var mapEl = document.getElementById('map');
  var sheetEl = document.getElementById('route-detail-sheet');
  var bodyTemplate = document.getElementById('route-detail-template');
  if (!mapEl || !sheetEl || !bodyTemplate || !window.MapSheet) return;

  // Rendered via {% url 'routes:bulletin' uuid='__UUID__' %}; the
  // placeholder is substituted per tap, matching map.js's resort-popup and
  // map_weather_detail.js's weather handling.
  var URL_TEMPLATE = mapEl.dataset.routeBulletinUrlTemplate || '';

  // `read` takes the whole fallback map and returns the whole map back,
  // each key overridden by the template's translation when it has one — it
  // is not a per-key getter. The English literals stay here as the fallback
  // for a page that somehow renders without the template.
  var FALLBACKS = {
    loading: 'Loading this day’s bulletin…',
    failed: 'This day’s bulletin couldn’t be loaded — check your connection.',
    'cached-as-of': 'Showing a saved reading — as of %(time)s.',
    expired:
      'This saved reading has expired — reconnect to read this day’s bulletin.',
  };
  var strings = window.pwaStrings
    ? window.pwaStrings.read('route-detail-strings-template', FALLBACKS)
    : FALLBACKS;

  var sheet = window.MapSheet.attach(sheetEl, {});

  // Bumped on every open. A tap on a second route while the first reading
  // is still in flight must not have the first response land in the sheet
  // on top of it — routes cross on the map, and a mis-tap followed by a
  // correction is the ordinary case rather than an edge one.
  var requestToken = 0;

  /** @type {HTMLElement|null} */
  var figuresSlot = null;
  /** @type {HTMLElement|null} */
  var bulletinSlot = null;

  /**
   * Format an ISO timestamp as "HH:MM" (24h, zero-padded).
   *
   * The same helper, with the same output, as favourites_offline.js's
   * `_formatHHMM` and routes.js's `formatHHMM` — a cached surface says when
   * it was cached in one format across the app. Returns '' on a value that
   * will not parse.
   *
   * @param {string|null} isoValue
   * @returns {string}
   */
  function formatHHMM(isoValue) {
    if (!isoValue) return '';
    var d = new Date(isoValue);
    if (Number.isNaN(d.valueOf())) return '';
    var pad = function (n) { return String(n).padStart(2, '0'); };
    return pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  /**
   * Replace the bulletin slot with a single line of text.
   *
   * `textContent`, not `innerHTML`: these strings come from a template the
   * page rendered, but nothing here needs markup and the narrower call
   * cannot grow into an injection later.
   *
   * @param {string} message Text to show.
   * @param {string} testid Value for the line's data-testid.
   */
  function showLine(message, testid) {
    if (!bulletinSlot) return;
    var line = document.createElement('p');
    line.className = 'mt-4 text-meta text-text-3';
    line.setAttribute('data-testid', testid);
    line.textContent = message;
    bulletinSlot.replaceChildren(line);
  }

  /**
   * Paint a cached reading, behind an explicit line saying it is one.
   *
   * The "as of" line is unconditional, exactly as it is on the offline
   * favourite card: a reader looking at a reading of avalanche terrain is
   * entitled to know it came off this device rather than off the server,
   * whatever its age.
   *
   * @param {object} record A row from routes_bulletin_offline.read.
   */
  function paintCached(record) {
    if (!bulletinSlot) return;
    // Server-rendered HTML, stored verbatim — the same bytes and the same
    // basis on which it was injected when it was fetched.
    bulletinSlot.innerHTML = record.body;
    var asOf = document.createElement('p');
    asOf.className = 'mt-2 font-mono text-meta text-text-3';
    asOf.setAttribute('data-testid', 'route-bulletin-cached-as-of');
    asOf.textContent = window.pwaStrings
      ? window.pwaStrings.interpolate(strings['cached-as-of'], {
        time: formatHHMM(record.cached_at),
      })
      : strings['cached-as-of'];
    bulletinSlot.appendChild(asOf);
  }

  /**
   * Fall back to whatever this device holds for this route and day.
   *
   * Three outcomes, and they are three different statements: a readable
   * row within its horizon is painted behind the "as of" line; a row past
   * it is REPLACED by the expired sentence, never shown as a current
   * reading; and nothing at all is the ordinary failure line.
   *
   * @param {string} key From routes_bulletin_offline.keyFor.
   * @returns {Promise<void>}
   */
  async function paintFromCache(key) {
    var offline = window.pwaRoutesBulletinOffline;
    var record = offline ? await offline.read(key) : null;
    if (!record) {
      showLine(strings.failed, 'route-bulletin-failed');
      return;
    }
    if (offline.isExpired(record)) {
      showLine(strings.expired, 'route-bulletin-expired');
      return;
    }
    paintCached(record);
  }

  /**
   * Fetch and seat this route's reading of the day's bulletin.
   *
   * @param {string} uuid The route's uuid.
   * @param {string} day The ISO date the map is showing, or ''.
   * @param {number} token This open's race token.
   * @returns {Promise<void>}
   */
  async function loadBulletin(uuid, day, token) {
    var offline = window.pwaRoutesBulletinOffline;
    // The key is the day the map is showing; with no day known it is this
    // device's own today, which is what a map with no scrubber is showing.
    var dayKey = day || new Date().toISOString().slice(0, 10);
    var key = offline ? offline.keyFor(uuid, dayKey) : null;

    showLine(strings.loading, 'route-bulletin-loading');

    var url = URL_TEMPLATE.replace('__UUID__', encodeURIComponent(uuid));
    if (day) url += '?d=' + encodeURIComponent(day);

    try {
      var resp = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!resp.ok) throw new Error('route bulletin ' + resp.status);
      var data = await resp.json();
      if (typeof data.html !== 'string') throw new Error('route bulletin body');
      // A later tap won this race; its own response owns the sheet.
      if (token !== requestToken) return;
      if (!bulletinSlot) return;
      // Server-trusted HTML: rendered by Django templates with autoescape
      // on. The same basis on which map.js calls popup.setHTML.
      bulletinSlot.innerHTML = data.html;
      if (offline) {
        var unsafeAfter = Number(resp.headers.get('X-Data-Unsafe-After'));
        // Keyed by the day the SERVER answered for, which is the day this
        // body is actually about — with no `?d=` it picked its own today,
        // and storing it under the device's guess would file a reading
        // under a date it does not belong to.
        offline
          .write(
            offline.keyFor(uuid, data.day || dayKey),
            data.html,
            resp.headers.get('X-Data-Generated-At'),
            Number.isFinite(unsafeAfter) && unsafeAfter > 0 ? unsafeAfter : null,
          )
          .catch(function () {
            // Swallowed — the cache is best effort.
          });
      }
    } catch (_err) {
      if (token !== requestToken) return;
      if (key) {
        await paintFromCache(key);
      } else {
        showLine(strings.failed, 'route-bulletin-failed');
      }
    }
  }

  /**
   * Open the sheet for one route.
   *
   * Opens IMMEDIATELY, with the figures map.js already built and a loading
   * line where the bulletin will be. A tap that shows nothing until the
   * network answers reads as a tap that missed, and the second tap then
   * closes what the first opened.
   *
   * @param {{node: HTMLElement, uuid?: string|null, day?: string|null}} detail
   *   `node` is the figure/profile DOM map.js built; `uuid` is the route's,
   *   absent for a pending share, whose reading is owner-scoped and is not
   *   asked for; `day` is the ISO date the map is showing.
   * @returns {boolean} Whether the sheet was opened.
   */
  function open(detail) {
    if (!detail || !detail.node) return false;

    var token = ++requestToken;
    sheet.open();

    var body = /** @type {HTMLTemplateElement} */ (
      bodyTemplate
    ).content.cloneNode(true);
    sheetEl.replaceChildren(body);
    figuresSlot = sheetEl.querySelector('[data-route-detail-figures]');
    bulletinSlot = sheetEl.querySelector('[data-route-detail-bulletin]');
    if (figuresSlot) figuresSlot.replaceChildren(detail.node);

    if (detail.uuid && URL_TEMPLATE) {
      loadBulletin(String(detail.uuid), detail.day || '', token).catch(
        function () {
          // loadBulletin handles its own failures; this is the guard
          // against an unhandled rejection escaping the open.
        },
      );
    }
    return true;
  }

  window.pwaRouteDetail = Object.freeze({
    open: open,
    close: sheet.close,
    isOpen: sheet.isOpen,
  });
}());
