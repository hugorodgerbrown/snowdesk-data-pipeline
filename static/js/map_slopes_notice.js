/*
 * static/js/map_slopes_notice.js — "Now showing the bulletin's slopes"
 * (SNOW-1378).
 *
 * The DOM half of map_slopes_notice_core.js. Since SNOW-1377 the map
 * paints the slopes the day's bulletin describes from z12, in Switzerland,
 * and each painted region's fill drops out, so the surface changes by
 * itself as a reader zooms in. The first time a device's map settles
 * there, #map-slopes-notice says so and names the marks; it gives no
 * advice (Hugo, 2 Nov 2026).
 *
 * Behaviour:
 *   - On every MapLibre 'moveend' until it has been shown, it reads the
 *     zoom, `window.pwaBulletinTerrain.isEnabled()` (false while Steep
 *     terrain is on, which takes precedence), Steep terrain itself, the
 *     country of the region at the map's centre, and whether anything else
 *     holds the screen — an object sheet (the phone root's
 *     `data-map-sheet`, a desktop left-column sheet, the route rail), any
 *     other registered overlay, or a tour or walkthrough — and asks the
 *     core. A yes has already marked the device seen.
 *   - The key is the region sheet's own: the rows of
 *     includes/_bulletin_terrain_key.html for the region at the centre on
 *     the map's day, fetched from public:slopes_notice_key_partial. The
 *     card renders with the whole-scale key the legend shows, and keeps it
 *     when the fetch fails, times out or answers empty. The card appears
 *     once the fetch has settled, so its rows never change under the
 *     reader.
 *   - "Got it", Escape and the next tap on the map close it. "What's
 *     shown?" is a link to the help topic.
 *   - A flyout/card (docs/map-surface.md): it registers with
 *     window.pwaMapOverlays, so another overlay opening closes it.
 *   - prefers-reduced-motion: the card appears without its slide-in.
 *
 * A classic script loaded after map.js: it reads the shared `MAP` and the
 * bridges map.js publishes. map.css withdraws it while a route is open or
 * drawn.
 */

