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
// SNOW-682: a failed locate is no longer swallowed silently. The original
// note here read "the map simply stays where it was, which is the least
// surprising behaviour" — that holds for an instant refusal, but not for the
// case this ticket came from: a phone in the mountains, offline, waiting on a
// cold GPS fix. There the pill looked inert for the full budget and then went
// on looking inert, and a user cannot tell "still trying" from "gave up".
// So the roundel now says both things — a busy state while a fix is in
// flight (below), and a banner with a retry when none arrives.
(function geolocateInit() {
  const btn = document.getElementById('locate-toggle');
  if (!btn || !MAP) return;

  // SNOW-682: how long the device gets to answer, for BOTH callers in this
  // module (the pill and the field-report flow's locate-request). It was
  // 15000, which is a very long time to stare at a map that is doing nothing
  // visible; five seconds is about the limit of a "it's working on it" and is
  // what the retry affordance is for. A device that can answer at all
  // normally answers well inside it — offline, the ones that can't would not
  // have made it in fifteen either, because what they are waiting for is a
  // cold GPS lock, not a slow network.
  const FIX_TIMEOUT_MS = 5000;

  const control = new maplibregl.GeolocateControl({
    trackUserLocation: false,
    showAccuracyCircle: true,
    // maximumAge lets control.trigger() reuse the fix from the bounds pre-check
    // below (taken moments earlier) instead of acquiring a second one.
    //
    // SNOW-682: `timeout` matters for the case where that reuse DOESN'T
    // happen — the control then acquires its own fix, and with no timeout it
    // can wait indefinitely while the map sits still and the pill has already
    // stopped pulsing. Bounded, its own `error` event fires instead, which is
    // now wired to the failure banner below.
    positionOptions: {
      enableHighAccuracy: true,
      maximumAge: 60000,
      timeout: FIX_TIMEOUT_MS,
    },
  });

  // SNOW-324/486: the off-map notification (#offmap-banner) renders through
  // the shared floating-banner primitive (includes/_overlay_banner.html) and
  // is a [data-overlay], so overlays.js owns the "×" hide via the class idiom.
  // It floats (fixed) on every viewport, so revealing it no longer affects the
  // map's layout — no resize needed. This module owns only the reveal and the
  // auto-dismiss timer.
  //
  // SNOW-682 added a second banner of exactly that shape (#locate-failed-
  // banner), so the reveal/hide/auto-dismiss trio below is now shared rather
  // than written twice.
  /**
   * Wrap a floating banner in a reveal, a hide, and an auto-dismiss timer.
   *
   * @param {HTMLElement|null} el The banner element, or null when the page
   *   doesn't render it — every method is then a no-op, which is what keeps
   *   this module working on a map surface that ships neither banner.
   * @param {number} timeoutMs How long the banner stays up unless hidden first.
   * @returns {{show: function(): void, hide: function(): void}}
   */
  function floatingBanner(el, timeoutMs) {
    let timer = null;

    function hide() {
      if (!el) return;
      clearTimeout(timer);
      el.classList.add('hidden');
    }

    function show() {
      if (!el) return;
      clearTimeout(timer);
      el.classList.remove('hidden');
      timer = setTimeout(hide, timeoutMs);
    }

    // If the user dismisses via the "×" (handled by overlays.js) before the
    // timeout fires, cancel the pending auto-dismiss so it can't fight a later
    // re-reveal.
    document.addEventListener('overlay:dismissed', (event) => {
      if (el && event.detail && event.detail.overlay === el) clearTimeout(timer);
    });

    return { show, hide };
  }

  const offMapBanner = floatingBanner(document.getElementById('offmap-banner'), 7000);
  // SNOW-682: "we couldn't find you — try again". Twelve seconds rather than
  // the off-map banner's seven: that one is a statement the user can read and
  // forget, this one carries a button they may want to reach for, and a CTA
  // that vanishes while it is being aimed at is worse than no CTA. The "×" and
  // a successful retry both close it sooner.
  const failedBanner = floatingBanner(document.getElementById('locate-failed-banner'), 12000);

  // Register the control at top-right so MapLibre adds its (hidden)
  // button and stands up the internal state machine. The position
  // marker and accuracy circle are added to the map canvas once a fix
  // is obtained, not at addControl time.
  MAP.addControl(control, 'top-right');

  /**
   * SNOW-682: one fix request, one answer, inside a fixed budget.
   *
   * The watchdog is not redundant with the `timeout` option, which is the
   * obvious reading. Per the spec that clock only starts once the permission
   * prompt has been ANSWERED, so a prompt left sitting on screen — the normal
   * case for a first tap — is not covered by it at all; and on a backgrounded
   * or offline mobile browser the error callback has been observed never
   * arriving. The whole point of this ticket is that the roundel stops
   * pretending to work within a known number of seconds, and only a timer we
   * own can promise that. `settled` is what keeps the two from both firing.
   *
   * @param {function(GeolocationPosition): void} onFix Called with the fix.
   * @param {function(): void} onFail Called on refusal, failure, or expiry —
   *   the three are not distinguished, because nothing downstream acts on the
   *   difference.
   * @returns {void}
   */
  function requestFix(onFix, onFail) {
    if (!navigator.geolocation) {
      onFail();
      return;
    }
    let settled = false;
    let watchdog = null;

    function settle(handler, value) {
      if (settled) return;
      settled = true;
      clearTimeout(watchdog);
      handler(value);
    }

    watchdog = setTimeout(() => settle(onFail), FIX_TIMEOUT_MS);
    navigator.geolocation.getCurrentPosition(
      (position) => settle(onFix, position),
      () => settle(onFail),
      { enableHighAccuracy: true, timeout: FIX_TIMEOUT_MS, maximumAge: 60000 }
    );
  }

  // SNOW-682: the roundel's "I'm looking" state. `data-locating` drives the
  // icon animation (static/css/map.css) and `aria-busy` says the same thing to
  // a screen reader; the flag is also the re-entrancy guard, so a second tap
  // while a fix is in flight is ignored rather than stacking a second request
  // (and a second watchdog) behind the first.
  let locating = false;

  /**
   * Enter or leave the roundel's "looking for you" state.
   *
   * @param {boolean} on
   * @returns {void}
   */
  function setLocating(on) {
    locating = on;
    btn.setAttribute('aria-busy', on ? 'true' : 'false');
    if (on) btn.dataset.locating = 'true';
    else delete btn.dataset.locating;
  }

  /** Acquire a fix and either fly to it, or say why we didn't.
   * @returns {void}
   */
  function locate() {
    if (locating) return;
    // Both outcomes below are answers to THIS tap, so neither banner should
    // still be showing an answer to the last one.
    offMapBanner.hide();
    failedBanner.hide();
    setLocating(true);
    // Check the fix against the map's bounds BEFORE handing off to
    // control.trigger(): MapLibre's GeolocateControl throws ("Unexpected
    // watchState undefined") on an out-of-maxBounds point with
    // trackUserLocation:false, and there's nowhere to fly to anyway. When the
    // user is off the map we say so; when they're on it we trigger the control
    // (which reuses this fix via maximumAge) for the fly + marker + circle.
    requestFix(
      (position) => {
        setLocating(false);
        const bounds = MAP.getMaxBounds();
        const onMap =
          !bounds ||
          bounds.contains([position.coords.longitude, position.coords.latitude]);
        if (onMap) {
          control.trigger();
        } else {
          offMapBanner.show();
        }
      },
      () => {
        // Denial, position-unavailable, or nothing at all inside the budget.
        // We deliberately don't distinguish them: the user's next move is the
        // same in every case, and "try again" is honest about a denial too —
        // tapping it re-prompts on a browser where the permission was never
        // decided, and where it was refused outright the banner is still the
        // only sign the map heard the tap.
        setLocating(false);
        failedBanner.show();
      }
    );
  }

  btn.addEventListener('click', locate);

  // The failure banner's "Try again" CTA — the same call as the roundel, which
  // is the point: the banner sits over the map at the top and the roundel is at
  // the bottom-right, so retrying should not mean hunting back down for a
  // control the banner just told you didn't work. locate() hides the banner
  // itself, so the retry replaces the old answer rather than stacking on it.
  const retryBtn = document.getElementById('locate-retry');
  if (retryBtn) retryBtn.addEventListener('click', locate);

  // The control's own geolocation errors. By the time we call
  // control.trigger() the fix is known-good and in bounds, so this only covers
  // a mid-flight failure — the map doesn't move, which SNOW-682 says out loud
  // rather than leaving as a tap that did nothing.
  control.on('error', () => {
    setLocating(false);
    failedBanner.show();
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
  //
  // SNOW-682: this path goes through the same requestFix() budget as the pill,
  // so the report sheet drops into its MANUAL ("move the map to set your
  // location") state within five seconds instead of holding "Finding your
  // location…" for fifteen. That flow has always had a graceful answer to a
  // failed fix — this only gets the user to it sooner. No banner here: the
  // sheet is already showing the user its own state, and report.js owns it.
  // The error detail no longer carries the platform's code, because a
  // watchdog expiry has none and report.js has never read it.
  document.addEventListener('snowdesk:locate-request', () => {
    requestFix(
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
      () => {
        document.dispatchEvent(new CustomEvent('snowdesk:geolocate-error', { detail: {} }));
      }
    );
  });
})();
