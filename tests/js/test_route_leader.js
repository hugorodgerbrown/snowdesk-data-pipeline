/*
 * tests/js/test_route_leader.js — the leader line's DOM half
 * (static/js/route_leader.js, SNOW-1019).
 *
 * Hidden with no cursor index; two stops, the map's dot and a notch on the
 * rail's top edge (SNOW-1065, since rail two went); hidden with no map
 * point; cleared when the rail closes; and the map's camera listener bound
 * once however many times a rail opens. Both surfaces' screen points are
 * stubbed — each surface's own point is tested beside it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/route_cursor_core.js';
import '../../static/js/route_leader_core.js';

document.body.innerHTML = '<div id="map"></div>';

const onChange = vi.fn(() => () => {});
/** What each surface answers for its point; a test may change them. */
const points = {
  map: { x: 100, y: 50 },
  rail: { x: 120, y: 300 },
};
/** The rail stub's state. */
const railState = { open: false, cursor: null };

window.pwaRouteCursorMap = { point: () => points.map, onChange };
window.pwaRouteRail = {
  isOpen: () => railState.open,
  cursor: () => railState.cursor,
  cursorPoint: () => points.rail,
};

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

  it('runs from the map to a notch on the rail', () => {
    openRail().setIndex(4);
    window.pwaRouteLeader.redraw();

    expect(svg.style.display).toBe('');
    expect(leaderPath()).toBe('M100 50 C100 175, 120 175, 120 300');
    expect(leaderPath().match(/C/g)).toHaveLength(1);
    // The notch: a triangle whose point sits on the rail's edge.
    expect(stops()).toHaveLength(1);
    expect(stops()[0].getAttribute('d')).toBe('M116 295 L124 295 L120 300 Z');
  });

  it('is hidden when the map\'s dot is covered', () => {
    // map.js answers null for a dot behind the rail; a line with one end
    // points at nothing.
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
