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
  const MENU_TOP_GAP = 8;
  const clampMenuHeight = () => {
    const mapEl = document.getElementById('map');
    if (!mapEl) return;
    const mapTop = mapEl.getBoundingClientRect().top;
    const menuBottom = menu.getBoundingClientRect().bottom;
    const available = Math.max(0, Math.round(menuBottom - mapTop - MENU_TOP_GAP));
    menu.style.maxHeight = `${available}px`;
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
    // SNOW-511: size the menu to the visible map area once it's laid out.
    if (open) clampMenuHeight();
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
    if (!menu.hidden) clampMenuHeight();
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
    favourites: ['favourites-pin', 'favourites-label'],
    community_reports: [
      'community-reports-clusters',
      'community-reports-cluster-count',
      'community-reports-point',
    ],
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

        // SNOW-656: the Bulletins row delegates wholesale, the same way
        // country.* does below. Its state is not a plain "is this layer
        // visible" — it is a persisted preference AND-ed with whatever is
        // currently suppressing it (the downloads overlay, resort-edit mode),
        // and turning it on has to switch the downloads overlay off. All of
        // that lives in the main IIFE, so this handler contributes the click
        // and nothing else. It does not even write localStorage, unlike every
        // branch below: the state machine owns the value that gets stored.
        //
        // The optimistic aria-checked write above is correct for this row
        // too — ``choose`` clears the downloads suppression whenever the new
        // preference is on, so the effective value the main IIFE writes back
        // always equals ``next``.
        if (overlayKey === 'bulletins') {
          // Same recovery as L4's below: a prior style swap can have dropped
          // the regions layers, and this row's fill is one of them.
          if (next && MAP && !MAP.getLayer('regions-fill')) {
            document.dispatchEvent(new CustomEvent('snowdesk:regions-reinstall'));
          }
          document.dispatchEvent(new CustomEvent('snowdesk:bulletins-toggle', {
            detail: { next },
          }));
          return;
        }

        // SNOW-172: handle country.* toggles by delegating to the main IIFE
        // via a CustomEvent. countryState / ensureCountryLoaded / applyCountryFilters
        // are all scoped to the main IIFE and are not accessible here.
        if (overlayKey.startsWith('country.')) {
          const code = overlayKey.slice(8); // 'country.fr' → 'fr'
          document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
            detail: { code, next },
          }));
          return;
        }

        // SNOW-414: notify telemetry when the favourites overlay is flipped.
        if (overlayKey === 'favourites') {
          window.pwaTelemetry?.emit('map.favourite.overlay_toggled', { visible: next });
        }
        // SNOW-419: notify telemetry when the community-reports overlay is
        // flipped.
        if (overlayKey === 'community_reports') {
          window.pwaTelemetry?.emit('map.community_reports.overlay_toggled', { visible: next });
        }
        // SNOW-573: notify telemetry when the weather overlay is flipped.
        if (overlayKey === 'weather') {
          window.pwaTelemetry?.emit('map.weather.overlay_toggled', { visible: next });
        }
        // Tier overlay — toggle layer visibility.
        writeStorage(OVERLAY_STORAGE_KEY[overlayKey], String(next));
        if (MAP) {
          if (next && (overlayKey === 'l1' || overlayKey === 'l2' || overlayKey === 'resorts' || overlayKey === 'favourites' || overlayKey === 'community_reports' || overlayKey === 'weather')) {
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
          // SNOW-499: bridge to the main IIFE so the resort layer's
          // favourited-resort exclusion is recomputed against the new
          // favourites visibility — otherwise a favourited resort stays
          // hidden (no dot, no star) when the overlay is switched off.
          // Dispatched for both directions; the toggle-on lazy path is
          // also covered by the overlay-load handler once its fetch settles.
          if (overlayKey === 'favourites') {
            document.dispatchEvent(
              new CustomEvent('snowdesk:favourites-visibility-changed'),
            );
          }
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
