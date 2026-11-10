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
 *   - THREE TABS (SNOW-1479; Today was a fourth until SNOW-1479 folded it
 *     into Places). Saved is window.pwaSavedPanel (static/js/saved_panel.js,
 *     SNOW-1481): the favourites, routes, trips and report sheets, one at a
 *     time, switched by the tabs each renders in its header, opening on the
 *     tab last chosen. The star roundel opens the same panel off the app.
 *     Help is the map's own "?" menu (window.pwaMapHelpMenu,
 *     public/partials/_map_help_menu.html), whose roundel this app hides;
 *     it gains a Map key item there. Map closes whichever is open.
 *   - The downloads sheet, whose roundel this app hides, opens from the
 *     layers menu's Downloaded areas row (map_downloads_manager.js).
 *   - A TAB TOGGLES. Tapping the lit tab closes its panel; tapping another
 *     closes it and opens the next. A first open rises (`data-tab-open`):
 *     Saved marks its own sheet, and the "?" menu, the same phone card
 *     (SNOW-1486), is marked here.
 *   - THE LIT TAB FOLLOWS THE PANELS. A panel closed by something else —
 *     a Places row handing off to the region or place sheet, Escape —
 *     lights Map again; one reopened by map_panel_trail.js's back pill
 *     lights its tab. Read from the "?" menu's `hidden` attribute and
 *     saved_panel.js's `snowdesk:saved-panel-change`.
 *   - ?panel=saved|help, which the bar's links carry off the map, opens
 *     that panel once at boot. ?panel=today, from a link made before
 *     SNOW-1479, opens Saved on Places. Any other value is left in the URL
 *     for static/js/map.js's own ?panel= handler (favourites, routes,
 *     reports, downloads), which runs after this module. The parameter is then removed with
 *     history.replaceState and every other parameter kept (the day in
 *     `d`, a camera in `at`), so a reload, Back or Forward never reopens
 *     it, and opening a panel adds no history entry, as no sheet does.
 *   - THE DOT. The Saved tab shows the alert dot the hidden sun roundel
 *     carries (static/js/today_panel.js owns it), mirrored as it changes:
 *     Today's places are Saved → Places now.
 */
(function appTabbarInit() {
  'use strict';

  var found = document.querySelector('[data-app-tabbar][data-app-tabbar-on-map]');
  if (!found || !window.MapSheet) return;
  // A const, so the null check above holds inside the closures below.
  const bar = found;

  /** @typedef {{ open(): void, close(): void, isOpen(): boolean }} Panel */
  /** @typedef {'map'|'saved'|'help'} Tab */

  var TABS = /** @type {Array<Tab>} */ (['map', 'saved', 'help']);
  var PANEL_PARAM = 'panel';
  var SAVED_KEY = 'snowdesk:app-tab-saved';
  // The length of map.css's rise (app-tab-panel-rise).
  var RISE_MS = 200;

  /**
   * Saved: window.pwaSavedPanel (static/js/saved_panel.js), read when used.
   * One object, so comparing panels stays true to which is which.
   *
   * @type {Panel}
   */
  var savedPanel = {
    open: function () {
      window.pwaSavedPanel?.open();
    },
    close: function () {
      window.pwaSavedPanel?.close();
    },
    isOpen: function () {
      return !!window.pwaSavedPanel?.isOpen();
    },
  };

  /** @param {'places'} choice Remember the Saved tab, as saved_panel.js does. */
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

  /**
   * The panel a tab opens now, or null (Map, or a surface not on the page).
   *
   * @param {Tab} tab
   * @returns {Panel|null}
   */
  function panelFor(tab) {
    if (tab === 'saved') return window.pwaSavedPanel ? savedPanel : null;
    if (tab === 'help') return helpMenu ? helpPanel : null;
    return null;
  }

  /** @type {Tab|null} The tab that last opened a panel. */
  var openedBy = null;

  /**
   * The tab whose panel is open, or null. The tab that opened a panel wins
   * while that panel is open; otherwise whichever panel is showing, so a
   * panel reopened by a back pill still lights its tab.
   *
   * @returns {Tab|null}
   */
  function openTab() {
    if (openedBy) {
      var mine = panelFor(openedBy);
      if (mine && mine.isOpen()) return openedBy;
    }
    if (helpPanel.isOpen()) return 'help';
    if (savedPanel.isOpen()) return 'saved';
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
   * Open `panel`, marking `el` to rise as a first open (`data-tab-open`);
   * with no element it opens as it is. Saved marks its own sheet
   * (saved_panel.js), so it passes none.
   *
   * @param {Panel} panel
   * @param {HTMLElement|null} el
   */
  function openPanel(panel, el) {
    if (!el) {
      panel.open();
      return;
    }
    const target = el;
    target.setAttribute('data-tab-open', '');
    panel.open();
    window.setTimeout(function () {
      target.removeAttribute('data-tab-open');
    }, RISE_MS);
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
    if (currentPanel && currentPanel !== next) currentPanel.close();
    openedBy = tab;
    // The "?" menu is not one of Saved's sheets, so it is marked here.
    if (!next.isOpen()) openPanel(next, tab === 'help' ? helpMenu : null);
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
  // Keep the lit tab true to the panels, however they open or close.
  // ---------------------------------------------------------------------

  document.addEventListener('snowdesk:saved-panel-change', light);
  if (helpMenu && typeof MutationObserver === 'function') {
    new MutationObserver(light).observe(helpMenu, { attributes: true, attributeFilter: ['hidden'] });
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
    // SNOW-1479: Today is Saved → Places now; an old ?panel=today opens it.
    if (asked === 'today') {
      rememberSaved('places');
      asked = 'saved';
    }
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
    select(/** @type {Tab} */ (asked));
  }

  light();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootPanel);
  } else {
    bootPanel();
  }

  window.pwaAppTabbar = Object.freeze({ select: select, light: light });
}());
