// @ts-check
/*
 * static/js/saved_panel.js — Saved, the one panel for the reader's places,
 * routes, trips and observations, on every screen (SNOW-1481).
 *
 * Saved is four existing sheets — the favourites, routes, trips and report
 * sheets (window.pwaFavouritesSheet / pwaRoutesSheet / pwaTripsSheet /
 * pwaReportSheet), one at a time — switched by the tabs each renders in
 * its header (includes/_saved_tabs.html). SNOW-1479 built it for the
 * installed phone app's Saved tab, inside static/js/app_tabbar.js; this
 * module is that logic moved out, so the star roundel (#favourite-add-btn)
 * opens the same panel on a desktop and in a phone browser. The tab bar
 * calls it for its Saved tab.
 *
 *   - THE STAR TOGGLES SAVED. Shut, it opens on the tab last chosen
 *     (sessionStorage, for the session), Places at first; open, it closes
 *     whichever sheet is showing. `aria-expanded` on the star is true while
 *     Saved is open, and map.css draws the filled star then.
 *   - A TAB SWITCHES IN PLACE. Choosing another Saved tab (`saved-tab`
 *     radios) closes one sheet as it opens its sibling in the same task, so
 *     only the content changes; `data-saved-go` does the same from a
 *     control (the Trips picker's no-routes item goes to Routes).
 *   - A FIRST OPEN RISES. The sheet is marked `data-tab-open` for the
 *     length of map.css's rise animation; a tab switch is not marked.
 *   - SIGNED OUT, Saved opens Places, whose sheet invites a visitor to sign
 *     in; the trips sheet is not rendered for them. Signed-in state is the
 *     star's `data-favourites-eligible`.
 *   - THE BINOCULARS' OWN FORM IS NOT SAVED. The binoculars roundel starts
 *     a report in the report sheet (report.js), marking it
 *     `data-report-direct`; that sheet is not Saved › Observations until
 *     Saved opens it, so the star stays outline over it.
 *   - The four sheets' `hidden` attribute is watched, so however a sheet
 *     opens or closes (its ×, Escape, a row handing off to the map) the
 *     star follows, and `snowdesk:saved-panel-change` tells the tab bar
 *     (on every change, a sheet opened and shut in one task included).
 *
 * Loads after the four sheet controllers and before app_tabbar.js
 * (static/js/bundles/map_surfaces.txt). Every controller is read when it
 * is used, so a sheet the page does not render is simply not offered.
 */
