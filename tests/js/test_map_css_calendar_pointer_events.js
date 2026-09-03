/*
 * tests/js/test_map_css_calendar_pointer_events.js — source-text guard for the
 * date picker's pointer events inside the map's bottom-left stack.
 *
 * .map-legend is `pointer-events: none`, because setting `right` on it turned
 * the stack from a content-width row into a box the width of the map, and
 * every tap in that strip was hitting it instead of MapLibre. Its controls
 * take their events back one selector at a time — and .map-calendar, moved
 * into that stack by SNOW-794, was not one of them.
 *
 * The failure looks like a broken feature, not a layout bug. The open card
 * paints normally; every click inside it passes THROUGH to #map, so the month
 * arrows and the day cells never fire, MapLibre handles the click, and
 * map_calendar.js's outside-tap handler sees a target outside the card and
 * shuts it. What a visitor sees is a calendar that opens and then closes on
 * any press — with no console error, and with every server-side check green.
 *
 * A source-text assertion, not a runtime one, for the reason its neighbour
 * test gives: jsdom does no layout and no hit testing, so there is nothing to
 * measure. What is checked is that the card states an answer for its open
 * state at all, and that it is the UNGATED one — .map-legend-card's
 * `[data-state="expanded"]` gating is right for a panel that stays displayed
 * and merely goes transparent, and wrong here.
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

describe('the date picker inside the pointer-events: none control stack', () => {
  it('is the stack it lives in, so the inherited value is none', () => {
    // The premise the rest of this file rests on. If .map-legend ever stops
    // being `none`, the assertion below is no longer load-bearing and this
    // test should be read again rather than kept passing.
    expect(ruleBody(/^\.map-legend \{/m)).toMatch(/pointer-events:\s*none;/);
  });

  it('takes its pointer events back, so clicks reach the arrows and the days', () => {
    expect(ruleBody(/^\.map-calendar \{/m)).toMatch(/pointer-events:\s*auto;/);
  });

  it('does so unconditionally — it is display:none when shut, not transparent', () => {
    // .map-legend-card needs its `auto` gated on [data-state="expanded"]
    // because it is always displayed; an ungated rule there leaves an
    // invisible panel swallowing clicks over a third of the map. This card
    // is `hidden` when shut, so a gate would only be a second state to keep
    // in step with the [hidden] rule.
    expect(ruleBody(/^\.map-calendar\[hidden\] \{/m)).toMatch(/display:\s*none;/);
    expect(MAP_CSS).not.toMatch(/\[data-state="[a-z]+"\]\s+\.map-calendar\s*\{/);
  });
});
