/*
 * tests/js/test_map_css_bottom_left_clearance.js — source-text guard for the
 * welcome panel's clearance over the map's bottom-left control stack.
 *
 * #home-intro is anchored to the map's top-left and bounded at the bottom.
 * Until the date control joined the bottom-left stack (SNOW-794) that bound
 * could be the bare edge inset, because the corner below the card held only
 * the (i) legend toggle. It now holds three control-height members — the
 * help roundel, the legend toggle and the date row — and on a short viewport
 * (a docked preview pane, or a laptop with the off-season banner up) a card
 * bounded at the map's own bottom edge lands straight on top of them.
 *
 * The failure is silent and reads as a broken feature rather than a layout
 * bug: the card wins the hit test, so tapping the date pill does nothing at
 * all — no console error, no visible overlap once the card's copy has
 * scrolled, and the calendar simply never opens. It is worth a guard for the
 * same reason the roundel-fill fallback next door is: nothing else fails
 * when this regresses.
 *
 * A source-text assertion, not a runtime one, for that file's own reason —
 * jsdom does no layout, so there is no geometry to measure. What is checked
 * is the correspondence the fix rests on: the stack's height is a token on
 * #map, beside the offset the stack's own members are positioned from, and
 * the card's bottom bound reads it. A future edit that hardcodes a figure,
 * or reverts to the bare inset, fails here.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

const MAP_CSS = readFileSync(join(process.cwd(), 'static', 'css', 'map.css'), 'utf8');

/**
 * The body of the first rule whose selector matches, comments included.
 *
 * @param {RegExp} selector Anchored at the rule's opening brace.
 * @returns {string}
 */
function ruleBody(selector) {
  const match = selector.exec(MAP_CSS);
  expect(match, `no rule matching ${selector}`).not.toBeNull();
  const start = MAP_CSS.indexOf('{', match.index);
  return MAP_CSS.slice(start, MAP_CSS.indexOf('\n}', start));
}

describe('the bottom-left stack clearance token', () => {
  it('is defined on #map, from the same control-height token the stack uses', () => {
    // On #map rather than on .map-legend, which is where the stack's other
    // shared figure lives: .map-legend is a SIBLING of #home-intro, so a
    // custom property set there does not inherit to it. #map is the nearest
    // common ancestor, and already carries --map-bottom-row-offset.
    const body = ruleBody(/^#map \{/m);
    expect(body).toMatch(
      /--map-bottom-left-stack:\s*calc\(3 \* var\(--map-control-sm\) \+ 16px\)/,
    );
  });

  it('is what bounds the welcome panel, so the card cannot cover the stack', () => {
    const body = ruleBody(/^\.home-intro \{/m);
    expect(body).toMatch(/--map-bottom-left-stack/);
    // The bare inset is what the card used to stop at, and is exactly the
    // regression this guards: it clears the map's edge but not the controls
    // standing on it.
    expect(body).not.toMatch(/bottom:\s*var\(--map-edge-inset-bottom\);/);
  });
});
