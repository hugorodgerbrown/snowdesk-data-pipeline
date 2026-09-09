/**
 * static/js/pwa_launch_shell.js — Dismiss the PWA launch shell (SNOW-878).
 *
 * The overlay (templates/includes/_pwa_launch_shell.html) continues the
 * OS launch screen into the page so an installed app boots on one
 * unbroken splash. This module's entire job is getting rid of it at the
 * right moment.
 *
 * WHETHER IT SHOWS AT ALL IS NOT DECIDED HERE. That is the inline gate
 * in the partial, which runs before first paint and adds
 * `pwa-launching` to <html> only for a cold launch of the installed
 * app. This module runs deferred — after paint — so it is structurally
 * incapable of preventing a flash, and does not try.
 *
 * THREE SIGNALS, FIRST ONE WINS
 * -----------------------------
 * `snowdesk:map-ready` is the one that matters: the map page is the
 * start_url, and it is the page with a gap worth covering — chrome
 * paints long before MapLibre has a style and tiles. map.js dispatches
 * it at the top of its `load` handler.
 *
 * `window.load` covers every other page (which have nothing to wait
 * for) and the map page in the states where the map event never comes:
 * MapLibre failing to initialise, WebGL unavailable, the style request
 * hanging offline.
 *
 * The timeout covers what neither does. `load` can be indefinitely
 * deferred by a single slow subresource, and a splash that outlives the
 * user's patience is worse than no splash: they installed an app that
 * appears to hang on launch. Five seconds is longer than any healthy
 * boot on the connections this app is used on, and short enough that a
 * user still reads it as slow rather than broken.
 *
 * A fourth backstop lives outside JavaScript entirely: a failsafe
 * keyframe in src/css/main.css hides the overlay at ten seconds whether
 * or not this file ever parsed. Everything here is an optimisation on
 * top of that guarantee — which is the right way round for code whose
 * failure mode is covering the whole app.
 */

(function () {
  'use strict';

  const SHELL_ID = 'pwa-launch-shell';
  const LAUNCHING_CLASS = 'pwa-launching';
  const DONE_CLASS = 'pwa-launch-shell--done';

  // Hard deadline from this module's execution, in ms. See the header:
  // shorter than the CSS failsafe, which only exists for the case where
  // this file never runs.
  const TIMEOUT_MS = 5000;

  // Must cover the .pwa-launch-shell--done opacity transition in
  // src/css/main.css (300ms). The node is removed on `transitionend`
  // normally; this is the fallback for the cases where that event never
  // fires — a hidden tab, or prefers-reduced-motion, where the rule sets
  // `transition: none` and there is no transition to end.
  const FADE_MS = 400;

  const shell = document.getElementById(SHELL_ID);
  if (!shell) return;

  // Not a launch — a browser tab, or a second navigation inside an app
  // session. The overlay is display:none in that state, so this is
  // tidiness rather than a fix: an element nothing will ever show has no
  // business staying in the document, carrying a failsafe animation and
  // an aria-live region into every page.
  if (!document.documentElement.classList.contains(LAUNCHING_CLASS)) {
    shell.remove();
    return;
  }

  let dismissed = false;

  /**
   * Fade the overlay out and take it out of the document.
   *
   * Idempotent: three signals race to call this and all three are
   * allowed to fire. The removal is scheduled twice on purpose — on
   * `transitionend`, and on a timer sized to outlast the transition —
   * because `transitionend` does not fire for a transition that never
   * started, which is the normal case under prefers-reduced-motion and
   * in a backgrounded tab.
   */
  function dismiss() {
    if (dismissed) return;
    dismissed = true;
    shell.classList.add(DONE_CLASS);
    const remove = () => shell.remove();
    shell.addEventListener('transitionend', remove, { once: true });
    window.setTimeout(remove, FADE_MS);
  }

  // Which signal ends the splash depends on what the page is waiting
  // for, and `window.load` is the WRONG answer on the map page: MapLibre
  // fetches its style and tiles with fetch(), which does not hold `load`
  // back, so `load` reliably fires while #map is still empty. Dismissing
  // there would hand the user the exact blank frame this feature exists
  // to cover — a splash that gets out of the way just too early is worse
  // than none, because it looks like the app failed rather than like it
  // is loading.
  //
  // So a page WITH a map waits for the map (or the timeout), and a page
  // without one — every other page, none of which has anything to wait
  // for beyond its own subresources — waits for `load`.
  if (document.getElementById('map')) {
    document.addEventListener('snowdesk:map-ready', dismiss, { once: true });
  } else if (document.readyState === 'complete') {
    // `load` has already fired — adding a listener now would wait
    // forever.
    dismiss();
  } else {
    window.addEventListener('load', dismiss, { once: true });
  }

  window.setTimeout(dismiss, TIMEOUT_MS);
})();
