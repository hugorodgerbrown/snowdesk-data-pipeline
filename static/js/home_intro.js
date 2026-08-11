/**
 * static/js/home_intro.js — Homepage intro overlay behaviour (SNOW-314).
 *
 * Controls the #home-intro overlay on the homepage (/). The overlay is
 * server-rendered so it appears immediately on first paint; this script
 * wires the dismiss behaviour and the /#about hash restore.
 *
 * Behaviour:
 *   - On page load, if localStorage key 'snowdesk.home.intro' is 'dismissed'
 *     the overlay is hidden immediately (no transition to avoid flash).
 *   - Clicking the "×" (#home-intro-close) or pressing Escape hides the
 *     overlay and persists the dismissed state to localStorage.
 *   - Clicking "Explore the map" (#home-intro-dismiss) does the same, and
 *     additionally opens the map-help coachmark tour — see the "Explore the
 *     map" section below.
 *   - If the URL hash is '#about' on load (or when hashchange fires), the
 *     overlay is forced open regardless of the persisted state.
 *   - '?intro=1' (or '?intro=true') does the same on load, and is stripped
 *     from the address bar on dismissal so the panel stays dismissed across
 *     a reload. Unlike '#about' it survives a server round-trip, which makes
 *     it the handle to reach for in QA, screenshots and bug reports.
 *   - SNOW-660: a load with no '?d=' also forces it open. No day has been
 *     asked for, so the map behind it is deliberately uncoloured — the panel
 *     is what says why, and the dismiss flag stays set for the dated loads
 *     that follow.
 *   - prefers-reduced-motion: transitions are skipped by removing the
 *     animation class before any state change.
 *
 * The overlay has no backdrop — it sits inside #map so the choropleth is
 * partially visible at all times, giving a sense of what lies beneath.
 */