(function slopesNoticeInit() {
  'use strict';

  const notice = document.getElementById('map-slopes-notice');
  const core = self.pwaSlopesNoticeCore;
  if (!notice || !core) return;

  // This surface's name in the shared map-overlay registry: its own id.
  const OVERLAY_NAME = 'map-slopes-notice';
  // How long the key may take before the card shows the whole scale.
  const KEY_WAIT_MS = 2500;
  // The tours and walkthroughs, any of which on screen means one is running.
  const TOUR_IDS = [
    'map-help-overlay',
    'map-example-guide',
    'map-example-pill',
    'map-example-finish',
    'map-resort-guide-bar',
    'map-resort-guide-pill',
    'map-resort-guide-finish',
  ];

  const okBtn = document.getElementById('map-slopes-notice-ok');
  const keyEl = notice.querySelector('[data-slopes-notice-key]');
  const URL_TEMPLATE = notice.dataset.keyUrl || '';
  const fallbackKey = keyEl ? keyEl.innerHTML : '';

  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    notice.classList.add('map-tour-offer--no-motion');
  }

  /**
   * The device's storage, or null where even reaching it throws (some
   * browsers' private modes); the core reads null as already seen.
   *
   * @returns {?Storage}
   */
  const storage = () => {
    try {
      return window.localStorage;
    } catch (_) {
      return null;
    }
  };

  /** @type {?AbortController} */
  let pending = null;
  /**
   * Set once the map is wired: listen for the next qualifying move again.
   *
   * @type {() => void}
   */
  let listenAgain = () => {};

  const isOpen = () => !notice.hidden;

  /**
   * Close the card. A showing still waiting on its key is dropped, and
   * since it never reached the screen the device is given its turn back:
   * the seen record is taken back and the next qualifying move tries again.
   */
  const hide = () => {
    if (pending) {
      pending.abort();
      pending = null;
      if (notice.hidden) {
        core.unmarkSeen(storage());
        listenAgain();
      }
    }
    notice.hidden = true;
  };

  if (okBtn) okBtn.addEventListener('click', hide);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && (isOpen() || pending)) hide();
  });

  window.pwaMapOverlays?.register(OVERLAY_NAME, { isOpen, close: hide });

  // typeof, not !MAP: a bare read of an undeclared MAP would throw rather
  // than return, on any page that loads this without the map bundle.
  if (typeof MAP === 'undefined' || !MAP || !URL_TEMPLATE) return;

  const scrubber = document.getElementById('season-scrubber');
  /** @type {?string} */
  let dateKey = (scrubber && scrubber.dataset.date) || null;
  document.addEventListener('snowdesk:date-changed', (ev) => {
    dateKey = (ev.detail && ev.detail.date) || null;
  });

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

  /** Whether an object sheet is open, at any width. */
  const sheetOpen = () => {
    if (document.documentElement.hasAttribute('data-map-sheet')) return true;
    if (document.querySelector("[data-sheet-side='left']:not([hidden])")) return true;
    const mapEl = document.getElementById('map');
    return Boolean(mapEl && mapEl.hasAttribute('data-route-rail-open'));
  };

  /** Whether any other registered overlay is open. */
  const overlayOpen = () => {
    const open = window.pwaMapOverlays?.openNames?.() || [];
    return open.some((name) => name !== OVERLAY_NAME);
  };

  /** Whether a tour, help mode or a walkthrough is running. */
  const tourRunning = () => {
    if (document.documentElement.hasAttribute('data-map-help-mode')) return true;
    return TOUR_IDS.some((id) => {
      const el = document.getElementById(id);
      return Boolean(el && !el.hidden);
    });
  };

  /**
   * Fetch the region's key and put it in the card, keeping the whole
   * scale when it fails, times out or answers empty.
   *
   * @param {string} regionId
   * @param {AbortController} ask
   * @returns {Promise<void>}
   */
  const loadKey = (regionId, ask) => {
    let url = URL_TEMPLATE.replace('XX-0000', encodeURIComponent(regionId.toLowerCase()));
    if (dateKey) url += '?' + new URLSearchParams({ d: dateKey }).toString();
    const timer = setTimeout(() => ask.abort(), KEY_WAIT_MS);
    return fetch(url, { headers: { 'HX-Request': 'true' }, signal: ask.signal })
      .then((resp) => (resp.ok ? resp.text() : ''))
      .catch(() => '')
      .then((html) => {
        clearTimeout(timer);
        if (!keyEl) return;
        // Server-rendered with autoescaping on, the region sheet's own
        // grounds for injecting its body.
        keyEl.innerHTML = html.trim() ? html : fallbackKey;
      });
  };

  /**
   * Show the card for a region once its key has settled, unless the
   * reader closed it, tapped the map or opened another overlay meanwhile.
   *
   * @param {string} regionId
   */
  /**
   * What the core decides on: the map's state now, and the region at the
   * centre.
   *
   * @returns {{input: Object, regionId: ?string}}
   */
  const readMap = () => {
    const feature = regionAtCentre();
    const props = (feature && feature.properties) || {};
    return {
      regionId: props.id ? String(props.id) : null,
      input: {
        zoom: MAP.getZoom(),
        slopesOn: !!(window.pwaBulletinTerrain && window.pwaBulletinTerrain.isEnabled()),
        filterOn: !!(window.pwaTerrainFilter && window.pwaTerrainFilter.isEnabled()),
        country: props.country || null,
        sheetOpen: sheetOpen(),
        overlayOpen: overlayOpen(),
        tourRunning: tourRunning(),
      },
    };
  };

  /**
   * Show the card for a region once its key has settled. The map is read
   * again first: if the reader opened another overlay or a sheet, zoomed
   * out or switched the slopes off while the key loaded, the card stays
   * hidden rather than closing what they opened, and the device is given
   * its turn back for a later move.
   *
   * @param {string} regionId
   */
  const show = (regionId) => {
    const ask = new AbortController();
    pending = ask;
    loadKey(regionId, ask).then(() => {
      // hide() cleared it: dismissed before it ever appeared.
      if (pending !== ask) return;
      pending = null;
      if (!core.eligible(readMap().input)) {
        core.unmarkSeen(storage());
        listenAgain();
        return;
      }
      window.pwaMapOverlays?.opening(OVERLAY_NAME);
      notice.hidden = false;
    });
  };

  /** After a camera move: show the card if the core says this is the moment. */
  const consider = () => {
    const { input, regionId } = readMap();
    if (!regionId || !core.claim(input, storage())) return;
    MAP.off('moveend', consider);
    show(regionId);
  };

  listenAgain = () => {
    MAP.off('moveend', consider);
    MAP.on('moveend', consider);
  };
  // Once seen, nothing here needs the map again but the tap that closes.
  if (!core.hasBeenSeen(storage())) MAP.on('moveend', consider);
  MAP.on('click', () => {
    if (isOpen() || pending) hide();
  });
})();
