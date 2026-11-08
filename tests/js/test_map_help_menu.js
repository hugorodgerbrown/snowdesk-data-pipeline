/*
 * tests/js/test_map_help_menu.js — Vitest unit tests for
 * static/js/map_help_menu.js (SNOW-1230).
 *
 * The "?" roundel opens a small menu now: show the welcome again, turn help
 * mode on, test 3D on this device (SNOW-1456), or go to the help pages. help mode hands the roundel's press to
 * this module as 'snowdesk:map-help-menu-requested'; these cases dispatch
 * that event directly and pin what each item asks for.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

function buildFixture() {
  document.body.innerHTML = `
    <button id="map-help-toggle" type="button">?</button>
    <div id="map-help-menu" hidden>
      <button type="button" data-help-menu-close>×</button>
      <button type="button" data-help-menu="welcome">Show me around again</button>
      <button type="button" data-help-menu="help-mode">What is everything?</button>
      <button type="button" data-help-menu="test-3d">Test 3D on this device</button>
      <button type="button" data-help-menu="relief-tuning">Tune the relief</button>
      <a href="#help" data-help-menu="pages">Help pages</a>
    </div>
    <button id="basemap-toggle" type="button">Layers</button>
  `;
}

const $ = (id) => document.getElementById(id);
const menu = () => $('map-help-menu');
const request = () => document.dispatchEvent(new CustomEvent('snowdesk:map-help-menu-requested'));

let added = [];
const realAdd = document.addEventListener.bind(document);

beforeEach(async () => {
  buildFixture();
  window.pwaTelemetry = { emit: vi.fn() };
  vi.resetModules();
  delete window.pwaMapOverlays;
  await import('../../static/js/map_overlay_exclusivity.js');
  document.addEventListener = (...args) => {
    added.push(args);
    realAdd(...args);
  };
  await import('../../static/js/map_help_menu.js');
  document.addEventListener = realAdd;
});

afterEach(() => {
  added.forEach((args) => document.removeEventListener(...args));
  added = [];
  delete window.pwaTelemetry;
});

describe('opening and closing', () => {
  it('is closed at first, and says what it controls', () => {
    expect(menu().hidden).toBe(true);
    expect($('map-help-toggle').getAttribute('aria-controls')).toBe('map-help-menu');
    expect($('map-help-toggle').getAttribute('aria-expanded')).toBe('false');
  });

  it('toggles on each press of the "?"', () => {
    request();
    expect(menu().hidden).toBe(false);
    expect($('map-help-toggle').getAttribute('aria-expanded')).toBe('true');
    request();
    expect(menu().hidden).toBe(true);
  });

  it('closes on its ×, and on Escape', () => {
    request();
    menu().querySelector('[data-help-menu-close]').click();
    expect(menu().hidden).toBe(true);

    request();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(menu().hidden).toBe(true);
  });

  it('closes when another overlay opens', () => {
    request();
    window.pwaMapOverlays.opening('basemap-menu');
    expect(menu().hidden).toBe(true);
  });
});

describe('the items', () => {
  it('"Show me around again" asks for the welcome card', () => {
    const welcome = vi.fn();
    document.addEventListener('snowdesk:map-welcome-requested', welcome);
    request();

    menu().querySelector('[data-help-menu="welcome"]').click();

    expect(welcome).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'welcome' });
    document.removeEventListener('snowdesk:map-welcome-requested', welcome);
  });

  it('"What is everything" asks for help mode', () => {
    const helpMode = vi.fn();
    document.addEventListener('snowdesk:map-help-mode-requested', helpMode);
    request();

    menu().querySelector('[data-help-menu="help-mode"]').click();

    expect(helpMode).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:map-help-mode-requested', helpMode);
  });

  it('"Test 3D on this device" asks for the 3D test\'s panel', () => {
    const test3d = vi.fn();
    document.addEventListener('snowdesk:map-3d-test-requested', test3d);
    request();

    menu().querySelector('[data-help-menu="test-3d"]').click();

    expect(test3d).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'test-3d' });
    document.removeEventListener('snowdesk:map-3d-test-requested', test3d);
  });

  it('"Tune the relief" asks for the staff tuning panel', () => {
    const tune = vi.fn();
    document.addEventListener('snowdesk:map-relief-tuning-requested', tune);
    request();

    menu().querySelector('[data-help-menu="relief-tuning"]').click();

    expect(tune).toHaveBeenCalledTimes(1);
    expect(menu().hidden).toBe(true);
    document.removeEventListener('snowdesk:map-relief-tuning-requested', tune);
  });

  it('the help pages link is recorded and closes the menu', () => {
    request();

    menu().querySelector('[data-help-menu="pages"]').click();

    expect(window.pwaTelemetry.emit).toHaveBeenCalledWith('map.help_menu.chosen', { choice: 'pages' });
    expect(menu().hidden).toBe(true);
  });
});