(function savedPanelInit() {
  'use strict';

  /** @typedef {{ open(): void, close(): void, isOpen(): boolean }} Panel */
  /** @typedef {'places'|'routes'|'trips'|'observations'} SavedChoice */

  var SAVED_KEY = 'snowdesk:app-tab-saved';
  var RISE_MS = 200;
  var CHANGE_EVENT = 'snowdesk:saved-panel-change';
  var DIRECT_ATTR = 'data-report-direct';

  /** @type {Record<SavedChoice, string>} */
  var SAVED_SHEET_IDS = {
    places: 'favourite-sheet',
    routes: 'route-sheet',
    trips: 'trips-sheet',
    observations: 'report-sheet',
  };
  var CHOICES = /** @type {Array<SavedChoice>} */ (['places', 'routes', 'trips', 'observations']);

  var sheets = CHOICES.map(function (choice) {
    return document.getElementById(SAVED_SHEET_IDS[choice]);
  }).filter(Boolean);
  if (!sheets.length) return;

  var star = document.getElementById('favourite-add-btn');

  /** @returns {boolean} The reader is signed in, read off the star. */
  function signedIn() {
    return !!star && star.getAttribute('data-favourites-eligible') === 'true';
  }

  /**
   * The controller behind one Saved choice, or null where the map does not
   * render that surface.
   *
   * @param {SavedChoice} choice
   * @returns {Panel|null}
   */
  function controllerFor(choice) {
    if (choice === 'routes') return window.pwaRoutesSheet || null;
    if (choice === 'trips') return window.pwaTripsSheet || null;
    if (choice === 'observations') return window.pwaReportSheet || null;
    return window.pwaFavouritesSheet || null;
  }

  /**
   * True when `choice`'s sheet is open as part of Saved: the report sheet
   * holding the binoculars' own form is not.
   *
   * @param {SavedChoice} choice
   * @returns {boolean}
   */
  function showing(choice) {
    var controller = controllerFor(choice);
    if (!controller || !controller.isOpen()) return false;
    var el = document.getElementById(SAVED_SHEET_IDS[choice]);
    return !(el && el.hasAttribute(DIRECT_ATTR));
  }

  /** @returns {SavedChoice|null} The Saved tab showing, or null. */
  function current() {
    for (var i = 0; i < CHOICES.length; i++) {
      if (showing(CHOICES[i])) return CHOICES[i];
    }
    return null;
  }

  /** @returns {boolean} Whether Saved is open. */
  function isOpen() {
    return current() !== null;
  }

  /** @returns {SavedChoice} The tab Saved opens on. */
  function remembered() {
    if (!signedIn()) return 'places';
    var stored = null;
    try {
      stored = window.sessionStorage.getItem(SAVED_KEY);
    } catch (_err) {
      // Storage refused (private window): places, every time.
    }
    if (stored === 'routes' || stored === 'trips' || stored === 'observations') {
      if (controllerFor(stored)) return stored;
    }
    return 'places';
  }

  /** @param {SavedChoice} choice */
  function remember(choice) {
    try {
      window.sessionStorage.setItem(SAVED_KEY, choice);
    } catch (_err) {
      // Not remembered; harmless.
    }
  }

  /** Open Saved on the remembered tab, rising in. Open already: nothing. */
  function open() {
    if (isOpen()) return;
    var choice = remembered();
    var controller = controllerFor(choice);
    if (!controller) return;
    const el = document.getElementById(SAVED_SHEET_IDS[choice]);
    if (el) {
      el.setAttribute('data-tab-open', '');
      window.setTimeout(function () {
        el.removeAttribute('data-tab-open');
      }, RISE_MS);
    }
    controller.open();
  }

  /** Close whichever Saved sheet is showing. */
  function close() {
    CHOICES.forEach(function (choice) {
      if (!showing(choice)) return;
      var controller = controllerFor(choice);
      if (controller) controller.close();
    });
  }

  /** What a tap on the star does. */
  function toggle() {
    if (isOpen()) close();
    else open();
  }

  /**
   * Show another Saved tab in the box the current one is in.
   *
   * @param {SavedChoice} choice
   */
  function switchTo(choice) {
    const next = controllerFor(choice);
    if (!next) return;
    var from = current();
    remember(choice);
    // After this event's own dispatch: the sheet the tab sits in is about
    // to be emptied, and nothing else should read it mid-change.
    window.setTimeout(function () {
      if (from && from !== choice) {
        var previous = controllerFor(from);
        if (previous) previous.close();
      }
      if (!showing(choice)) next.open();
    }, 0);
  }

  /** @param {unknown} value @returns {value is SavedChoice} */
  function isChoice(value) {
    return typeof value === 'string' && CHOICES.indexOf(/** @type {SavedChoice} */ (value)) !== -1;
  }

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || input.name !== 'saved-tab') return;
    if (isChoice(input.value)) switchTo(input.value);
  });

  document.addEventListener('click', function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var go = target ? target.closest('[data-saved-go]') : null;
    if (!go) return;
    var choice = go.getAttribute('data-saved-go');
    if (isChoice(choice)) switchTo(choice);
  });

  if (star) {
    star.addEventListener('click', toggle);
  }

  // ---------------------------------------------------------------------
  // The star, and the tab bar, follow the sheets however they change.
  // ---------------------------------------------------------------------

  /** Paint the star's state, and tell the tab bar the sheets changed. */
  function paint() {
    var openNow = isOpen();
    if (star) star.setAttribute('aria-expanded', openNow ? 'true' : 'false');
    document.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { open: openNow } }));
  }

  if (typeof MutationObserver === 'function') {
    var observer = new MutationObserver(paint);
    sheets.forEach(function (el) {
      observer.observe(/** @type {Node} */ (el), {
        attributes: true,
        attributeFilter: ['hidden', DIRECT_ATTR],
      });
    });
  }
  paint();

  window.pwaSavedPanel = Object.freeze({
    open: open,
    close: close,
    isOpen: isOpen,
    toggle: toggle,
    switchTo: switchTo,
  });
}());
