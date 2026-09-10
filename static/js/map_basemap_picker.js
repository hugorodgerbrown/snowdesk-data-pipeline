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
//     each section a derived second line naming what is on inside it. Both
//     are read off the rows' own ``aria-checked``, so nothing here keeps a
//     flag that could disagree with the controls;
//   - the sections COLLAPSE, persisted one key per section
//     (``LAYERS_SECTION_STORAGE_KEY``), because seventeen rows in one list
//     is a scroll rather than a menu;
//   - a row whose overlay needs an account (favourites and routes, never
//     field observations — those are public) hands an anonymous visitor to
//     the sign-in sheet instead of ticking a box over an empty layer.
//
// The four new rows drive the SAME ``window.pwa*Overlay`` bridges the
// panel switches drove. That is deliberate: the telemetry emitters, the
// favourited-resort exclusion recompute and the roundel-ring announcement
// all live inside those bridges, so routing through them keeps every one
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

  // SNOW-904: the header count and the section summaries are assembled
  // here, so their words have to be server-translated and read back —
  // ``makemessages`` never scans JavaScript. The literals are the English
  // fallback for a page that omits the strings template.
  const STRINGS = self.pwaStrings.read('map-layers-menu-strings-template', {
    'count-none': 'No layers on',
    'count-one': '1 layer on',
    'count-many': '%(count)s layers on',
    'none-selected': 'None selected',
    'summary-overflow': '%(count)s of %(total)s on',
    'label-prefix': 'Display',
    'signin-favourites': 'Sign in to save places and show your favourites on the map.',
    'signin-routes': 'Sign in to upload routes and show them on the map.',
    'signin-cta': 'Sign in',
  });

  // Longest a section's summary may get before it stops naming rows and
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
   * A row's label, shortened for a section summary.
   *
   * Derived from the row's own text rather than a second string per row:
   * a trailing parenthetical goes ("SLF bulletins (CH)" → "SLF bulletins")
   * and so does the leading "Display" verb the two Basemap overlay rows
   * carry ("Display slope angles" → "slope angles"). The verb is read from
   * the strings template, so the strip survives a locale that words it
   * differently.
   *
   * @param {HTMLElement} item - a ``.basemap-menu-item``.
   * @returns {string}
   */
  const shortLabel = (item) => {
    const full = self.pwaStrings.collapse(item.textContent);
    const withoutParenthetical = full.replace(/\s*\([^)]*\)$/, '').trim();
    const prefix = STRINGS['label-prefix'];
    if (prefix && withoutParenthetical.toLowerCase().startsWith(`${prefix.toLowerCase()} `)) {
      return withoutParenthetical.slice(prefix.length + 1);
    }
    return withoutParenthetical;
  };

  /**
   * The one-line summary under a section heading: which of its rows are on.
   *
   * "None selected" when nothing is; the names, comma-separated, when they
   * fit; "N of M on" when they do not. The Basemap section names its own
   * radio selection through the same path, since a checked radio is a
   * checked row like any other.
   *
   * @param {HTMLElement} group - the section's ``role="group"`` list.
   * @returns {string}
   */
  const summaryFor = (group) => {
    const rows = Array.from(group.querySelectorAll('.basemap-menu-item'));
    const checked = rows.filter((row) => row.getAttribute('aria-checked') === 'true');
    if (checked.length === 0) return STRINGS['none-selected'];
    const named = checked.map(shortLabel).join(', ');
    if (named.length <= MAX_SUMMARY_CHARS) return named;
    return self.pwaStrings.interpolate(STRINGS['summary-overflow'], {
      count: checked.length,
      total: rows.length,
    });
  };

  /**
   * Repaint the header count and every section's summary from the rows'
   * live ``aria-checked``.
   *
   * The count is OVERLAY rows only — the basemap radio is always exactly
   * one and would inflate every reading by one without ever varying. Run on
   * every open, after every toggle and at parse time, which is after map.js
   * has seeded the rows from the persisted state.
   *
   * @returns {void}
   */
  const refreshReadout = () => {
    const countEl = menu.querySelector('[data-layers-count]');
    if (countEl) {
      const on = items.filter(
        (item) =>
          item.classList.contains('basemap-menu-item--overlay') &&
          item.getAttribute('aria-checked') === 'true',
      ).length;
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
    for (const section of menu.querySelectorAll('.basemap-menu-section')) {
      const group = section.querySelector('.basemap-menu-group');
      const summary = section.querySelector('[data-section-summary]');
      if (group && summary) summary.textContent = summaryFor(group);
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
  // not of #basemap-pill: the pill moved into #map-controls-collapsible, which
  // is `overflow: hidden` for its height animation and clips both axes, so a
  // menu opening leftward out of it would simply be cut off. That is the same
  // move SNOW-656 made for #map-fill-flyout, for the same reason. The floor
  // and the cap are unchanged; only the box the `bottom` offset is measured
  // from is, which is why the pill's own height no longer enters into it.
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
   * Open or close one section, and remember the choice.
   *
   * ``positionMenu()`` is called on every toggle, not only on open: it
   * writes ``max-height`` inline from the room the viewport leaves, and the
   * content whose height that bounds has just changed.
   *
   * @param {string} slug - the section's ``data-section-toggle`` value.
   * @param {boolean} open
   * @param {boolean} persist - false while seeding from storage at boot, so
   *   reading a preference never writes one back.
   * @returns {void}
   */
  const setSectionOpen = (slug, open, persist) => {
    const button = menu.querySelector(`[data-section-toggle="${slug}"]`);
    const group = document.getElementById(`basemap-menu-group-${slug}`);
    if (!button || !group) return;
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    group.hidden = !open;
    if (persist) writeStorage(LAYERS_SECTION_STORAGE_KEY(slug), String(open));
    if (!menu.hidden) positionMenu();
  };

  for (const button of menu.querySelectorAll('[data-section-toggle]')) {
    const slug = button.dataset.sectionToggle;
    setSectionOpen(
      slug,
      readBoolStorage(
        LAYERS_SECTION_STORAGE_KEY(slug), slug === LAYERS_SECTION_DEFAULT_OPEN,
      ),
      false,
    );
    button.addEventListener('click', (e) => {
      // Same reason every row below stops propagation: the document-level
      // outside-click dismiss must not read a heading tap as "outside".
      e.stopPropagation();
      setSectionOpen(slug, button.getAttribute('aria-expanded') !== 'true', true);
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
    if (open) window.pwaMapOverlays?.opening(MENU_OVERLAY_NAME);
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
    // SNOW-904: the count and the section summaries are derived, so they are
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

  // SNOW-664: the collapsible strip closing must take the menu with it —
  // #basemap-pill lives inside that strip now, so a menu left open would be
  // floating beside a roundel that is no longer on screen. The strip
  // dispatches no event (map_controls_collapse.js only writes `data-expanded`
  // on the stack), so observe that attribute — the same shape
  // mapFillControlInit below already uses for its own roundel.
  if (stack && typeof MutationObserver === 'function') {
    new MutationObserver(() => {
      if (stack.dataset.expanded !== 'true') setMenuOpen(false);
    }).observe(stack, { attributes: true, attributeFilter: ['data-expanded'] });
  }

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setMenuOpen(menu.hidden);
  });

  // Outside-click dismiss. Use click (not pointerdown) so an item
  // selection inside the menu fires before this handler can close.
  //
  // SNOW-664: the menu has to be named here as well as the pill. It used to be
  // a DESCENDANT of the pill, so `pill.contains` covered both; it is a sibling
  // of the collapsible group now. Item clicks stop propagation and so never
  // reach this handler either way — but this menu is mostly not items. A tap
  // on a section heading, a separator, the 4px padding or the scrollbar would
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
          }
          // SNOW-499's snowdesk:favourites-visibility-changed dispatch stood
          // here, recomputing the resort layer's favourited-resort exclusion
          // whenever the favourites overlay was flipped from this menu.
          // SNOW-658 took that row away; window.pwaFavouritesOverlay calls
          // applyResortsFavouritedFilter directly on both edges instead, being
          // inside the IIFE that owns it. The listener itself stays — other
          // callers still fire that event.
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
      // The Basemap section's summary names its selection, so it moves with
      // the radios even though the header count deliberately does not.
      refreshReadout();
      setMenuOpen(false);
      resolveBasemapStyle(key, url).then((style) => MAP.setStyle(style));
    });
  }
})();

