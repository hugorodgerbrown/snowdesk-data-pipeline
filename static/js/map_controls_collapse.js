/*
 * static/js/map_controls_collapse.js — collapsible bottom-right map control
 * group.
 *
 * Six roundels in one column read as clutter over the map. Layers, locate and
 * the toggle stay out; the other four (basemap download, favourite,
 * observations, help) collapse into the toggle.
 *
 * The toggle is the LAST child of #map-controls-br, so the bottom-anchored
 * stack grows and shrinks upward out of it: the toggle never moves under the
 * finger pressing it, and the revealed controls appear directly beneath layers
 * and locate in their existing stack order.
 *
 * The animation itself is pure CSS (static/css/map.css) driven off
 * data-expanded on the stack. This module owns only the state, the persisted
 * preference, and the child count the CSS needs to compute the open height.
 *
 * Progressive enhancement: _map_embed.html server-renders data-expanded="true",
 * so with JS disabled every control is present and reachable — the toggle
 * simply does nothing rather than trapping four controls behind it.
 *
 * A standalone module rather than another IIFE in map.js so it is reachable
 * from the Vitest + jsdom harness (map.js can't be imported there — its boot
 * IIFEs need MapLibre), matching map_layer_sync_status.js / home_intro.js.
 */

(function mapControlsCollapseInit() {
  const stack = document.getElementById('map-controls-br');
  const group = document.getElementById('map-controls-collapsible');
  const toggle = document.getElementById('map-controls-toggle');
  const inner = group && group.querySelector('.map-controls-collapsible-inner');
  if (!stack || !group || !toggle || !inner) return;

  const STORAGE_KEY = 'snowdesk.map.controls.expanded';

  // localStorage guarded by try/catch — private mode, disabled storage and
  // quota errors are all swallowed; the choice still applies for the session.
  const readStored = (dflt) => {
    try {
      const v = localStorage.getItem(STORAGE_KEY);
      return v === null ? dflt : v === 'true';
    } catch (_) {
      return dflt;
    }
  };
  const writeStored = (value) => {
    try {
      localStorage.setItem(STORAGE_KEY, String(value));
    } catch (_) {
      /* private mode — preference holds for this session only */
    }
  };

  /**
   * Publish the group's child count so the CSS can compute its open height
   * (n * --map-control-lg + (n - 1) * 8px).
   *
   * Read from the live DOM rather than hardcoded: favourite and observations
   * were both conditionally rendered in the past, and a stale count would
   * leave the group clipped short or standing open too tall.
   *
   * @returns {void}
   */
  const syncCount = () => {
    group.style.setProperty('--map-controls-count', String(inner.children.length));
  };

  /**
   * Paint `expanded` onto the stack and the toggle's ARIA state.
   *
   * The label describes what the next tap DOES rather than the current
   * state, matching the chevron, which points the way the group will move.
   *
   * @param {boolean} expanded
   * @returns {void}
   */
  const apply = (expanded) => {
    stack.dataset.expanded = String(expanded);
    toggle.setAttribute('aria-expanded', String(expanded));
    toggle.setAttribute('aria-label', expanded ? 'Hide map controls' : 'Show map controls');
  };

  // The user's own preference, distinct from the transient forced-open state
  // the help tour imposes below.
  let userExpanded = readStored(true);
  // True only while the help tour is holding the group open.
  let forcedOpen = false;

  syncCount();
  apply(userExpanded);

  toggle.addEventListener('click', () => {
    // Tapping while the tour holds the group open adopts "collapsed" as the
    // new preference, rather than appearing to do nothing and then silently
    // reverting when the tour closes.
    userExpanded = forcedOpen ? false : !userExpanded;
    forcedOpen = false;
    writeStored(userExpanded);
    apply(userExpanded);
  });

  // Several help-tour steps target controls inside this group. A collapsed
  // control still matches document.querySelector, so map_help.js's
  // absent-target filter won't drop the step and the highlight ring would be
  // positioned against a box clipped to zero height. Force the group open for
  // the duration of the tour, then hand it back to the user's preference.
  document.addEventListener('snowdesk:map-help-open', () => {
    if (userExpanded) return;
    forcedOpen = true;
    apply(true);
  });

  document.addEventListener('snowdesk:map-help-close', () => {
    if (!forcedOpen) return;
    forcedOpen = false;
    apply(userExpanded);
  });
})();
