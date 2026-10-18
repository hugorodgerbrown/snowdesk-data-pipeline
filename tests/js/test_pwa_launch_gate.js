/*
 * tests/js/test_pwa_launch_gate.js — Vitest unit tests for the inline gate
 * in templates/includes/_pwa_launch_shell.html (SNOW-1112).
 *
 * The gate decides, before first paint, whether this load shows the launch
 * shell at all and — since SNOW-1112 — WHICH mark it shows: Android's
 * splash is the circle-cropped maskable icon, iOS's is the rounded tile,
 * and the shell has to continue whichever one the OS just drew. It also
 * gives only that one image a src, so a browser tab (which renders the
 * markup on every page and never shows it) downloads neither.
 *
 * The gate is inline on purpose (it must run before paint), so there is no
 * module to import. These tests read the script out of the template and run
 * it against a jsdom fixture of the same markup; a template edit that
 * changes the gate is exercised here with no copy to keep in step.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

const TEMPLATE = readFileSync(
  resolve(__dirname, '../../templates/includes/_pwa_launch_shell.html'),
  'utf8',
);

// The one inline <script> in the partial; its nonce is a template tag, so
// match the element by its body rather than its attributes.
const GATE_SOURCE = TEMPLATE.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];

const MARK = '/static/icons/pwa-staging/icon-maskable-512.png';
const TILE = '/static/icons/pwa-staging/icon-192.png';

function buildFixture() {
  document.documentElement.className = '';
  document.body.innerHTML = `
    <div id="pwa-launch-shell" class="pwa-launch-shell">
      <img class="pwa-launch-shell__mark" data-src="${MARK}" alt="">
      <img class="pwa-launch-shell__tile" data-src="${TILE}" alt="">
    </div>
  `;
}

/**
 * Run the gate as a given platform.
 *
 * @param {{standalone: boolean, ios: boolean}} platform
 */
function runGate({ standalone, ios }) {
  buildFixture();
  vi.stubGlobal('matchMedia', () => ({ matches: standalone && !ios }));
  if (ios) {
    // iOS WebKit is the only engine that defines navigator.standalone.
    Object.defineProperty(navigator, 'standalone', {
      value: standalone,
      configurable: true,
    });
  }
  new Function(GATE_SOURCE)();
}

const html = () => document.documentElement.classList;
const mark = () => document.querySelector('.pwa-launch-shell__mark');
const tile = () => document.querySelector('.pwa-launch-shell__tile');

afterEach(() => {
  vi.unstubAllGlobals();
  delete navigator.standalone;
  sessionStorage.clear();
  document.documentElement.className = '';
  document.body.innerHTML = '';
});

describe('the platform decides the mark', () => {
  it('continues Android with the maskable icon and fetches only it', () => {
    runGate({ standalone: true, ios: false });
    expect(html().contains('pwa-launching')).toBe(true);
    expect(html().contains('pwa-launching--ios')).toBe(false);
    expect(mark().getAttribute('src')).toBe(MARK);
    expect(tile().hasAttribute('src')).toBe(false);
  });

  it('continues iOS with the rounded tile and fetches only it', () => {
    runGate({ standalone: true, ios: true });
    expect(html().contains('pwa-launching')).toBe(true);
    expect(html().contains('pwa-launching--ios')).toBe(true);
    expect(tile().getAttribute('src')).toBe(TILE);
    expect(mark().hasAttribute('src')).toBe(false);
  });
});

describe('who sees it', () => {
  it('shows nothing and fetches nothing in a browser tab', () => {
    runGate({ standalone: false, ios: false });
    expect(html().contains('pwa-launching')).toBe(false);
    expect(mark().hasAttribute('src')).toBe(false);
    expect(tile().hasAttribute('src')).toBe(false);
  });

  it('shows only once per app session', () => {
    runGate({ standalone: true, ios: false });
    runGate({ standalone: true, ios: false });
    expect(html().contains('pwa-launching')).toBe(false);
    expect(mark().hasAttribute('src')).toBe(false);
  });
});
