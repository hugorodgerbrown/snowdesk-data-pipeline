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
 *   - Clicking the dismiss button or pressing Escape hides the overlay and
 *     persists the dismissed state to localStorage.
 *   - If the URL hash is '#about' on load (or when hashchange fires), the
 *     overlay is forced open regardless of the persisted state.
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

  // ---- Initial state ----

  if (hashIsAbout()) {
    // /#about forces the overlay open regardless of persisted state.
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

  // ---- Dismiss button ----
  // SNOW-486: #home-intro-dismiss carries data-action="dismiss" inside the
  // [data-overlay] #home-intro, so the click itself — the hide (attribute
  // idiom, unchanged) and the localStorage persist (data-overlay-persist=
  // "snowdesk.home.intro=dismissed") — is handled by
  // static/js/overlays.js's shared delegated handler. This only runs the
  // teardown that handler doesn't know about.

  document.addEventListener('overlay:dismissed', (event) => {
    const dismissed = event.detail && event.detail.overlay;
    if (dismissed !== overlay) return;
    overlay.setAttribute('aria-hidden', 'true');
    // Clear the hash so /#about does not re-open immediately if the user
    // presses the browser back button and forward again.
    if (hashIsAbout()) {
      history.replaceState(null, '', location.pathname + location.search);
    }
  });

  // ---- Keyboard dismiss (Escape) ----

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (overlay.hasAttribute('hidden')) return;
    hide(true);
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
