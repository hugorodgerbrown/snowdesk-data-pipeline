// @ts-check
/*
 * static/js/app_tabbar.js — the installed phone app's tab bar, on the map
 * (SNOW-1476).
 *
 * The bar (templates/includes/_app_tabbar.html) is drawn only in the
 * installed app on a phone; this module runs wherever the map renders it,
 * and does nothing a visitor can see when the bar is not drawn — every
 * panel it opens is one the map already has.
 *
 *   - FOUR TABS. Today is the Today panel
 *     (window.pwaTodayPanel). Saved is the favourites, routes and report
 *     sheets (window.pwaFavouritesSheet / pwaRoutesSheet / pwaReportSheet),
 *     one at a time, switched by the chip row each renders under its
 *     header (includes/_saved_chips.html). Help is the map's own "?" menu
 *     (window.pwaMapHelpMenu, public/partials/_map_help_menu.html), whose
 *     roundel this app hides; it gains a Map key item there. Map closes
 *     whichever is open.
 *   - The downloads sheet, whose roundel this app hides, opens from the
 *     layers menu's Downloaded areas row (map_downloads_manager.js).
 *   - A TAB TOGGLES. Tapping the lit tab closes its panel; tapping another
 *     swaps it for the next in place. Today's and the Saved panels share
 *     one box, and the old one is hidden and the new one shown in the same
 *     task, so no frame is painted between them: the card stays put and
 *     only its content changes. A tab swap marks the new panel `data-tab-swap`,
 *     whose content (never the card, which would let the map show
 *     through) fades in; a Saved chip swaps with no mark at all, because
 *     the three sheets' headers sit in the same place (Hugo, 8 Nov 2026:
 *     "If a panel exists it should just replace the content"). The "?"
 *     menu is a card of its own, so it rises in and out instead.
 *   - THE LIT TAB FOLLOWS THE PANELS. A panel closed by something else —
 *     a Today or Saved row handing off to the region or place sheet, the
 *     ×, Escape — lights Map again; one reopened by map_panel_trail.js's
 *     back pill lights its tab. Read from the panels' `hidden` attribute.
 *   - SIGNED OUT, Today and Saved open the favourites sheet, which already
 *     invites a visitor to sign in; the Today panel is not rendered for
 *     them (public/partials/_today_surface.html).
 *   - SAVED REMEMBERS its chip for the session (sessionStorage), so the tab
 *     reopens the list last shown; places by default.
 *   - ?panel=today|saved|help, which the bar's links carry off the map,
 *     opens that panel once at boot. Any other value is left in the URL
 *     for static/js/map.js's own ?panel= handler (favourites, routes,
 *     reports, downloads), which runs after this module. The parameter is then removed with
 *     history.replaceState and every other parameter kept (the day in
 *     `d`, a camera in `at`), so a reload, Back or Forward never reopens
 *     it, and opening a panel adds no history entry, as no sheet does.
 *   - THE DOT. The Today tab shows the alert dot the hidden sun roundel
 *     carries (static/js/today_panel.js owns it), mirrored as it changes.
 */
