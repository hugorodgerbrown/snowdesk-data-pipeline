/*
 * tests/js/_route_rail_stub.js — a recording stand-in for
 * `window.pwaRouteRail` (static/js/route_rail.js, SNOW-1018), for the
 * suites that boot map.js.
 *
 * Since SNOW-1018 a tap on a saved route opens rail one and nothing else;
 * the route detail sheet is reached through the rail's menu, which calls
 * the `details` function map.js handed the rail. These suites test map.js's
 * half of that contract — what it hands over, and what the `details` call
 * then opens — so they need the rail's surface and not its DOM, which
 * tests/js/test_route_rail.js covers against the real module.
 *
 * Installed BEFORE the bundle boots, the way the page loads route_rail.js
 * ahead of map.js's first tap.
 */

/**
 * Install the stub and return its handle.
 *
 * @returns {{
 *   state: {open: boolean, calls: Array<{feature: object, options: object}>},
 *   element: HTMLElement,
 *   last: function(): ?{feature: object, options: object},
 *   openDetails: function(): *,
 *   reset: function(): void,
 * }}
 */
export function installRouteRailStub() {
  const state = { open: false, calls: [] };
  const element = document.createElement('section');
  element.id = 'route-rail-stub';
  window.pwaRouteRail = {
    open(feature, options) {
      state.open = true;
      state.calls.push({ feature, options: options || {} });
      return true;
    },
    close() {
      state.open = false;
    },
    isOpen: () => state.open,
    cursor: () => null,
    element,
  };
  return {
    state,
    element,
    last: () => state.calls.at(-1) || null,
    // What a press on the rail's "Terrain and bulletin" item does.
    openDetails: () => state.calls.at(-1).options.details(),
    reset: () => {
      state.open = false;
      state.calls.length = 0;
    },
  };
}