// SNOW-656: the bulletin-fill strength control — a roundel in the bottom-right
// stack whose flyout opens to the LEFT with five opacity steps.
//
// Its own IIFE rather than part of basemapPickerInit above: it shares nothing
// with the basemap popover but the side it opens from, and it must keep
// working if that popover's early returns fire (no #basemap-pill on a page
// that embeds the map without a picker).
//
// It lives in this file because this is where every map-chrome control that
// delegates to the main IIFE is wired. The delegation follows the shape
// `country.*` already uses: the value is a persisted preference AND-ed with
// whatever is suppressing it (resort-edit mode — the downloads overlay used
// to suppress it too, and no longer does: the squares are a hatch the
// choropleth reads through, so both can be on). All of
// that lives in the main IIFE, so this contributes the click and nothing
// else — no localStorage write, and no optimistic `aria-checked`, because the
// main IIFE mirrors the EFFECTIVE step back onto all five segments and a
// write here could disagree with it.
(function mapFillControlInit() {
  const pill = document.getElementById('map-fill-pill');
  if (!pill) return;
  const toggle = document.getElementById('map-fill-toggle');
  const flyout = document.getElementById('map-fill-flyout');
  if (!toggle || !flyout) return;

  // SNOW-658: this flyout's name in the shared map-overlay registry — the
  // panel's own id, so a failing exclusivity assertion names something
  // greppable. The overlay is the FLYOUT, not #map-fill-pill: the pill is the
  // roundel, which stays on screen (and keeps its `data-state`) whether the
  // flyout is up or not.
  const FLYOUT_OVERLAY_NAME = 'map-fill-flyout';

  // The flyout is a child of .map-controls-br, not of the roundel — the
  // roundel lives inside #map-controls-collapsible, which is `overflow:
  // hidden` for its height animation and would clip a panel extending left
  // out of it. So its vertical position has to be measured rather than
  // inherited: line its centre up with the roundel's, in the stack's own
  // coordinates. Recomputed on every open because the roundel moves — the
  // collapsible animates, and the stack reflows on resize.
  const alignToRoundel = () => {
    const stack = document.getElementById('map-controls-br');
    if (!stack) return;
    const stackBox = stack.getBoundingClientRect();
    const pillBox = toggle.getBoundingClientRect();
    const centre = pillBox.top + pillBox.height / 2 - stackBox.top;
    flyout.style.top = `${Math.round(centre - flyout.offsetHeight / 2)}px`;
  };

  const setOpen = (open) => {
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before the flyout is unhidden, so whatever it replaces is gone by the
    // time this is on screen.
    if (open) window.pwaMapOverlays?.opening(FLYOUT_OVERLAY_NAME);
    pill.dataset.state = open ? 'expanded' : 'collapsed';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    flyout.hidden = !open;
    // After unhiding — offsetHeight is 0 while `hidden`.
    if (open) alignToRoundel();
  };

  // SNOW-658: the flyout closes whenever any other map overlay opens, and
  // closes every other one when it opens.
  //
  // The two handlers below do NOT make this redundant, and neither was doing
  // this job. The toggle's `stopPropagation` (it has to be there, or the
  // flyout's own opening click would immediately read as "outside") means
  // opening the flyout reached no OTHER surface's outside-click dismiss, so it
  // opened over an open layers menu — and every other surface's toggle stops
  // propagation for the same reason, so its own dismiss below never saw them
  // open either. The strip observer answers a third question again: it closes
  // the flyout when the collapsible group hides the roundel it is anchored to,
  // which is not another overlay opening.
  window.pwaMapOverlays?.register(FLYOUT_OVERLAY_NAME, {
    isOpen: () => !flyout.hidden,
    close: () => setOpen(false),
  });

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(flyout.hidden);
  });

  // Outside-click dismiss — a tap on the map itself, which is no overlay and
  // so announces nothing to the registry. `click`, not `pointerdown`, so a
  // step inside the flyout fires its own handler before this one can close
  // it — the same reasoning the basemap menu's dismiss carries.
  document.addEventListener('click', (e) => {
    if (flyout.hidden) return;
    if (pill.contains(e.target)) return;
    setOpen(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !flyout.hidden) {
      setOpen(false);
      toggle.focus();
    }
  });

  // The collapsible strip closing must take the flyout with it — otherwise it
  // is left floating beside a roundel that is no longer on screen. The strip
  // dispatches no event (map_controls_collapse.js only writes `data-expanded`
  // on the stack), so observe that attribute rather than inventing a bridge
  // for one listener.
  const stack = document.getElementById('map-controls-br');
  if (stack && typeof MutationObserver === 'function') {
    new MutationObserver(() => {
      if (stack.dataset.expanded !== 'true') setOpen(false);
    }).observe(stack, { attributes: true, attributeFilter: ['data-expanded'] });
  }

  // Keep it beside its roundel if the viewport changes while it is open.
  window.addEventListener('resize', () => {
    if (!flyout.hidden) alignToRoundel();
  });

  flyout.addEventListener('click', (e) => {
    const seg = e.target.closest && e.target.closest('[data-bulletins-step]');
    if (!seg) return;
    e.stopPropagation();
    if (seg.getAttribute('aria-disabled') === 'true') return;
    const step = Number(seg.dataset.bulletinsStep);
    // Same recovery the L4 toggle makes: a prior style swap can have dropped
    // the regions layers, and the choropleth is one of them.
    if (step > 0 && MAP && !MAP.getLayer('regions-fill')) {
      document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
    }
    document.dispatchEvent(new CustomEvent('snowdesk:bulletins-step', {
      detail: { step },
    }));
  });
})();
