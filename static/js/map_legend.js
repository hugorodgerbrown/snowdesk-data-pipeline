/*
 * static/js/map_legend.js — the collapsible danger-scale legend.
 *
 * SNOW-610, step 4: extracted verbatim from map.js. Runs at parse time and
 * binds its own DOM, so it loads AFTER map.js in the position it held
 * inside that file — document order is execution order for deferred
 * classic scripts. Shared state (`MAP`, `FEATURE_BY_REGION_ID`,
 * `COUNTRY_STATE`, …) comes from static/js/map_state.js; shared helpers
 * (`getSeasonRatings`, `repaintRegionsForDate`, the localStorage trio)
 * from static/js/map_shared.js. Both load first.
 */

// SNOW-38: Collapsible danger-scale legend. State persists in localStorage
// under `snowdesk.map.legend` (namespaced — distinct from the legacy flat
// `offline-map-saved` key, which is intentionally left as-is).
(function legendInit() {
  const root = document.getElementById('map-legend');
  if (!root) return;
  const toggle = document.getElementById('map-legend-toggle');
  const STORAGE_KEY = 'snowdesk.map.legend';

  function applyState(state) {
    const next = state === 'expanded' ? 'expanded' : 'collapsed';
    root.dataset.state = next;
    toggle.setAttribute('aria-expanded', next === 'expanded' ? 'true' : 'false');
  }

  const initial = readStorage(STORAGE_KEY) === 'expanded' ? 'expanded' : 'collapsed';
  applyState(initial);

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = root.dataset.state === 'expanded' ? 'collapsed' : 'expanded';
    applyState(next);
    writeStorage(STORAGE_KEY, next);
  });

  // Outside-tap dismiss: any click outside the legend container collapses
  // it. Inside-card clicks bubble harmlessly; the toggle stops propagation
  // above so its own click is not treated as "outside".
  document.addEventListener('click', (e) => {
    if (root.dataset.state !== 'expanded') return;
    if (root.contains(e.target)) return;
    applyState('collapsed');
    writeStorage(STORAGE_KEY, 'collapsed');
  });
})();
