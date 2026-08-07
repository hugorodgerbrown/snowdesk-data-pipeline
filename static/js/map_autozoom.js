/*
 * static/js/map_autozoom.js — the auto-zoom checkbox in the layers menu.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */


// SNOW-65: auto-zoom toggle — now a menuitemcheckbox inside the layers
// menu rather than a standalone icon button.
(function autozoomToggleInit() {
  const btn = document.getElementById('autozoom-toggle');
  if (!btn) return;

  const STORAGE_KEY = AUTOZOOM_STORAGE_KEY;

  const sync = () => {
    btn.setAttribute('aria-checked', AUTOZOOM ? 'true' : 'false');
  };

  sync(); // Reflect the value already set by the main IIFE from localStorage.

  btn.addEventListener('click', () => {
    AUTOZOOM = !AUTOZOOM;
    writeStorage(STORAGE_KEY, String(AUTOZOOM));
    sync();
  });
})();
