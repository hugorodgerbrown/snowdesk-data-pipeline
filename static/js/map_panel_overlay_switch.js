/*
 * static/js/map_panel_overlay_switch.js — the "Show on the map" switch in
 * each map panel, bound to the same state as that layer's layers-menu row.
 *
 * Four panels each own one layer: favourites, field observations, routes
 * and downloads. Each panel's foot carries a switch for its layer
 * (templates/includes/_map_overlay_toggle.html, rendered by
 * includes/_ugc_panel.html's `toggle_id`), and the layers menu carries a row
 * for the same layer. They are two controls over ONE setting, so:
 *
 *   - flipping a switch flips the row — by CLICKING the row, so the switch
 *     inherits everything the row does: the sign-in hand-off for a signed-out
 *     visitor on favourites and routes, the offline-disabled guard, the
 *     header count and section summary, and the route through the
 *     window.pwa*Overlay bridge (telemetry, the favourited-resort recompute,
 *     SNOW-857's never-write-on-seed rule for downloads). A second copy of
 *     that logic here would be free to drift from the menu's;
 *   - every switch on the page is re-seeded from its bridge's
 *     ``isEnabled()`` whenever any overlay moves
 *     (``snowdesk:overlay-visibility-changed``, which every bridge show/hide
 *     announces, downloads included), so flipping the row moves the switch.
 *
 * ``isEnabled()``, not ``isVisible()``: the switch states what the reader
 * asked for, as the menu row does and as SNOW-658's switch did. Offline with
 * nothing cached the two disagree, and the switch reading ON while nothing is
 * drawn is the honest answer — the request took and restores next boot.
 *
 * WHY THIS DOES NOT REINTRODUCE SNOW-904's PROBLEM. SNOW-904 removed these
 * switches because, with them as the ONLY control, a reader asking "what is
 * on my map" had to open five surfaces. The layers menu still answers that
 * in one place; a panel's switch answers it for the panel's own layer only.
 * See docs/decisions/map-controls-are-two-groups-without-an-on-map-marker.md.
 *
 * Every panel body is re-cloned from its <template> on every open, so the
 * change listener is DELEGATED on the document (it survives the clone) and
 * the owning module calls ``window.pwaPanelOverlaySwitch.sync()`` after each
 * clone to seed the new switch — a cloned checkbox starts unchecked.
 *
 * LOAD ORDER: reads the bridges lazily (map.js publishes them at parse
 * time) and the layers-menu rows on demand, so it has no ordering
 * constraint beyond running before the first panel opens.
 */

(function mapPanelOverlaySwitchInit() {
  'use strict';

  /*
   * Each switch id, the layers-menu ``data-overlay-key`` it is bound to, and
   * the bridge that owns that layer. Written out one line each rather than
   * resolved from a ``window[name]`` lookup: a literal
   * ``window.pwaFavouritesOverlay`` is what bin/js-globals-lint can check
   * against the tree's assignments.
   */
  const SWITCHES = [
    {
      id: 'map-favourites-overlay-toggle',
      key: 'favourites',
      bridge: () => window.pwaFavouritesOverlay,
    },
    {
      id: 'map-community-reports-overlay-toggle',
      key: 'community_reports',
      bridge: () => window.pwaCommunityReportsOverlay,
    },
    {
      id: 'map-routes-overlay-toggle',
      key: 'routes',
      bridge: () => window.pwaRoutesOverlay,
    },
    {
      id: 'map-downloads-overlay-toggle',
      key: 'downloads',
      bridge: () => window.pwaDownloadedOverlay,
    },
  ];

  /**
   * The layers-menu row for one overlay key, or null on a page without one.
   *
   * @param {string} key - A ``data-overlay-key`` value.
   * @returns {HTMLElement|null}
   */
  function rowFor(key) {
    return document.querySelector(`#basemap-menu [data-overlay-key="${key}"]`);
  }

  /**
   * Set every switch on the page from its bridge's ``isEnabled()``.
   *
   * A switch whose bridge is not published yet is left alone rather than
   * forced off — "not known" is not "off".
   *
   * @returns {void}
   */
  function sync() {
    for (const entry of SWITCHES) {
      const input = /** @type {HTMLInputElement|null} */ (
        document.getElementById(entry.id)
      );
      if (!input) continue;
      const bridge = entry.bridge();
      if (!bridge || typeof bridge.isEnabled !== 'function') continue;
      input.checked = !!bridge.isEnabled();
    }
  }

  /**
   * Carry one switch's new position to its layer.
   *
   * Through the menu row when there is one and it disagrees with the switch
   * — a real click, so the row's own handler decides what happens (toggle,
   * sign-in hand-off, or nothing for an offline-disabled row). Straight to
   * the bridge when the page has no such row, or the row already reads what
   * the switch asks for (it can only lag if something skipped the
   * announcement; the bridge is the state either way). Then every switch is
   * re-seeded from the bridges, which is what puts a switch back when the
   * row declined the change.
   *
   * @param {{id: string, key: string, bridge: function(): *}} entry
   * @param {boolean} wanted - The switch's new ``checked``.
   * @returns {void}
   */
  function apply(entry, wanted) {
    const row = rowFor(entry.key);
    const rowOn = row ? row.getAttribute('aria-checked') === 'true' : null;
    if (row && rowOn !== wanted) {
      row.click();
    } else {
      const bridge = entry.bridge();
      if (bridge) {
        if (wanted) bridge.show();
        else bridge.hide();
      }
    }
    sync();
  }

  document.addEventListener('change', (event) => {
    const target = /** @type {HTMLInputElement|null} */ (event.target);
    if (!target || !target.id) return;
    const entry = SWITCHES.find((candidate) => candidate.id === target.id);
    if (!entry) return;
    apply(entry, target.checked);
  });

  // Every bridge's show()/hide() announces this, including the downloads
  // bridge (its own ``snowdesk:downloaded-overlay-changed`` is followed by
  // the same announcement), so a row click, a placement flow, a deep link or
  // a connectivity flip all reach an open panel's switch.
  document.addEventListener('snowdesk:overlay-visibility-changed', sync);

  // Frozen bridge, the idiom every other cross-IIFE export here uses. The
  // panel modules call sync() after cloning their body, so a freshly opened
  // panel shows the setting rather than an unchecked default.
  window.pwaPanelOverlaySwitch = Object.freeze({ sync: sync });
})();
