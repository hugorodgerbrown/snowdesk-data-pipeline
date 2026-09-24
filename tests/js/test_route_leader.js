/*
 * tests/js/test_route_leader.js — the leader line's DOM half
 * (static/js/route_leader.js, SNOW-1019).
 *
 * Hidden with no cursor index; two stops without rail two, three with;
 * cleared when the rail closes; and the map's camera listener bound once
 * however many times a rail opens. The three surfaces' screen points are
 * stubbed — each surface's own `cursorPoint` is tested beside it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/route_cursor_core.js';
import '../../static/js/route_leader_core.js';

document.body.innerHTML = '<div id="map"></div>';

const onChange = vi.fn(() => () => {});
/** What each surface answers for its point; a test may change them. */
const points = {
  map: { x: 100, y: 50 },
  railOne: { x: 120, y: 300 },
  railTwo: null,
};
/** The rail stub's state. */
const railState = { open: false, cursor: null };

window.pwaRouteCursorMap = { point: () => points.map, onChange };
window.pwaRouteRail = {
  isOpen: () => railState.open,
  cursor: () => railState.cursor,
  cursorPoint: () => points.railOne,
};
window.pwaRouteRailTwo = { cursorPoint: () => points.railTwo };

await import('../../static/js/route_leader.js');

const svg = document.querySelector('[data-route-leader]');
const leaderPath = () => svg.querySelector('path').getAttribute('d');
const stops = () => svg.querySelectorAll('[data-route-leader-stop]');

/**
 * Open the rail on a fresh cursor, the way route_rail.js announces it.
 *
 * @returns {object} The cursor.
 */
function openRail() {
  railState.open = true;
  railState.cursor = self.pwaRouteCursorCore.createRouteCursor(20);
  document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed'));
  return railState.cursor;
}

/** Close the rail, the way route_rail.js announces it. */
function closeRail() {
  railState.open = false;
  railState.cursor = null;
  document.dispatchEvent(new CustomEvent('snowdesk:route-rail-changed'));
}

beforeEach(() => {
  points.railTwo = null;
  closeRail();
});

describe('the leader line', () => {
  it('sits inside #map and takes no pointer events', () => {
    expect(svg.parentElement.id).toBe('map');
    expect(svg.getAttribute('class')).toBe('route-leader');
  });

  it('is hidden while the cursor has no index', () => {
    openRail();
    window.pwaRouteLeader.redraw();

    expect(svg.style.display).toBe('none');
  });

  it('runs from the map to rail one when rail two is closed', () => {
    openRail().setIndex(4);
    window.pwaRouteLeader.redraw();

    expect(svg.style.display).toBe('');
    expect(leaderPath()).toBe('M100 50 C100 175, 120 175, 120 300');
    expect(stops()).toHaveLength(1);
  });

  it('runs on to rail two when it is open', () => {
    points.railTwo = { x: 140, y: 420 };
    openRail().setIndex(4);
    window.pwaRouteLeader.redraw();

    expect(leaderPath().match(/C/g)).toHaveLength(2);
    expect(stops()).toHaveLength(2);
  });

  it('is hidden when the map has no point to give', () => {
    points.map = null;
    openRail().setIndex(4);
    window.pwaRouteLeader.redraw();

    expect(svg.style.display).toBe('none');
    points.map = { x: 100, y: 50 };
  });

  it('clears when the rail closes', () => {
    openRail().setIndex(4);
    window.pwaRouteLeader.redraw();

    closeRail();

    expect(svg.style.display).toBe('none');
    expect(leaderPath()).toBe('');
    expect(stops()).toHaveLength(0);
  });

  it('binds the camera listener once across opens', () => {
    openRail();
    openRail();

    expect(onChange).toHaveBeenCalledTimes(1);
  });
});
