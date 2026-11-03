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
 *
 * Two tiers (3 Nov 2026). The card opens on "On the map now": a key for
 * what is drawn in the current view, worked out by
 * static/js/map_legend_core.js from what this module asks the map —
 * queryRenderedFeatures over the viewport, and the bridges map.js
 * publishes. "All keys & sources" is the card's second page. Four keys
 * (problem terrain, Steep terrain, route legs, downloaded maps) are also
 * switched by their own module through `hidden`; this one never writes
 * that attribute on them, only data-legend-out-of-view, and reads it.
 * Worked out only while the card is open: a closed card costs a pan
 * nothing.
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

  // ---- The two tiers -----------------------------------------------------

  const card = document.getElementById('map-legend-card');
  const core = self.pwaMapLegendCore;

  /**
   * Show one of the card's two pages, and move focus to the control that
   * leads back when the reader moved it there.
   *
   * @param {string} page 'now' or 'all'.
   * @param {boolean} [focus] Whether to move focus with the page.
   * @returns {void}
   */
  function showPage(page, focus) {
    if (!card) return;
    const next = page === 'all' ? 'all' : 'now';
    card.dataset.legendPage = next;
    for (const panel of card.querySelectorAll('[data-legend-panel]')) {
      panel.hidden = panel.getAttribute('data-legend-panel') !== next;
    }
    card.scrollTop = 0;
    if (focus) {
      const back = card.querySelector(
        next === 'all' ? '[data-legend-go="now"]' : '[data-legend-go="all"]',
      );
      if (back instanceof HTMLElement) back.focus();
    }
  }

  /**
   * Set or clear a boolean attribute, writing only on a change — the
   * observer below watches `hidden`, and a write of the same value is
   * still a mutation.
   *
   * @param {Element} el
   * @param {string} name
   * @param {boolean} on
   * @returns {void}
   */
  function flag(el, name, on) {
    if (el.hasAttribute(name) === on) return;
    if (on) el.setAttribute(name, '');
    else el.removeAttribute(name);
  }

  /**
   * The map's features for a group of layers inside the viewport. A layer
   * that is not in the style, or is switched off, contributes nothing.
   *
   * @param {any} map
   * @param {readonly string[]} ids
   * @returns {any[]}
   */
  function renderedIn(map, ids) {
    const live = ids.filter(
      (id) => map.getLayer(id) && map.getLayoutProperty(id, 'visibility') !== 'none',
    );
    if (!live.length) return [];
    try {
      return map.queryRenderedFeatures({ layers: live });
    } catch {
      // A style mid-swap can name a layer it has not finished adding.
      return [];
    }
  }

  /**
   * Whether a module-switched key section is on (its own `hidden`).
   *
   * @param {string} key
   * @returns {boolean}
   */
  function sectionOn(key) {
    const el = card && card.querySelector(`[data-legend-key="${key}"]`);
    return !!el && !el.hidden;
  }

  /**
   * Ask the map what the current view draws.
   *
   * @returns {any}
   */
  function probeView() {
    const map = typeof MAP !== 'undefined' ? MAP : null;
    /** @type {Object<string, any[]>} */
    const rendered = {};
    if (map && typeof map.getLayer === 'function' && typeof map.queryRenderedFeatures === 'function') {
      for (const [key, ids] of Object.entries(core.LAYERS)) {
        rendered[key] = renderedIn(map, ids);
      }
    }
    // Both terrain layers paint only from z12, inside the survey: a key
    // for a layer that is on but cannot draw here is a key for nothing.
    const terrain = window.pwaTerrainFilter;
    const paints = !terrain || typeof terrain.availability !== 'function'
      || terrain.availability() === 'ok';
    const bulletins = window.pwaBulletinsLayer;
    return {
      bulletins: !!(bulletins && bulletins.isVisible()),
      problemTerrain: sectionOn('problem_terrain') && paints,
      terrainFilter: sectionOn('terrain_filter') && paints,
      routeLegsOn: sectionOn('routes'),
      downloads: sectionOn('downloads'),
      rendered,
    };
  }

  /** The three providers, named in the first tier's credit with their key. */
  const AVALANCHE_SOURCES = 'SLF · ALBINA · Météo-France';

  /**
   * Bring the first tier, the second tier's "On" marks and the one-line
   * credit into line with the view.
   *
   * @returns {void}
   */
  function refresh() {
    if (!card || !core || !isExpanded()) return;
    const state = core.legendState(probeView());
    let shown = 0;
    for (const el of card.querySelectorAll('[data-legend-key]')) {
      const key = el.getAttribute('data-legend-key') || '';
      flag(el, 'data-legend-out-of-view', !state.keys[key]);
      if (state.keys[key] && !el.hidden) shown += 1;
    }
    for (const el of card.querySelectorAll('[data-legend-item]')) {
      const item = el.getAttribute('data-legend-item') || '';
      flag(el, 'data-legend-out-of-view', state.items[item] === false);
    }
    for (const el of card.querySelectorAll('[data-legend-on]')) {
      flag(el, 'hidden', !state.on[el.getAttribute('data-legend-on') || '']);
    }
    const empty = card.querySelector('[data-legend-empty]');
    if (empty) flag(empty, 'hidden', shown > 0);

    const sources = card.querySelector('[data-legend-sources]');
    if (sources) {
      const tiles = document.getElementById('map-attribution-text');
      const parts = [core.shortSources([tiles ? tiles.innerHTML : ''])];
      if (state.keys.danger || state.keys.problem_terrain) parts.push(AVALANCHE_SOURCES);
      const text = parts.filter(Boolean).join(' · ');
      if (sources.textContent !== text) sources.textContent = text;
    }
  }

  let refreshQueued = false;
  /** Refresh once on the next frame, however many changes asked. */
  function scheduleRefresh() {
    if (refreshQueued || !isExpanded()) return;
    refreshQueued = true;
    requestAnimationFrame(() => {
      refreshQueued = false;
      refresh();
    });
  }

  let mapBound = false;
  /** Follow the camera: a pan or a zoom settles into an `idle`. */
  function bindMap() {
    if (mapBound) return;
    const map = typeof MAP !== 'undefined' ? MAP : null;
    if (!map || typeof map.on !== 'function') return;
    mapBound = true;
    map.on('idle', scheduleRefresh);
  }

  if (card) {
    card.addEventListener('click', (e) => {
      const go = e.target instanceof Element && e.target.closest('[data-legend-go]');
      if (!go) return;
      showPage(go.getAttribute('data-legend-go') || 'now', true);
    });
    // A module switching one of the four keys on or off.
    if (typeof MutationObserver === 'function') {
      const observer = new MutationObserver(scheduleRefresh);
      for (const el of card.querySelectorAll('[data-legend-key]')) {
        observer.observe(el, { attributes: true, attributeFilter: ['hidden'] });
      }
    }
  }
  for (const name of [
    'snowdesk:overlay-visibility-changed',
    'snowdesk:date-changed',
    'snowdesk:map-mode-changed',
    'snowdesk:problem-terrain-changed',
    'snowdesk:terrain-filter-changed',
  ]) {
    document.addEventListener(name, scheduleRefresh);
  }

  const initial = readStorage(STORAGE_KEY) === 'expanded' ? 'expanded' : 'collapsed';
  applyState(initial);
  bindMap();
  scheduleRefresh();

  toggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const next = isExpanded() ? 'collapsed' : 'expanded';
    // SNOW-658: only one overlay is open over the map at a time. Announced
    // before the card is revealed, so whatever it replaces is gone by the
    // time this is on screen.
    // SNOW-1196: on a desktop the card opens beside the left-column sheet,
    // so it leaves that sheet open there.
    if (next === 'expanded') {
      window.pwaMapOverlays?.opening(OVERLAY_NAME, { spareObjectSheets: 'wide' });
      // The card always opens on its first page.
      showPage('now');
    }
    setState(next);
    if (next === 'expanded') {
      bindMap();
      refresh();
    }
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
