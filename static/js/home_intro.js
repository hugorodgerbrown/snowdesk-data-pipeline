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
 *   - A 'snowdesk:home-intro-requested' CustomEvent re-opens the panel over
 *     the map at any time, without clearing the dismissed flag. The map's
 *     "?" roundel (#map-help-toggle) dispatches it — see map_help.js. That
 *     roundel used to start the coachmark tour directly, which left a
 *     visitor who had dismissed this panel with no way back to what
 *     Snowdesk is; the tour is now what the panel's own CTA leads into.
 *   - If the URL hash is '#about' on load (or when hashchange fires), the
 *     overlay is forced open regardless of the persisted state.
 *   - '?intro=1' (or '?intro=true') does the same on load, and is stripped
 *     from the address bar on dismissal so the panel stays dismissed across
 *     a reload. Unlike '#about' it survives a server round-trip, which makes
 *     it the handle to reach for in QA, screenshots and bug reports.
 *   - prefers-reduced-motion: transitions are skipped by removing the
 *     animation class before any state change.
 *
 * The overlay has no backdrop — it sits inside #map so the choropleth is
 * partially visible at all times, giving a sense of what lies beneath.
 *
 * It is a map overlay, so it registers with window.pwaMapOverlays
 * (static/js/map_overlay_exclusivity.js) — opening it closes every other
 * map overlay, and opening any of them closes it. It could skip that while
 * the only way to see it was a fresh page load, with nothing else yet open;
 * now that the "?" roundel re-opens it over a map the visitor has been
 * using, it cannot.
 */

(function homeIntroInit() {
  'use strict';

  const overlay = document.getElementById('home-intro');
  if (!overlay) return;

  const STORAGE_KEY = 'snowdesk.home.intro';
  const DISMISSED_VALUE = 'dismissed';
  const FORCE_PARAM = 'intro';
  // This surface's name in the shared map-overlay registry — the element's
  // own id, so a failing exclusivity assertion names something greppable
  // (the convention map_help.js's OVERLAY_NAME set).
  const OVERLAY_NAME = 'home-intro';

  // Whatever held focus when the panel was re-opened from the "?" roundel,
  // so closing it hands focus back rather than dropping it on <body>. Null
  // on a first paint: the panel is simply there, nothing was displaced.
  let lastFocused = null;

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
    // Only one overlay is open over the map at a time. Announced before the
    // panel is revealed, so whatever it replaces is gone by the time it is
    // on screen. A no-op on first paint, when nothing else is open yet.
    window.pwaMapOverlays?.opening(OVERLAY_NAME);
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

  /** Whether the panel is currently on screen. */
  const isOpen = () => !overlay.hasAttribute('hidden');

  /**
   * Hand focus back to whatever held it when the panel was re-opened from
   * the "?" roundel. A no-op on the first-paint panel, where nothing was
   * displaced and moving focus would be an interruption rather than a
   * return.
   *
   * The recorded element can be gone by the time it is wanted: opening this
   * panel closes whatever else was up, so a roundel click during the
   * coachmark tour records the tour's own tooltip and then hides it.
   * Focusing a ``display: none`` element is a silent no-op that leaves
   * focus on <body>, so an element that is no longer drawn falls back to
   * the "?" roundel — the control that opened this panel, and where a
   * keyboard user would look to open it again.
   *
   * ``checkVisibility()`` where the browser has it — it answers for an
   * ancestor's ``display: none`` too, which is how the tour hides its
   * tooltip. Where it is missing (older browsers, and jsdom, which has no
   * layout) the fallback reads the element's own computed display, the
   * same pairing map_help.js's buildSteps uses.
   */
  const restoreFocus = () => {
    const target = lastFocused;
    lastFocused = null;
    if (!target) return;
    const rendered = typeof target.checkVisibility === 'function'
      ? target.checkVisibility()
      : target.isConnected && getComputedStyle(target).display !== 'none';
    if (rendered && typeof target.focus === 'function') {
      target.focus();
      return;
    }
    const toggle = document.getElementById('map-help-toggle');
    if (toggle) toggle.focus();
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

  // ---- Map-overlay registration ----
  // Closed without persisting: being displaced by another overlay is not the
  // visitor saying "I have read this", which is what the "×", Escape and the
  // CTA mean. Focus is not restored either — it belongs to the surface the
  // user just opened, not to the roundel this panel borrowed it from (the
  // same distinction map_help.js draws in its own close()).
  window.pwaMapOverlays?.register(OVERLAY_NAME, {
    isOpen: isOpen,
    close: () => {
      lastFocused = null;
      hide(false);
    },
  });

  // ---- Initial state ----

  if (hashIsAbout() || forceOpenRequested()) {
    // /#about and ?intro=1 both force the overlay open regardless of the
    // persisted state.
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
    restoreFocus();
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
      // Focus is the tour's from here: it moves focus to its own tooltip on
      // open, so the dismissal this click also triggers must not pull it
      // back to the roundel. Dropping the record is how that is said — the
      // "×" and Escape keep theirs and do return focus.
      lastFocused = null;
      document.dispatchEvent(new CustomEvent('snowdesk:map-help-requested'));
    });
  }

  // ---- Re-opening from the map's "?" roundel ----
  // map_help.js hands the roundel's click over to this panel wherever the
  // page renders one, so the tour now starts here: the panel says what
  // Snowdesk is, and "Explore the map" carries on into the walkthrough.
  // Requested rather than reached into, mirroring the direction of the
  // map-help-requested event above — each module owns its own surface.
  //
  // The dismissed flag is deliberately left alone, exactly as the /#about
  // restore leaves it: this is a one-shot re-open, not an undo of the
  // dismissal, so the next page load is still a clean map.

  document.addEventListener('snowdesk:home-intro-requested', () => {
    if (!overlay.hasAttribute('hidden')) return;
    lastFocused = document.activeElement;
    show();
    // Focus the CTA rather than the panel: it is the thing to press next,
    // and it puts a keyboard user one Enter away from the tour. Escape and
    // the "×" both hand focus back to the roundel via restoreFocus().
    if (exploreBtn) exploreBtn.focus();
  });

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
