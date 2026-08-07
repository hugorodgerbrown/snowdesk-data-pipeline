/*
 * static/js/map_geolocate.js — the locate-me roundel, wrapping a MapLibre GeolocateControl.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// SNOW-328: Locate-me roundel — wires #locate-toggle to a MapLibre
// GeolocateControl so clicking the pill one-shots a pan/zoom to the
// user's current position. The control's own DOM button is hidden via
// CSS (.maplibregl-ctrl-geolocate { display: none }) so MapLibre still
// manages the internal state machine and the blue accuracy circle, while
// our pill in the utility cluster is the only visible affordance.
//
// Permission-denied and position-unavailable errors are swallowed
// silently — the map simply stays where it was, which is the least
// surprising behaviour when the user declines or the device has no fix.
(function geolocateInit() {
  const btn = document.getElementById('locate-toggle');
  if (!btn || !MAP) return;

  const control = new maplibregl.GeolocateControl({
    trackUserLocation: false,
    showAccuracyCircle: true,
    // maximumAge lets control.trigger() reuse the fix from the bounds pre-check
    // below (taken moments earlier) instead of acquiring a second one.
    positionOptions: { enableHighAccuracy: true, maximumAge: 60000 },
  });

  // SNOW-324/486: the off-map notification (#offmap-banner) renders through
  // the shared floating-banner primitive (includes/_overlay_banner.html) and
  // is a [data-overlay], so overlays.js owns the "×" hide via the class idiom.
  // It floats (fixed) on every viewport, so revealing it no longer affects the
  // map's layout — no resize needed. map.js owns only the reveal and the 7s
  // auto-dismiss timer.
  const offMapBanner = document.getElementById('offmap-banner');
  let offMapTimer = null;

  function hideOffMapBanner() {
    if (!offMapBanner) return;
    clearTimeout(offMapTimer);
    offMapBanner.classList.add('hidden');
  }

  function showOffMapBanner() {
    if (!offMapBanner) return;
    clearTimeout(offMapTimer);
    offMapBanner.classList.remove('hidden');
    offMapTimer = setTimeout(hideOffMapBanner, 7000);
  }

  // If the user dismisses via the "×" (handled by overlays.js) before the
  // timeout fires, cancel the pending auto-dismiss so it can't fight a later
  // re-reveal.
  document.addEventListener('overlay:dismissed', (event) => {
    if (event.detail && event.detail.overlay === offMapBanner) {
      clearTimeout(offMapTimer);
    }
  });

  // Register the control at top-right so MapLibre adds its (hidden)
  // button and stands up the internal state machine. The position
  // marker and accuracy circle are added to the map canvas once a fix
  // is obtained, not at addControl time.
  MAP.addControl(control, 'top-right');

  btn.addEventListener('click', () => {
    if (!navigator.geolocation) return;
    // Check the fix against the map's bounds BEFORE handing off to
    // control.trigger(): MapLibre's GeolocateControl throws ("Unexpected
    // watchState undefined") on an out-of-maxBounds point with
    // trackUserLocation:false, and there's nowhere to fly to anyway. When the
    // user is off the map we say so; when they're on it we trigger the control
    // (which reuses this fix via maximumAge) for the fly + marker + circle.
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const bounds = MAP.getMaxBounds();
        const onMap =
          !bounds ||
          bounds.contains([position.coords.longitude, position.coords.latitude]);
        if (onMap) {
          control.trigger();
        } else {
          showOffMapBanner();
        }
      },
      () => {
        // Denial / position-unavailable — stay silent (map doesn't move),
        // matching the original locate-pill behaviour.
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });

  // Swallow the control's own geolocation errors silently — by the time we
  // call control.trigger() the fix is known-good and in bounds, so this only
  // covers a mid-flight failure; the map simply doesn't move.
  control.on('error', () => {
    // Intentionally empty — no user-facing noise on a failed locate.
  });

  // SNOW-324: answer the field-report flow's location requests. report.js
  // dispatches ``snowdesk:locate-request`` and consumes the result on
  // ``snowdesk:geolocate`` / ``snowdesk:geolocate-error`` — so map.js remains
  // the single owner of geolocation and report.js never calls the API itself.
  //
  // This deliberately does NOT go through ``control.trigger()``: the report
  // only needs coordinates, not a camera move, and MapLibre's GeolocateControl
  // throws ("Unexpected watchState undefined") when a fix lands outside the
  // map's maxBounds with trackUserLocation:false — i.e. for every location
  // outside the Alps box (a tester in the UK, say). A plain getCurrentPosition
  // is robust wherever the user is; out-of-region points then resolve to a
  // null region server-side (the "we couldn't match your location" path).
  document.addEventListener('snowdesk:locate-request', () => {
    if (!navigator.geolocation) {
      document.dispatchEvent(
        new CustomEvent('snowdesk:geolocate-error', { detail: { code: 2 } })
      );
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => {
        document.dispatchEvent(
          new CustomEvent('snowdesk:geolocate', {
            detail: {
              lat: position.coords.latitude,
              lon: position.coords.longitude,
              accuracy: position.coords.accuracy,
            },
          })
        );
      },
      (err) => {
        document.dispatchEvent(
          new CustomEvent('snowdesk:geolocate-error', {
            detail: { code: err && err.code },
          })
        );
      },
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }
    );
  });
})();
