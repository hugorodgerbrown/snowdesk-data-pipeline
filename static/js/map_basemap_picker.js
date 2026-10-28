/*
 * static/js/map_basemap_picker.js — the basemap popover and the setStyle swap it drives.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// ``mapDatePillInit`` was removed here. It drove a #map-date-pill element
// that has not existed in _map_embed.html since SNOW-314 moved the scrubbed-
// date readout to .map-date-ribbon in the bottom-left row, so the IIFE
// returned at its first line on every load.

// SNOW-58: basemap layer picker — opens a popover of basemap radio
// buttons and swaps the MapLibre style on selection. Persistence and
// initial aria-checked state are handled by the main IIFE before the
// map is constructed so the popover renders correctly on first paint.
//
// SNOW-904: it drives EVERY layer now, not the boundary tiers and the
// basemap alone. Four overlays — downloads, favourites, field observations
// and routes — were toggled from a "Display on the map" switch in the
// footer of whichever panel their roundel opened; those switches are gone
// and each is a row here. Three things follow from that, all of them in
// this file:
//
//   - the menu has a pinned header carrying a live "N layers on" count, and
//     each layer with options a derived second line naming what is on in
//     them. Both
//     are read off the rows' own ``aria-checked``, so nothing here keeps a
//     flag that could disagree with the controls;
//   - (SNOW-1266 replaced SNOW-904's collapsible sections — see below);
//   - a row whose overlay needs an account (favourites and routes, never
//     field observations — those are public) hands an anonymous visitor to
//     the sign-in sheet instead of ticking a box over an empty layer.
//
// SNOW-1266: on/off first, options one level down. The first level is one
// row per layer, in two groups ("The map", "Yours"). A row's switch says
// whether its layer is drawn; a row with options opens them on a PAGE of
// their own, in place of the first level, with a back arrow — the
// collapsible sections, and the per-section storage keys that remembered
// which were open, are gone. Two layers have no single row of their own to
// be the switch, so their switches are MASTERS (``data-layer-master``),
// derived from the rows on their options page and never counted twice:
//
//   - bulletins: on while any opacity step above 0 is checked. Off sends
//     step 0 — the old "Bulletins off" segment, which is no longer drawn —
//     and on sends back the step it was switched off at;
//   - boundaries: on while any EAWS tier is. Off clicks each checked tier
//     off and remembers which; on clicks them back (Micro regions when
//     nothing is remembered).
//
// See docs/decisions/the-layers-menu-is-on-off-then-options.md.
//
// The four new rows drive the SAME ``window.pwa*Overlay`` bridges the
// panel switches drove. That is deliberate: the telemetry emitters and the
// roundel-ring announcement all live inside those bridges, so routing through them keeps every one
// of them without this file learning about any of them.
//
// Style swapping itself happens via MAP.setStyle(); the regions source
// + layers are re-installed by a style.load handler inside the main
// IIFE. Active timelapse playback (if any) is stopped first via the
// snowdesk:basemap-changing event so its setInterval doesn't paint
// into a half-loaded style.
(function basemapPickerInit() {
  const pill = document.getElementById('basemap-pill');
  if (!pill) return;
  const toggle = document.getElementById('basemap-toggle');
  const menu = document.getElementById('basemap-menu');
  if (!toggle || !menu) return;
  const items = Array.from(menu.querySelectorAll('.basemap-menu-item'));
  if (items.length === 0) return;

  const STORAGE_KEY = BASEMAP_STORAGE_KEY;

  // SNOW-904: the header count and the level-one summaries are assembled
  // here, so their words have to be server-translated and read back —
  // ``makemessages`` never scans JavaScript. The literals are the English
  // fallback for a page that omits the strings template.
  const STRINGS = self.pwaStrings.read('map-layers-menu-strings-template', {
    'count-none': 'No layers on',
    'count-one': '1 layer on',
    'count-many': '%(count)s layers on',
    'none-selected': 'None selected',
    'summary-overflow': '%(count)s of %(total)s on',
    'signin-favourites': 'Sign in to save places and show your favourites on the map.',
    'signin-routes': 'Sign in to upload routes and show them on the map.',
    'signin-cta': 'Sign in',
  });

  // SNOW-1266: where the two master switches remember what they turned off.
  // The bulletins one holds a step (0.25…1), the boundaries one a JSON list
  // of tier keys. Read only when a master is switched back on.
  const BULLETINS_LAST_STEP_KEY = 'snowdesk.map.bulletins.last-on-step';
  const BOUNDARIES_LAST_ON_KEY = 'snowdesk.map.boundaries.last-on';
  const DEFAULT_BULLETINS_STEP = 0.5;
  const DEFAULT_BOUNDARIES = ['l4'];
  const BOUNDARY_KEYS = ['l1', 'l2', 'l4'];

  // Longest a page's summary may get before it stops naming rows and
  // states a count instead. The menu is shrink-to-fit and its rows are
  // ``white-space: nowrap``, so an unbounded summary would widen the whole
  // menu to fit a line nobody reads.
  const MAX_SUMMARY_CHARS = 38;

  // SNOW-904: the four rows this ticket added, each mapped to the bridge
  // that already owns its overlay. Read lazily (``() =>``) because map.js
  // publishes them from inside its own IIFE, which has run by the time a
  // click arrives but is not guaranteed to have at parse time.
  const OVERLAY_BRIDGES = {
    favourites: () => window.pwaFavouritesOverlay,
    community_reports: () => window.pwaCommunityReportsOverlay,
    routes: () => window.pwaRoutesOverlay,
    downloads: () => window.pwaDownloadedOverlay,
    // SNOW-1109: the terrain filter's row is a checkbox now, not an action
    // row, so it toggles through its bridge like the four above.
    terrain: () => window.pwaTerrainFilter,
    // "Highlight problem terrain" (2026-10-23): the bulletins' own terrain,
    // its own row under Conditions rather than a colour of the filter.
    problem_terrain: () => window.pwaBulletinTerrain,
    // SNOW-1185: "Show unsupported regions" — a re-filter of the micro
    // regions that only map.js's applyCountryFilters can compose.
    unsupported: () => window.pwaUnsupportedRegions,
  };

  // The two rows that need an account, and the sentence each hands off
  // with. ``community_reports`` is deliberately absent: #map's
  // ``data-community-reports-eligible`` is a hardcoded "true" because
  // community reports are public data that genuinely works signed out.
  const SIGNIN_GATED = {
    favourites: 'signin-favourites',
    routes: 'signin-routes',
  };

  /**
   * Is the visitor able to see this overlay's data at all?
   *
   * Read off ``#map``'s own eligibility attributes rather than kept here,
   * so this answers exactly what map.js answers when it decides whether to
   * fetch the layer. Absent element or absent attribute reads as eligible —
   * the gate exists to redirect a visitor who has nothing to draw, never to
   * withhold a row from one who might.
   *
   * @param {string} key - an overlay key.
   * @returns {boolean}
   */
  const overlayEligible = (key) => {
    const el = document.getElementById('map');
    if (!el) return true;
    const attr = key === 'favourites'
      ? el.dataset.favouritesEligible
      : el.dataset.routesEligible;
    return attr !== 'false';
  };

  /**
   * A row's label, shortened for a level-one summary line.
   *
   * Derived from the row's own text rather than a second string per row:
   * a trailing parenthetical goes ("SLF bulletins (CH)" → "SLF bulletins").
   *
   * @param {HTMLElement} item - a ``.basemap-menu-item``.
   * @returns {string}
   */
  const shortLabel = (item) => {
    // SNOW-978: a row with a second line (the terrain filter's summary)
    // names itself by its label span alone.
    const labelEl = item.querySelector('[data-row-label]');
    const full = self.pwaStrings.collapse((labelEl || item).textContent);
    return full.replace(/\s*\([^)]*\)$/, '').trim();
  };

  /**
   * Whether a row's layer is on: its ``aria-checked``, for every checkbox
   * and radio row. (SNOW-1109 made the terrain filter's row a checkbox, so
   * the bridge read SNOW-978 needed here is gone.)
   *
   * @param {HTMLElement} row - a ``.basemap-menu-item``.
   * @returns {boolean}
   */
  const rowIsOn = (row) => row.getAttribute('aria-checked') === 'true';

  /**
   * The second line of a level-one row with options: which of the rows on
   * its page are on.
   *
   * "None selected" when nothing is; the names, comma-separated, when they
   * fit; "N of M on" when they do not. The Basemap row names its radio
   * selection through the same path, since a checked radio is a checked
   * row like any other.
   *
   * @param {HTMLElement} group - the page's ``role="group"`` list.
   * @returns {string}
   */
  const summaryFor = (group) => {
    const rows = Array.from(group.querySelectorAll('.basemap-menu-item'));
    const checked = rows.filter(rowIsOn);
    if (checked.length === 0) return STRINGS['none-selected'];
    const named = checked.map(shortLabel).join(', ');
    if (named.length <= MAX_SUMMARY_CHARS) return named;
    return self.pwaStrings.interpolate(STRINGS['summary-overflow'], {
      count: checked.length,
      total: rows.length,
    });
  };

  /**
   * Whether the bulletins master is on: any opacity step above 0 checked.
   *
   * map.js mirrors the EFFECTIVE step onto the segments, so this reads off
   * while resort-edit mode suppresses the choropleth, as the segments do.
   *
   * @returns {boolean}
   */
  const bulletinsOn = () => Array.from(menu.querySelectorAll('[data-bulletins-step]')).some(
    (seg) => Number(seg.dataset.bulletinsStep) > 0 && seg.getAttribute('aria-checked') === 'true',
  );

  /**
   * The EAWS tier rows, in menu order.
   *
   * @returns {HTMLElement[]}
   */
  const boundaryRows = () => BOUNDARY_KEYS
    .map((key) => menu.querySelector(`[data-overlay-key="${key}"]`))
    .filter(Boolean);

  /**
   * Repaint the header count, the two master switches and every level-one
   * summary from the rows' live ``aria-checked``.
   *
   * The count is the level-one SWITCHES that are on — one per layer, so a
   * provider or a tier never counts on top of the layer it belongs to, and
   * the basemap, which has no switch, never inflates it. Run on every open,
   * after every toggle and at parse time, which is after map.js has seeded
   * the rows from the persisted state.
   *
   * @returns {void}
   */
  const refreshReadout = () => {
    const bulletinsMaster = menu.querySelector('[data-layer-master="bulletins"]');
    if (bulletinsMaster) {
      bulletinsMaster.setAttribute('aria-checked', bulletinsOn() ? 'true' : 'false');
    }
    const boundariesMaster = menu.querySelector('[data-layer-master="boundaries"]');
    if (boundariesMaster) {
      boundariesMaster.setAttribute(
        'aria-checked', boundaryRows().some(rowIsOn) ? 'true' : 'false',
      );
    }
    const countEl = menu.querySelector('[data-layers-count]');
    if (countEl) {
      const on = Array.from(menu.querySelectorAll('[data-layer-switch]')).filter(rowIsOn).length;
      if (on === 0) {
        countEl.textContent = STRINGS['count-none'];
      } else if (on === 1) {
        countEl.textContent = STRINGS['count-one'];
      } else {
        countEl.textContent = self.pwaStrings.interpolate(STRINGS['count-many'], {
          count: on,
        });
      }
    }
    for (const summary of menu.querySelectorAll('[data-page-summary]')) {
      const group = document.getElementById(`basemap-menu-group-${summary.dataset.pageSummary}`);
      if (group) summary.textContent = summaryFor(group);
    }
  };

  // SNOW-658: this menu's name in the shared map-overlay registry — the
  // element's own id, so a failing exclusivity assertion names something
  // greppable.
  const MENU_OVERLAY_NAME = 'basemap-menu';

  // SNOW-511: the menu is bottom-anchored (CSS `bottom: -96px`) and grows
  // upward. On a short viewport a tall menu grows past the top of #map,
  // sliding its first rows (the Countries section) up behind the nav and
  // the conditional off-season banner where they can't be reached — the CSS
  // `max-height: calc(100dvh - 96px)` floor reserves nothing for that top
  // chrome. Clamp the height to the room actually available between #map's
  // top edge (a small gap below it) and the menu's fixed bottom baseline so
  // the top rows stay on-screen and the overflowing list scrolls internally.
  // The menu's bottom is pinned by CSS regardless of its height, so reading
  // its baseline before applying the cap is stable. Recomputed on each open
  // and on resize because the banner (conditional) and the top safe-area
  // inset both move #map's top.
  //
  // SNOW-656 lowers the BASELINE as well, because clamping alone was solving
  // the wrong half of the problem. The CSS `bottom` is measured from the
  // basemap pill, and that pill is the FIRST roundel in a bottom-anchored
  // column — so the taller the column, the higher the pill sits and the
  // higher the baseline lands. On a 375x812 phone it put the menu's lower
  // edge at y=455 with the scrubber at y=707: a 333px window over 580px of
  // content, opening already scrolled past its own Countries section, while
  // 253px of map below it went unused. Dropping the baseline to just above
  // the scrubber roughly doubles the room and removes the scroll at that
  // size.
  // SNOW-658: both gaps, and the floor/cap arithmetic that used them, moved
  // to static/js/map_overlay_bounds.js — the three UGC sheets need the same
  // answer, and a second copy of it would be free to drift from this one.
  // What stays here is the translation into the menu's own coordinate base,
  // which is the half the sheets cannot share (see that module's header).
  //
  // SNOW-664 changed that base. The menu is a child of .map-controls-br now,
  // not of #basemap-pill: the pill sits inside a box that clips both axes
  // (#map-controls-collapsible then, the "map" capsule — .map-control-group —
  // since the column became two groups), so a menu opening leftward out of
  // it would simply be cut off. The floor and the cap are unchanged; only the
  // box the `bottom` offset is measured from is, which is why the pill's own
  // height no longer enters into it.
  const stack = document.getElementById('map-controls-br');

  /**
   * Place the menu's lower edge and cap its height to the room that leaves.
   *
   * Both halves are measured rather than declared: the column's height varies
   * with the collapsible group and with which roundels are eligible, and
   * `#map`'s top moves with the nav, the conditional off-season banner and
   * the safe-area inset. Recomputed on every open and on resize for the same
   * reason.
   *
   * @returns {void}
   */
  const positionMenu = () => {
    const bounds = window.pwaOverlayBounds?.compute();
    if (!bounds) return;

    // `bottom` is measured from the STACK — the menu's containing block since
    // SNOW-664 — and is negative downward, so this is the offset that puts the
    // menu's lower edge on the floor. Falls back to the CSS value if the stack
    // has no box yet — the menu is unhidden before this runs, but a
    // display:none ancestor would still yield zeros.
    const anchorBottom = stack ? stack.getBoundingClientRect().bottom : 0;
    if (anchorBottom > 0) {
      menu.style.bottom = `${Math.round(anchorBottom - bounds.floorY)}px`;
    }

    // Then the height that baseline leaves above it, so the first rows never
    // clip behind the header (SNOW-511's original point).
    menu.style.maxHeight = `${bounds.maxHeight}px`;
  };

  /**
   * Show one page of the menu — ``'main'``, or the options of one layer —
   * in place of whichever was showing.
   *
   * ``positionMenu()`` runs on every change, not only on open: it writes
   * ``max-height`` inline from the room the viewport leaves, and the content
   * that height bounds has just been swapped. Focus moves with the page —
   * to its back button going in, and to the row that opened it coming out —
   * so a keyboard user is never left on a control that just disappeared.
   *
   * @param {string} slug - a ``data-layers-page`` value.
   * @param {{focus?: boolean}} [options] - ``focus: false`` while resetting
   *   the menu as it opens, which keeps focus where the opener left it.
   * @returns {void}
   */
  const showPage = (slug, { focus = true } = {}) => {
    const current = menu.querySelector('[data-layers-page]:not([hidden])');
    const from = current ? current.dataset.layersPage : 'main';
    let target = null;
    for (const page of menu.querySelectorAll('[data-layers-page]')) {
      const shown = page.dataset.layersPage === slug;
      page.hidden = !shown;
      if (shown) target = page;
    }
    if (!target) return;
    target.scrollTop = 0;
    if (!menu.hidden) positionMenu();
    if (!focus) return;
    const next = slug === 'main'
      ? menu.querySelector(`[data-layers-open="${from}"]`)
      : target.querySelector('[data-layers-back]');
    if (next) next.focus();
  };

  for (const opener of menu.querySelectorAll('[data-layers-open]')) {
    opener.addEventListener('click', (e) => {
      // Same reason every row below stops propagation: the document-level
      // outside-click dismiss must not read an opener tap as "outside" —
      // and by the time it ran, the row tapped would no longer be shown.
      e.stopPropagation();
      showPage(opener.dataset.layersOpen);
    });
  }
  for (const back of menu.querySelectorAll('[data-layers-back]')) {
    back.addEventListener('click', (e) => {
      e.stopPropagation();
      showPage('main');
    });
  }

  // SNOW-904: the sign-in hand-off behind the Favourites and Routes rows.
  // MapSheet.attach empties the sheet on every close, so the body is cloned
  // from its template on every open — the same shape the favourites and
  // routes panels use.
  const signinSheetEl = document.getElementById('map-layer-signin-sheet');
  const signinTemplate = document.getElementById('map-layer-signin-template');
  const signinSheet =
    signinSheetEl && window.MapSheet ? window.MapSheet.attach(signinSheetEl) : null;

  /**
   * Hand an anonymous visitor off to sign-in for one gated row.
   *
   * Opening the sheet announces itself to ``window.pwaMapOverlays``, which
   * closes this menu — so the tap reads as a hand-off rather than as a menu
   * that ignored the click. The row itself is left untouched: nothing is
   * ticked, and nothing is persisted, for an overlay that has no data.
   *
   * @param {string} key - ``'favourites'`` or ``'routes'``.
   * @returns {void}
   */
  const openSigninSheet = (key) => {
    if (!signinSheet || !signinTemplate || !window.snowdeskSigninCta) return;
    const el = signinSheet.element;
    el.replaceChildren(signinTemplate.content.cloneNode(true));
    el.appendChild(
      window.snowdeskSigninCta.build(
        menu.dataset.signinUrl || '',
        STRINGS[SIGNIN_GATED[key]],
        STRINGS['signin-cta'],
      ),
    );
    signinSheet.open();
  };

  const setMenuOpen = (open) => {
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before the menu is unhidden, so whatever it replaces is gone by the
    // time this is on screen.
    // SNOW-1196: on a desktop the menu opens at the right, clear of the
    // left-column sheet, so it leaves that sheet open there.
    if (open) window.pwaMapOverlays?.opening(MENU_OVERLAY_NAME, { spareObjectSheets: 'wide' });
    // SNOW-1266: every open starts at the first level, so the menu always
    // opens on the answer to "what is on my map", never on a page left open
    // by the last visit.
    if (open) showPage('main', { focus: false });
    pill.dataset.state = open ? 'expanded' : 'collapsed';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    menu.hidden = !open;
    // SNOW-505: recompute the sync-status dots on every open, so no need
    // to keep them live while closed. SNOW-613: "cheap, client-side
    // probes" was written before the per-area bucket split — a pass is now
    // a dozen Cache Storage reads, several of which walk every pinned
    // bucket, so repeated opens coalesce inside `refresh()` rather than
    // each starting their own pass.
    if (open) window.pwaLayerSyncStatus?.refresh();
    // SNOW-904: the count and the level-one summaries are derived, so they are
    // recomputed here as well as on every toggle — the downloads row can
    // change under the menu while it is closed (its untouched state follows
    // the connection), and a stale header is exactly the kind of quiet lie
    // this menu was rebuilt to stop telling.
    if (open) refreshReadout();
    // SNOW-511/SNOW-656: place the baseline and size the menu to the visible
    // map area, once it's laid out.
    if (open) positionMenu();
  };

  // SNOW-857/SNOW-904: the downloads row is the one whose state can move
  // without anyone touching it — untouched means "on while offline", so a
  // connectivity flip repaints the overlay and this row has to follow.
  // map.js broadcasts every change to it; this is the same read-back the
  // downloads sheet's own switch made before SNOW-904 removed it.
  document.addEventListener('snowdesk:downloaded-overlay-changed', (event) => {
    const row = menu.querySelector('[data-overlay-key="downloads"]');
    if (!row) return;
    const visible = !!(event.detail && event.detail.visible);
    row.setAttribute('aria-checked', visible ? 'true' : 'false');
    refreshReadout();
  });

  // SNOW-904 review: the menu is the only CONTROL for these overlays, but it
  // is not the only CALLER of their bridges. row_focus.js turns a layer on
  // when someone focuses or creates a favourite, route or observation while
  // it is off (its ``reveal()`` calls ``overlay.show()``), and the deep-link
  // paths in map.js do the same. Nothing wrote that back to the row, so its
  // ``aria-checked`` went stale: the header undercounted, the level-one summary
  // omitted a layer that was plainly on the map, and the row's next click
  // called ``show()`` on an already-shown overlay — costing the user a
  // second click to turn something off.
  //
  // Re-seed from the bridges rather than from the event's payload: the
  // bridges are the state, ``isEnabled()`` is the preference every row
  // renders, and reading all four keeps this correct no matter which one
  // moved. Cheap enough to do unconditionally — four property reads and at
  // most four attribute writes, on an event that fires only when a layer
  // actually changes.
  const syncRowsFromBridges = () => {
    for (const [key, resolve] of Object.entries(OVERLAY_BRIDGES)) {
      const row = menu.querySelector(`[data-overlay-key="${key}"]`);
      if (!row) continue;
      const bridge = resolve();
      if (!bridge || typeof bridge.isEnabled !== 'function') continue;
      row.setAttribute('aria-checked', bridge.isEnabled() ? 'true' : 'false');
    }
    refreshReadout();
  };

  document.addEventListener(
    'snowdesk:overlay-visibility-changed',
    syncRowsFromBridges,
  );
  // SNOW-1109: the terrain filter row also follows its own announcement,
  // which is the one that fires once the layer is installed at boot and
  // when the sheet's adjust button turns the filter on.
  document.addEventListener(
    'snowdesk:terrain-filter-changed',
    syncRowsFromBridges,
  );
  // The problem-terrain row follows its own announcement for the same
  // reasons, and because the region sheet's switch writes it too.
  document.addEventListener(
    'snowdesk:problem-terrain-changed',
    syncRowsFromBridges,
  );

  // Seed the header and the summaries from the state map.js has already
  // written onto the rows (it runs before this file — see home.html).
  refreshReadout();

  // SNOW-588 exposed ``window.pwaLayersMenu.close()`` here so the "Manage
  // downloads" sheet could close this menu on its way in. SNOW-658 replaces
  // that one-directional bridge — and its single caller — with a
  // registration: the menu says how to ask whether it is open and how to
  // close it, and window.pwaMapOverlays
  // (static/js/map_overlay_exclusivity.js) closes it whenever ANY other map
  // overlay opens, not just the one surface that remembered to call.
  window.pwaMapOverlays?.register(MENU_OVERLAY_NAME, {
    isOpen: () => !menu.hidden,
    close: () => setMenuOpen(false),
  });

  // SNOW-511: keep the cap correct if the viewport changes while the menu is
  // open (orientation flip, mobile URL-bar show/hide, desktop resize).
  window.addEventListener('resize', () => {
    if (!menu.hidden) positionMenu();
  });

  // SNOW-664 closed this menu whenever the collapsible strip closed, because
  // #basemap-pill lived inside that strip. It does not any more: layers sits
  // in the always-visible "map" capsule with locate, and the chevron folds
  // only the "yours" capsule away, so collapsing it leaves the menu's own
  // roundel on screen and the menu has no reason to go.

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setMenuOpen(menu.hidden);
  });

  // SNOW-1266: the header's close button, shown on a phone only, where the
  // full-width menu covers the roundel that opened it.
  const closeButton = menu.querySelector('[data-layers-close]');
  if (closeButton) {
    closeButton.addEventListener('click', (e) => {
      e.stopPropagation();
      setMenuOpen(false);
      toggle.focus();
    });
  }

  // Outside-click dismiss. Use click (not pointerdown) so an item
  // selection inside the menu fires before this handler can close.
  //
  // SNOW-664: the menu has to be named here as well as the pill. It used to be
  // a DESCENDANT of the pill, so `pill.contains` covered both; it is a sibling
  // of the collapsible group now. Item clicks stop propagation and so never
  // reach this handler either way — but this menu is mostly not items. A tap
  // on a group label, a separator, the 4px padding or the scrollbar would
  // otherwise read as "outside" and close the menu the user was reading.
  document.addEventListener('click', (e) => {
    if (menu.hidden) return;
    if (pill.contains(e.target) || menu.contains(e.target)) return;
    setMenuOpen(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) {
      setMenuOpen(false);
      toggle.focus();
    }
  });
  // SNOW-897: the layer ids for each overlay live in ``OVERLAY_LAYERS``
  // (map_state.js), shared with map.js. This file used to carry its own
  // near-identical copy; see that table's comment for why there were two and
  // why one is enough. The rows this picker drives are a subset of its keys,
  // and looking up a key no row uses simply never happens.

  for (const item of items) {
    item.addEventListener('click', (e) => {
      e.stopPropagation();

      // Offline-integrity: a row map_layer_sync_status.js has disabled
      // (offline AND its resource/basemap isn't cached) is inert. Honour
      // aria-disabled here — the source of truth is that module's probe, so
      // there's no state to toggle and no basemap to swap to.
      if (item.getAttribute('aria-disabled') === 'true') return;

      // SNOW-59 / SNOW-172: overlay checkbox — toggle visibility or country filter.
      const overlayKey = item.dataset.overlayKey;
      if (overlayKey) {
        // SNOW-904: an overlay that needs an account, tapped by someone
        // without one, is a hand-off and not a toggle. Checked BEFORE the
        // aria-checked flip so nothing is ticked and nothing is persisted
        // for a layer that has no data behind it.
        if (SIGNIN_GATED[overlayKey] && !overlayEligible(overlayKey)) {
          openSigninSheet(overlayKey);
          return;
        }

        const next = item.getAttribute('aria-checked') !== 'true';
        item.setAttribute('aria-checked', next ? 'true' : 'false');
        refreshReadout();

        // SNOW-904: the four rows whose overlay is owned by a bridge in
        // map.js. Routing through the bridge rather than repeating its work
        // here is what keeps the telemetry emit, the favourited-resort
        // exclusion recompute and the roundel-ring announcement — and, for
        // downloads, keeps this file from ever writing that row's
        // localStorage on any path but a real click (SNOW-857's tri-state:
        // 'true'/'false' are the user's answer, null means untouched and
        // derives to "on while offline").
        const bridge = OVERLAY_BRIDGES[overlayKey] && OVERLAY_BRIDGES[overlayKey]();
        if (bridge) {
          if (next) bridge.show();
          else bridge.hide();
          return;
        }

        // SNOW-314 prototype: notify the season-header readout so its breadcrumb
        // mirrors which region tiers are visible (l1=Major, l2=Minor, l4=Micro).
        if (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'l4') {
          document.dispatchEvent(new CustomEvent('snowdesk:overlays-changed', {
            detail: { key: overlayKey, visible: next },
          }));
        }

        // SNOW-172: handle country.* toggles by delegating to the main IIFE
        // via a CustomEvent. countryState / ensureCountryLoaded / applyCountryFilters
        // are all scoped to the main IIFE and are not accessible here.
        //
        // SNOW-658: one dispatch PER CODE. A row is a bulletin provider now,
        // and ALBINA publishes for two countries — but nothing downstream
        // learned about providers: countryState, the per-code storage keys and
        // applyCountryFilters are all still per-code, so the merge is handled
        // here, by sending the same event twice. countryCodesFor
        // (static/js/map_state.js) is the one place the grouping is declared.
        if (overlayKey.startsWith('country.')) {
          for (const code of countryCodesFor(overlayKey)) {
            document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
              detail: { code, next },
            }));
          }
          return;
        }

        // SNOW-658: the favourites and community-reports telemetry emits that
        // sat here moved to their bridges in map.js, with the switches that
        // now drive them. Leaving them would not have double-counted — the
        // rows are gone, so these branches were simply unreachable — but the
        // next reader wiring a row back would have found two emitters.
        // Tier overlay — toggle layer visibility.
        writeStorage(OVERLAY_STORAGE_KEY[overlayKey], String(next));
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2'
                       || overlayKey === 'resorts' || overlayKey === 'weather')) {
            // SNOW-235: First enable of a lazy overlay tier — delegate to the
            // main IIFE via snowdesk:overlay-load so it can fetch the GeoJSON,
            // install the layers, and then make them visible. The main IIFE
            // listener handles both the fetch and the setLayoutProperty call,
            // so we return here without running the direct visibility loop.
            document.dispatchEvent(new CustomEvent('snowdesk:overlay-load', {
              detail: { key: overlayKey },
            }));
          } else {
            // Toggling off, or toggling a non-lazy tier (l4): use the direct
            // setLayoutProperty path. For the lazy tiers toggling off, the
            // layer may not exist yet (if the user enabled then immediately
            // disabled before the fetch resolved) — getLayer guards cover this.
            //
            // L4 recovery: unlike the lazy tiers, Micro regions has no
            // fetch-and-install path here — if a prior style swap dropped its
            // layers, a plain setLayoutProperty would silently no-op (the
            // reported "toggling micro-regions does nothing" bug). Rebuild
            // from the in-memory cache first (synchronous), then fall through
            // to make the freshly-added layers visible.
            if (next && overlayKey === 'l4' && !MAP.getLayer('regions-fill')) {
              document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
            }
            // SNOW-656: the companion bulletin-boundary load that used to sit
            // here moved to the Bulletins branch above, with the layer it
            // belongs to. Enabling the micro-region GEOGRAPHY says nothing
            // about whether that day's bulletin grouping should be drawn.
            for (const layerId of OVERLAY_LAYERS[overlayKey]) {
              if (MAP.getLayer(layerId)) {
                MAP.setLayoutProperty(
                  layerId, 'visibility', next ? 'visible' : 'none',
                );
              }
            }
            // The weather symbols sit beside a resort pin rather than over
            // it, but only while the pins are drawn: hiding them hands the
            // symbols their own points back. Enabling goes through
            // overlay-load above, which does the same on its way out.
            if (overlayKey === 'resorts') {
              document.dispatchEvent(new CustomEvent('snowdesk:resorts-hidden'));
            }
            // SNOW-1196: map_weather_window.js drops its "Forecast only" line
            // when the layer goes off. Enabling announces from map.js's
            // overlay-load listener instead.
            if (overlayKey === 'weather') {
              document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
            }
          }
          // SNOW-499's snowdesk:favourites-visibility-changed dispatch stood
          // here, recomputing the resort layer's favourited-resort exclusion
          // whenever the favourites overlay was flipped from this menu.
          // SNOW-658 took that row away, and SNOW-1229 the exclusion with
          // its listener: a pinned resort is a blue resort dot now, which the
          // favourites overlay does not govern.
        }
        return;
      }

      const url = item.dataset.basemapUrl;
      const key = item.dataset.basemapKey;
      if (!url || !key || !MAP) return;
      // No-op if this option is already active — just close the popover.
      if (item.getAttribute('aria-checked') === 'true') {
        setMenuOpen(false);
        return;
      }
      // Notify other consumers (timelapse) to surrender control before
      // we tear down the current style.
      document.dispatchEvent(new CustomEvent('snowdesk:basemap-changing', {
        detail: { key, url },
      }));
      writeStorage(STORAGE_KEY, key);
      // Only update aria-checked on basemap radios — overlay checkboxes
      // are independent and shouldn't be cleared when the basemap swaps,
      // and SNOW-588's "Manage downloads…" action row has no checked state
      // at all. Tested for positively rather than by excluding the overlay
      // rows, so a fourth kind of row added later is left alone by default
      // instead of silently acquiring an aria-checked it should not have.
      for (const other of items) {
        if (!other.dataset.basemapKey) continue;
        other.setAttribute(
          'aria-checked',
          other === item ? 'true' : 'false',
        );
      }
      // The Basemap row's summary names its selection, so it moves with
      // the radios even though the header count deliberately does not.
      refreshReadout();
      setMenuOpen(false);
      resolveBasemapStyle(key, url).then((style) => MAP.setStyle(style));
    });
  }

  // SNOW-978 / SNOW-1109: the opener that leads the Steep terrain row (it was
  // an adjust button beside the row until SNOW-1266). Not a
  // ``.basemap-menu-item`` — it has no layer of its own to count or
  // summarise — so it is wired here rather than in the loop above. It opens
  // the filter's sheet rather than a page here: the sheet IS that layer's
  // options (which turns the filter on if it is off); the
  // sheet announces itself to window.pwaMapOverlays, which closes this menu
  // on the way in.
  for (const adjust of menu.querySelectorAll('[data-terrain-filter-open]')) {
    adjust.addEventListener('click', (e) => {
      e.stopPropagation();
      window.pwaTerrainFilterSheet?.open();
    });
  }

  // Bulletin opacity — the steps on the Avalanche bulletins page, under the
  // three providers whose bulletins they shade. SNOW-1266 took the "off"
  // step out: that is the level-one switch now, below. SNOW-656 built this as a
  // roundel of its own with a flyout (``mapFillControlInit``, which lived
  // below this IIFE); the two-capsule column moved it into this menu, so
  // every choice about what the map draws is made in one place.
  //
  // The steps are deliberately NOT ``.basemap-menu-item``s, so the row loop
  // above never sees them (it would read one as a basemap radio). A click
  // stops propagation for the reason every row's does — the document-level
  // dismiss must not read it as "outside" — and leaves the menu OPEN: the
  // step is a setting the reader is tuning against the map, not a choice
  // that ends the visit, exactly as an overlay row is.
  //
  // The delegation follows the shape ``country.*`` uses: the value is a
  // persisted preference AND-ed with whatever is suppressing it (resort-edit
  // mode), and all of that lives in map.js's main IIFE. So this contributes
  // the click and nothing else — no localStorage write and no optimistic
  // ``aria-checked``, because map.js mirrors the EFFECTIVE step back onto
  // every ``[data-bulletins-step]`` and a write here could disagree with it.
  const opacity = menu.querySelector('.map-fill-steps');
  if (opacity) {
    opacity.addEventListener('click', (e) => {
      const seg = e.target.closest && e.target.closest('[data-bulletins-step]');
      if (!seg) return;
      e.stopPropagation();
      if (seg.getAttribute('aria-disabled') === 'true') return;
      const step = Number(seg.dataset.bulletinsStep);
      // Same recovery the L4 toggle makes: a prior style swap can have
      // dropped the regions layers, and the choropleth is one of them.
      if (step > 0 && MAP && !MAP.getLayer('regions-fill')) {
        document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
      }
      document.dispatchEvent(new CustomEvent('snowdesk:bulletins-step', {
        detail: { step },
      }));
      // map.js's listener runs inside that dispatch and has already
      // mirrored the step onto the segments, so the master reads it here.
      refreshReadout();
    });
  }

  /**
   * Send one bulletin step through map.js, as an opacity segment would.
   *
   * @param {number} step - 0 (off) or a step above it.
   * @returns {void}
   */
  const sendBulletinsStep = (step) => {
    if (step > 0 && MAP && !MAP.getLayer('regions-fill')) {
      document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
    }
    document.dispatchEvent(new CustomEvent('snowdesk:bulletins-step', {
      detail: { step },
    }));
    refreshReadout();
  };

  // SNOW-1266: the two master switches. Each toggles the rows on its page
  // through the paths those rows already use, so nothing downstream learns
  // that a master exists — and each remembers what it turned off, so off
  // then on is a round trip rather than a reset to the defaults.
  const bulletinsMaster = menu.querySelector('[data-layer-master="bulletins"]');
  if (bulletinsMaster) {
    bulletinsMaster.addEventListener('click', (e) => {
      e.stopPropagation();
      if (bulletinsOn()) {
        const checked = menu.querySelector('[data-bulletins-step][aria-checked="true"]');
        if (checked) writeStorage(BULLETINS_LAST_STEP_KEY, checked.dataset.bulletinsStep);
        sendBulletinsStep(0);
        return;
      }
      const remembered = Number(readStorage(BULLETINS_LAST_STEP_KEY));
      sendBulletinsStep(remembered > 0 ? remembered : DEFAULT_BULLETINS_STEP);
    });
  }

  const boundariesMaster = menu.querySelector('[data-layer-master="boundaries"]');
  if (boundariesMaster) {
    boundariesMaster.addEventListener('click', (e) => {
      e.stopPropagation();
      const on = boundaryRows().filter(rowIsOn);
      if (on.length > 0) {
        writeStorage(
          BOUNDARIES_LAST_ON_KEY, JSON.stringify(on.map((row) => row.dataset.overlayKey)),
        );
        for (const row of on) row.click();
        return;
      }
      let keys = DEFAULT_BOUNDARIES;
      try {
        const stored = JSON.parse(readStorage(BOUNDARIES_LAST_ON_KEY) || 'null');
        if (Array.isArray(stored)) {
          const known = stored.filter((key) => BOUNDARY_KEYS.includes(key));
          if (known.length > 0) keys = known;
        }
      } catch {
        // A hand-edited or truncated value reads as "nothing remembered".
      }
      for (const row of boundaryRows()) {
        if (keys.includes(row.dataset.overlayKey)) row.click();
      }
    });
  }
})();
