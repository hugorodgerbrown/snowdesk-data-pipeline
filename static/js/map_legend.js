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
 *
 * SNOW-658: the card is a map overlay like any other, so it registers with
 * window.pwaMapOverlays (static/js/map_overlay_exclusivity.js) — see the
 * note beside the registration for why its own outside-tap dismiss could
 * not already be doing that job.
 */

// SNOW-38: Collapsible danger-scale legend. State persists in localStorage
// under `snowdesk.map.legend` (namespaced — distinct from the legacy flat
// `offline-map-saved` key, which is intentionally left as-is).
(function legendInit() {
  const root = document.getElementById('map-legend');
  if (!root) return;
  const toggle = document.getElementById('map-legend-toggle');
  const STORAGE_KEY = 'snowdesk.map.legend';

  // SNOW-658: this surface's name in the shared map-overlay registry. The
  // overlay is the CARD (#map-legend-card) — #map-legend is the cluster that
  // also holds the (i) toggle and the date ribbon, both of which stay on
  // screen when the card collapses — so the card's id is the greppable name
  // a failing exclusivity assertion should print. The expanded/collapsed
  // state itself lives on the cluster, because the CSS that reveals the card
  // is written as a descendant rule (`.map-legend[data-state="expanded"]
  // .map-legend-card`), which is what `isOpen` below has to read.
  const OVERLAY_NAME = 'map-legend-card';

  /**
   * Reflect a state onto the DOM, normalising anything unrecognised to
   * collapsed.
   *
   * @param {string} state Either 'expanded' or 'collapsed'.
   * @returns {void}
   */
  function applyState(state) {
    const next = state === 'expanded' ? 'expanded' : 'collapsed';
    root.dataset.state = next;
    toggle.setAttribute('aria-expanded', next === 'expanded' ? 'true' : 'false');
  }

  /** @returns {boolean} Whether the card is currently on screen. */
  function isExpanded() {
    return root.dataset.state === 'expanded';
  }

  /**
   * Apply a state and remember it, so the card comes back the way the user
   * left it. Every close path persists, including the registry's — a card
   * that another overlay displaced should not reappear over that overlay on
   * the next load.
   *
   * @param {string} state Either 'expanded' or 'collapsed'.
   * @returns {void}
   */
  function setState(state) {
    applyState(state);
    writeStorage(STORAGE_KEY, state);
  }

  const initial = readStorage(STORAGE_KEY) === 'expanded' ? 'expanded' : 'collapsed';
  applyState(initial);

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = isExpanded() ? 'collapsed' : 'expanded';
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before the card is revealed, so whatever it replaces is gone by the
    // time this is on screen.
    if (next === 'expanded') window.pwaMapOverlays?.opening(OVERLAY_NAME);
    setState(next);
  });

  // SNOW-658: the card closes whenever any other map overlay opens, and
  // closes every other one when it opens.
  //
  // The outside-tap dismiss below does NOT make this redundant, in either
  // direction. Inbound: a surface opened without a click on the document —
  // the downloads sheet from its roundel's own handler, the anchored detail
  // popup from a MapLibre canvas event — reaches no document listener at
  // all, so the card stayed up behind it. Outbound: the toggle's
  // `stopPropagation` above (it has to be there, or the card's own opening
  // click would immediately read as "outside") means expanding the card was
  // invisible to every OTHER surface's identical outside-click handler, so
  // it opened over an open layers menu. The two handlers answer different
  // questions — "did the user click away from me" is not "did another
  // overlay open" — which is exactly why pairwise hand-wiring kept missing
  // cases.
  window.pwaMapOverlays?.register(OVERLAY_NAME, {
    isOpen: isExpanded,
    close: () => setState('collapsed'),
  });

  // Outside-tap dismiss: any click outside the legend container collapses
  // it. Inside-card clicks bubble harmlessly; the toggle stops propagation
  // above so its own click is not treated as "outside".
  document.addEventListener('click', (e) => {
    if (!isExpanded()) return;
    if (root.contains(e.target)) return;
    setState('collapsed');
  });
})();