(function homeIntroInit() {
  'use strict';

  const overlay = document.getElementById('home-intro');
  if (!overlay) return;

  const STORAGE_KEY = 'snowdesk.home.intro';
  const DISMISSED_VALUE = 'dismissed';
  const FORCE_PARAM = 'intro';

  // Respect prefers-reduced-motion: when set, remove the CSS transition
  // class so state changes are instant rather than animated.
  const prefersReducedMotion = window.matchMedia(
    '(prefers-reduced-motion: reduce)',
  ).matches;
  if (prefersReducedMotion) {
    overlay.classList.add('home-intro--no-motion');
  }

  /**
   * Show the overlay — removes the hidden class and records the open state.
   * Does not clear the localStorage dismissed flag so a manual /#about visit
   * is a one-shot restore: the next page load is still dismissed.
   */
  const show = () => {
    overlay.removeAttribute('hidden');
    overlay.setAttribute('aria-hidden', 'false');
  };

  /**
   * Hide the overlay and, when persist=true, record the dismissed state in
   * localStorage so the overlay stays hidden on reload.
   *
   * @param {boolean} persist - Whether to write to localStorage.
   */
  const hide = (persist) => {
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('aria-hidden', 'true');
    if (persist) {
      try {
        localStorage.setItem(STORAGE_KEY, DISMISSED_VALUE);
      } catch (_) {
        // Private mode / storage full — dismissed state applies for this
        // session only; the overlay reappears on the next page load.
      }
    }
  };

  /**
   * Check whether the URL hash is '#about' (case-insensitive).
   *
   * @returns {boolean}
   */
  const hashIsAbout = () => location.hash.toLowerCase() === '#about';

  /**
   * Check whether the query string asks for the overlay to be forced open.
   *
   * SNOW-535: '?intro=1' (or '?intro=true', case-insensitive) re-opens the
   * panel regardless of the persisted dismissed state. Unlike '#about' a
   * query parameter survives a server round-trip and copy-paste into a bug
   * report, which makes it the practical handle for QA, screenshots and
   * demos once the panel has been dismissed.
   *
   * @returns {boolean}
   */
  const forceOpenRequested = () => {
    let value = null;
    try {
      value = new URLSearchParams(location.search).get(FORCE_PARAM);
    } catch (_) {
      // No URLSearchParams (very old browser) — fall back to not forcing.
      return false;
    }
    if (value === null) return false;
    const normalised = value.toLowerCase();
    return normalised === '1' || normalised === 'true';
  };

  /**
   * Whether the URL asks for no particular day (SNOW-660).
   *
   * The map no longer paints a day nobody asked for: with no `?d=` the
   * choropleth is uncoloured and the date ribbon reads "No date selected".
   * That is precisely the moment the intro earns its place back — it is the
   * panel that explains what the map is and points at the timeline — so a
   * dateless load forces it open regardless of the one-shot dismiss flag,
   * exactly as `#about` and `?intro=1` do (and, like them, without clearing
   * the flag: choosing a day and coming back leaves it dismissed).
   *
   * `?d=` is read here with `URLSearchParams` rather than via
   * `readUrlDateParam()`: this module is not part of the map bundle, so that
   * binding is not in its scope. Presence is all that matters — a malformed
   * `?d=` is a day the visitor asked for, however badly, and the map's own
   * validation decides what to do with it.
   *
   * @returns {boolean}
   */
  const noDateRequested = () => {
    try {
      return !new URLSearchParams(location.search).get('d');
    } catch (_) {
      // No URLSearchParams (very old browser) — fall back to not forcing.
      return false;
    }
  };

  /**
   * Strip the force-open parameter from the address bar, preserving any
   * other query parameters, so a dismissal is not undone by a reload or a
   * back/forward step.
   */
  const stripForceParam = () => {
    const params = new URLSearchParams(location.search);
    if (!params.has(FORCE_PARAM)) return;
    params.delete(FORCE_PARAM);
    const query = params.toString();
    history.replaceState(
      null,
      '',
      location.pathname + (query ? `?${query}` : '') + location.hash,
    );
  };

  // ---- Initial state ----

  if (hashIsAbout() || forceOpenRequested() || noDateRequested()) {
    // /#about, ?intro=1 and (SNOW-660) a load with no ?d= all force the
    // overlay open regardless of the persisted state.
    show();
  } else {
    let persisted = null;
    try {
      persisted = localStorage.getItem(STORAGE_KEY);
    } catch (_) {
      // Private mode — treat as not dismissed.
    }
    if (persisted === DISMISSED_VALUE) {
      hide(false);
    } else {
      show();
    }
  }

  // ---- Dismiss controls ("×" and "Explore the map") ----
  // SNOW-486: both #home-intro-close and #home-intro-dismiss carry
  // data-action="dismiss" inside the [data-overlay] #home-intro, so the
  // click itself — the hide (attribute idiom, unchanged) and the
  // localStorage persist (data-overlay-persist="snowdesk.home.intro=
  // dismissed") — is handled by static/js/overlays.js's shared delegated
  // handler for both buttons. This only runs the teardown that handler
  // doesn't know about.

  document.addEventListener('overlay:dismissed', (event) => {
    const dismissed = event.detail && event.detail.overlay;
    if (dismissed !== overlay) return;
    overlay.setAttribute('aria-hidden', 'true');
    // Clear the hash so /#about does not re-open immediately if the user
    // presses the browser back button and forward again.
    if (hashIsAbout()) {
      history.replaceState(null, '', location.pathname + location.search);
    }
    // Likewise drop ?intro=1, so dismissing sticks across a reload rather
    // than the parameter forcing the panel straight back open.
    stripForceParam();
  });

  // ---- "Explore the map" also opens the map-help tour ----
  // SNOW-535: unlike the "×", this CTA is an invitation to be shown around,
  // not just a dismissal — so, in addition to the shared dismiss handling
  // above, it dispatches a request event that static/js/map_help.js listens
  // for and answers by calling its own open() entry point. The tour-opening
  // logic itself stays in map_help.js; this module only asks for it.
  const exploreBtn = document.getElementById('home-intro-dismiss');
  if (exploreBtn) {
    exploreBtn.addEventListener('click', () => {
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-requested'));
    });
  }

  // ---- Keyboard dismiss (Escape) ----

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (overlay.hasAttribute('hidden')) return;
    hide(true);
    // overlays.js dispatches 'overlay:dismissed' from its delegated click
    // handler only, so the keyboard path has to announce itself — otherwise
    // the teardown above (hash clear, ?intro=1 strip) never runs and the
    // next reload forces the panel back open.
    overlay.dispatchEvent(
      new CustomEvent('overlay:dismissed', {
        bubbles: true,
        detail: { overlay: overlay },
      }),
    );
  });

  // ---- /#about hash restore ----
  // When the user navigates to /#about (e.g. from a shared link or the nav),
  // re-open the overlay without clearing the localStorage flag — it's a
  // deliberate deep-link, not a persistence override.

  window.addEventListener('hashchange', () => {
    if (hashIsAbout()) {
      show();
    }
  });
})();