(function appTabbarInit() {
  'use strict';

  var found = document.querySelector('[data-app-tabbar][data-app-tabbar-on-map]');
  if (!found || !window.MapSheet) return;
  // A const, so the null check above holds inside the closures below.
  const bar = found;

  /** @typedef {{ open(): void, close(): void, isOpen(): boolean }} Panel */
  /** @typedef {'map'|'today'|'saved'|'help'} Tab */
  /** @typedef {'places'|'routes'|'observations'} SavedChoice */

  var TABS = /** @type {Array<Tab>} */ (['map', 'today', 'saved', 'help']);
  var PANEL_PARAM = 'panel';
  var SAVED_KEY = 'snowdesk:app-tab-saved';
  var SWAP_MS = 200;

  /** @type {Record<SavedChoice, string>} */
  var SAVED_SHEET_IDS = {
    places: 'favourite-sheet',
    routes: 'route-sheet',
    observations: 'report-sheet',
  };

  /**
   * The controller behind one Saved choice, or null where the map does not
   * render that surface.
   *
   * @param {SavedChoice} choice
   * @returns {Panel|null}
   */
  function savedController(choice) {
    if (choice === 'routes') return window.pwaRoutesSheet || null;
    if (choice === 'observations') return window.pwaReportSheet || null;
    return window.pwaFavouritesSheet || null;
  }

  /** @returns {SavedChoice} The chip Saved opens on. */
  function savedChoice() {
    var stored = null;
    try {
      stored = window.sessionStorage.getItem(SAVED_KEY);
    } catch (_err) {
      // Storage refused (private window): places, every time.
    }
    if (stored === 'routes' || stored === 'observations') return stored;
    return 'places';
  }

  /** @param {SavedChoice} choice */
  function rememberSaved(choice) {
    try {
      window.sessionStorage.setItem(SAVED_KEY, choice);
    } catch (_err) {
      // Not remembered; harmless.
    }
  }

  // ---------------------------------------------------------------------
  // Help: the map's own "?" menu.
  // ---------------------------------------------------------------------

  var helpMenu = document.getElementById('map-help-menu');
  /**
   * The "?" menu as a tab's panel, read when used: map_help_menu.js may
   * load after this module.
   *
   * @type {Panel}
   */
  var helpPanel = {
    open: function () {
      window.pwaMapHelpMenu?.open();
    },
    close: function () {
      window.pwaMapHelpMenu?.close();
    },
    isOpen: function () {
      return !!window.pwaMapHelpMenu?.isOpen();
    },
  };

  // ---------------------------------------------------------------------
  // Tabs → panels.
  // ---------------------------------------------------------------------

  /** True when the Today panel is on the page: a signed-in reader. */
  function signedIn() {
    return !!window.pwaTodayPanel;
  }

  /**
   * The panel a tab opens now, or null (Map, or a surface not on the page).
   *
   * @param {Tab} tab
   * @returns {Panel|null}
   */
  function panelFor(tab) {
    if (tab === 'today') {
      return signedIn() ? window.pwaTodayPanel : savedController('places');
    }
    if (tab === 'saved') {
      return signedIn() ? savedController(savedChoice()) : savedController('places');
    }
    if (tab === 'help') return helpMenu ? helpPanel : null;
    return null;
  }

  /**
   * The element a tab's panel lives in, for the swap attribute.
   *
   * @param {Tab} tab
   * @returns {HTMLElement|null}
   */
  function elementFor(tab) {
    if (tab === 'today') {
      return document.getElementById(signedIn() ? 'today-sheet' : SAVED_SHEET_IDS.places);
    }
    if (tab === 'saved') {
      return document.getElementById(SAVED_SHEET_IDS[signedIn() ? savedChoice() : 'places']);
    }
    if (tab === 'help') return helpMenu;
    return null;
  }

  /** @type {Tab|null} The tab that last opened a panel. */
  var openedBy = null;

  /**
   * The tab whose panel is open, or null. The tab that opened a panel wins
   * while that panel is open (signed out, Today and Saved share one);
   * otherwise whichever panel is showing, so a panel reopened by a back
   * pill still lights its tab.
   *
   * @returns {Tab|null}
   */
  function openTab() {
    if (openedBy) {
      var mine = panelFor(openedBy);
      if (mine && mine.isOpen()) return openedBy;
    }
    if (signedIn() && window.pwaTodayPanel.isOpen()) return 'today';
    if (helpPanel.isOpen()) return 'help';
    var choices = /** @type {Array<SavedChoice>} */ (['places', 'routes', 'observations']);
    for (var i = 0; i < choices.length; i++) {
      var controller = savedController(choices[i]);
      if (controller && controller.isOpen()) return 'saved';
    }
    return null;
  }

  /** Light the tab whose panel is open, Map when none is. */
  function light() {
    var lit = openTab() || 'map';
    TABS.forEach(function (tab) {
      var button = bar.querySelector('[data-app-tab="' + tab + '"]');
      if (!button) return;
      if (tab === lit) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
  }

  /**
   * Open `panel`, marking `el` for the CSS: `open` rises as a first open,
   * `swap` fades the content in over a card already in place, and null
   * shows it as it is.
   *
   * @param {Panel} panel
   * @param {HTMLElement|null} el
   * @param {'open'|'swap'|null} kind
   */
  function openPanel(panel, el, kind) {
    if (!el || !kind) {
      panel.open();
      return;
    }
    const target = el;
    const mark = 'data-tab-' + kind;
    target.setAttribute(mark, '');
    panel.open();
    window.setTimeout(function () {
      target.removeAttribute(mark);
    }, SWAP_MS);
  }

  /**
   * What a tap on a tab does.
   *
   * @param {Tab} tab
   */
  function select(tab) {
    var current = openTab();
    var currentPanel = current ? panelFor(current) : null;
    if (tab === 'map' || tab === current) {
      if (currentPanel) currentPanel.close();
      openedBy = null;
      light();
      return;
    }
    var next = panelFor(tab);
    if (!next) return;
    var swap = !!(currentPanel && currentPanel.isOpen());
    if (currentPanel && currentPanel !== next) currentPanel.close();
    openedBy = tab;
    // The "?" menu is its own card, not the panels' box: it rises.
    /** @type {'open' | 'swap'} */
    var kind = swap && tab !== 'help' && current !== 'help' ? 'swap' : 'open';
    if (!next.isOpen()) openPanel(next, elementFor(tab), kind);
    light();
  }

  bar.addEventListener('click', function (event) {
    var target = /** @type {Element|null} */ (event.target instanceof Element ? event.target : null);
    var button = target ? target.closest('[data-app-tab]') : null;
    if (!button) return;
    var tab = /** @type {Tab} */ (button.getAttribute('data-app-tab'));
    if (TABS.indexOf(tab) === -1) return;
    select(tab);
  });

  // ---------------------------------------------------------------------
  // The Saved chips: one sheet closes as its sibling opens.
  // ---------------------------------------------------------------------

  document.addEventListener('change', function (event) {
    var input = event.target;
    if (!(input instanceof HTMLInputElement) || input.name !== 'saved-tab') return;
    var choice = /** @type {SavedChoice} */ (input.value);
    if (!(choice in SAVED_SHEET_IDS)) return;
    const next = savedController(choice);
    if (!next) return;
    var from = /** @type {SavedChoice|null} */ (null);
    Object.keys(SAVED_SHEET_IDS).forEach(function (key) {
      var controller = savedController(/** @type {SavedChoice} */ (key));
      if (controller && controller !== next && controller.isOpen()) {
        from = /** @type {SavedChoice} */ (key);
      }
    });
    rememberSaved(choice);
    openedBy = 'saved';
    // After this event's own dispatch: the sheet the chip sits in is about
    // to be emptied, and nothing else should read it mid-change.
    window.setTimeout(function () {
      if (from) {
        var previous = savedController(from);
        if (previous) previous.close();
      }
      if (!next.isOpen()) openPanel(next, document.getElementById(SAVED_SHEET_IDS[choice]), null);
      light();
    }, 0);
  });

  // ---------------------------------------------------------------------
  // Keep the lit tab true to the panels, however they open or close.
  // ---------------------------------------------------------------------

  var watched = ['today-sheet', 'map-help-menu']
    .concat(Object.keys(SAVED_SHEET_IDS).map(function (key) {
      return SAVED_SHEET_IDS[/** @type {SavedChoice} */ (key)];
    }))
    .map(function (id) {
      return document.getElementById(id);
    })
    .filter(Boolean);
  if (typeof MutationObserver === 'function') {
    var observer = new MutationObserver(light);
    watched.forEach(function (el) {
      observer.observe(/** @type {Node} */ (el), { attributes: true, attributeFilter: ['hidden'] });
    });
  }

  // ---------------------------------------------------------------------
  // The alert dot, mirrored from the sun roundel.
  // ---------------------------------------------------------------------

  var sourceDot = document.querySelector('#map-today-panel-toggle [data-today-dot]');
  var tabDot = bar.querySelector('[data-app-tab-dot]');
  if (sourceDot && tabDot) {
    var mirror = function () {
      /** @type {HTMLElement} */ (tabDot).hidden = /** @type {HTMLElement} */ (sourceDot).hidden;
    };
    mirror();
    if (typeof MutationObserver === 'function') {
      new MutationObserver(mirror).observe(sourceDot, { attributes: true, attributeFilter: ['hidden'] });
    }
  }

  // ---------------------------------------------------------------------
  // ?panel= — the bar's links from the other pages.
  // ---------------------------------------------------------------------

  /** Open the panel ?panel= names, once, and take the parameter off the URL. */
  function bootPanel() {
    var url;
    try {
      url = new URL(window.location.href);
    } catch (_err) {
      return;
    }
    var asked = url.searchParams.get(PANEL_PARAM);
    // Only the bar's own names. The rest (favourites, routes, reports,
    // downloads) belong to map.js's openPanelDeepLink, which runs after
    // this and must still find the parameter.
    if (asked !== 'today' && asked !== 'saved' && asked !== 'help') return;
    url.searchParams.delete(PANEL_PARAM);
    try {
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    } catch (_err) {
      // A sandboxed frame refuses it; the panel still opens.
    }
    // The parameter is taken now, before map.js's own deep-link reader
    // strips it, but Help's controller comes from the map bundle, which
    // runs after this one: open it once every deferred script has run.
    if (asked === 'help' && !window.pwaMapHelpMenu && document.readyState !== 'complete') {
      document.addEventListener('DOMContentLoaded', function () { select('help'); }, { once: true });
      return;
    }
    select(asked);
  }

  light();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootPanel);
  } else {
    bootPanel();
  }

  window.pwaAppTabbar = Object.freeze({ select: select, light: light });
}());
