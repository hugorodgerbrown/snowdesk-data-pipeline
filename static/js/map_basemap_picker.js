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

    // `bottom` is measured from the pill (the menu's containing block) and is
    // negative downward, so this is the offset that puts the menu's lower
    // edge on the floor. Falls back to the CSS value if the pill has no box
    // yet — the menu is unhidden before this runs, but a display:none
    // ancestor would still yield zeros.
    const pillBottom = pill.getBoundingClientRect().bottom;
    if (pillBottom > 0) {
      menu.style.bottom = `${Math.round(pillBottom - bounds.floorY)}px`;
    }

    // Then the height that baseline leaves above it, so the first rows never
    // clip behind the header (SNOW-511's original point).
    menu.style.maxHeight = `${bounds.maxHeight}px`;
  };

  const setMenuOpen = (open) => {
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
    // SNOW-511/SNOW-656: place the baseline and size the menu to the visible
    // map area, once it's laid out.
    if (open) positionMenu();
  };

  // SNOW-588: let the "Manage downloads" sheet close this menu when it
  // opens over it. The menu's open state is three DOM writes held in this
  // closure; mirroring them in map_downloads_manager.js would be a
  // duplicate free to drift from the real one, so expose the setter
  // instead — the same bridge pattern pwaDownloadedOverlay uses for the
  // download controls in sibling IIFEs.
  window.pwaLayersMenu = Object.freeze({
    close() {
      setMenuOpen(false);
    },
  });

  // SNOW-511: keep the cap correct if the viewport changes while the menu is
  // open (orientation flip, mobile URL-bar show/hide, desktop resize).
  window.addEventListener('resize', () => {
    if (!menu.hidden) positionMenu();
  });

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setMenuOpen(menu.hidden);
  });

  // Outside-click dismiss. Use click (not pointerdown) so an item
  // selection inside the menu fires before this handler can close.
  document.addEventListener('click', (e) => {
    if (menu.hidden) return;
    if (pill.contains(e.target)) return;
    setMenuOpen(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) {
      setMenuOpen(false);
      toggle.focus();
    }
  });

  // SNOW-59 overlay layer ids, mirrored from the main IIFE. Each tier
  // owns a line layer (the outline, where applicable) and a symbol
  // layer (the zoom-banded label) — toggling the overlay flips both in
  // lockstep so a hidden tier never leaves an orphan label floating
  // with no boundary.
  //
  // The picker mutates layer visibility via setLayoutProperty rather
  // than reaching into the main IIFE's overlayState — the layer state
  // on the map IS the source of truth, and the localStorage key is
  // the persistence shadow.
  const OVERLAY_LAYER_IDS = {
    l1: ['major-regions-line', 'major-regions-label'],
    l2: ['sub-regions-line', 'sub-regions-label'],
    // SNOW-656: ``l4`` is the micro-region GEOGRAPHY alone now — the boundary
    // and its label. The choropleth (``regions-fill``) and the dissolved
    // bulletin boundary (``bulletin-groupings-line``) that used to ride in
    // this list are the BULLETINS row, and they are not in this table at all:
    // both are driven by map.js's applyBulletinsVisibility, because the fill
    // is hidden by opacity rather than visibility (it has to stay
    // hit-testable) and the boundary answers to a suppression the picker
    // knows nothing about. There is no ``l3`` entry for the same reason there
    // never was one.
    l4: ['regions-line', 'regions-label'],
    resorts: ['resorts-pin', 'resorts-label'],
    // SNOW-658: the 'favourites' and 'community_reports' entries went with
    // their menu rows. Both overlays are switched from the panel their own
    // roundel opens now, through window.pwaFavouritesOverlay /
    // window.pwaCommunityReportsOverlay — bridges INSIDE map.js's main IIFE,
    // which is why they need nothing here at all: the visibility loop below
    // exists only to let this separate IIFE reach layers it cannot otherwise
    // touch.
    // SNOW-645: the 'downloaded' entry that lived here (cached-tiles-fill/
    // -line) went with the layers-menu row — the overlay is now bound to
    // the "Manage downloads" sheet being open, not a togglable layer (see
    // map_downloads_manager.js's open()/close handling and
    // window.pwaDownloadedOverlay's show/hide in map.js).
    // SNOW-573: one symbol layer carries both the icon and the temp label
    // (data-driven 'icon-image'/'text-field'), unlike favourites/resorts'
    // separate pin+label layers.
    weather: ['weather-point'],
  };

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
        const next = item.getAttribute('aria-checked') !== 'true';
        item.setAttribute('aria-checked', next ? 'true' : 'false');

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
        // SNOW-573: notify telemetry when the weather overlay is flipped.
        if (overlayKey === 'weather') {
          window.pwaTelemetry?.emit('map.weather.overlay_toggled', { visible: next });
        }
        // Tier overlay — toggle layer visibility.
        writeStorage(OVERLAY_STORAGE_KEY[overlayKey], String(next));
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'resorts' || overlayKey === 'weather')) {
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
            for (const layerId of OVERLAY_LAYER_IDS[overlayKey]) {
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
// whatever is suppressing it (the downloads overlay, resort-edit mode), and
// choosing any step above 0 has to switch the downloads overlay off. All of
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
    pill.dataset.state = open ? 'expanded' : 'collapsed';
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    flyout.hidden = !open;
    // After unhiding — offsetHeight is 0 while `hidden`.
    if (open) alignToRoundel();
  };

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    setOpen(flyout.hidden);
  });

  // Outside-click dismiss. `click`, not `pointerdown`, so a step inside the
  // flyout fires its own handler before this one can close it — the same
  // reasoning the basemap menu's dismiss carries.
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
