/*
 * tests/js/test_theme_preference.js — Vitest unit tests for
 * static/js/theme_preference.js.
 *
 * The module is the write half of the theme preference: theme_head.html
 * resolves the stored key in <head> and puts the `dark` class on <html>, and
 * this module edits that key and re-applies the result. The tests are written
 * against the three-state contract rather than against localStorage alone,
 * because the defect being fixed was a stored value with nothing able to
 * change it — and the two-option version of this control would have stranded
 * "system" in exactly the same way.
 *
 * The module is an IIFE that binds at import time and returns early when
 * `[data-theme-preference]` is absent, so the fixture has to exist BEFORE the
 * import — hence `vi.resetModules()` + `await import(...)` per test, the same
 * pattern as test_home_intro.js.
 *
 * `matchMedia` is stubbed per test rather than using setup.js's always-false
 * default: half of what "system" means is what the OS is currently saying, so
 * the stub has to be steerable, and the 'system' branch needs a real
 * addEventListener to fire the OS-changed case.
 *
 * `document.documentElement` is shared across tests in a file (jsdom builds
 * one document per file, not per test), so the `dark` class is cleared in
 * beforeEach — a leaked class would silently invert the next test.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'theme';

/** Listeners registered against the prefers-color-scheme query. */
let mediaListeners = [];

/**
 * The single query object every `matchMedia()` call returns.
 *
 * One shared, mutable object rather than a fresh one per call, because the
 * module captures the query at load and reads `.matches` off it later — a new
 * object per call would make the OS setting unchangeable from a test.
 */
let mediaQuery;

/**
 * Stub `matchMedia` with a steerable prefers-color-scheme result.
 *
 * @param {boolean} prefersDark
 */
function stubMatchMedia(prefersDark) {
  mediaListeners = [];
  mediaQuery = {
    matches: prefersDark,
    addEventListener: (_event, handler) => mediaListeners.push(handler),
    removeEventListener: () => {},
  };
  vi.stubGlobal('matchMedia', () => mediaQuery);
}

/** Flip the OS appearance and notify whatever the module registered. */
function flipOsTo(prefersDark) {
  mediaQuery.matches = prefersDark;
  mediaListeners.forEach((handler) => handler());
}

/** Render the radio group as includes/_theme_preference.html emits it. */
function buildFixture() {
  document.body.innerHTML = `
    <fieldset data-theme-preference>
      <label><input type="radio" name="theme-preference" value="system" checked></label>
      <label><input type="radio" name="theme-preference" value="light"></label>
      <label><input type="radio" name="theme-preference" value="dark"></label>
    </fieldset>
  `;
}

/** Click one option, as a user would. */
function choose(value) {
  const radio = document.querySelector(`input[value="${value}"]`);
  radio.checked = true;
  radio.dispatchEvent(new Event('change', { bubbles: true }));
}

/** The value of the currently checked radio. */
function checkedValue() {
  return document.querySelector('input[name="theme-preference"]:checked')?.value;
}

/** Re-run the module's IIFE against the current fixture and <html> state. */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/theme_preference.js');
}

describe('theme_preference.js', () => {
  beforeEach(() => {
    document.documentElement.classList.remove('dark');
    document.body.innerHTML = '';
    localStorage.clear();
    vi.unstubAllGlobals();
    stubMatchMedia(false);
  });

  describe('choosing a theme', () => {
    it('pins dark, storing the key', async () => {
      buildFixture();
      await loadModule();

      choose('dark');

      expect(localStorage.getItem(STORAGE_KEY)).toBe('dark');
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('pins light, storing the key', async () => {
      document.documentElement.classList.add('dark');
      buildFixture();
      await loadModule();

      choose('light');

      expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });

    it('spells system by REMOVING the key, not storing "system"', async () => {
      // The whole three-state design rests on this: theme_head.html's
      // fall-through branch IS the system branch, so storing a third literal
      // would make the resolver treat it as "not light" — dark-ish by
      // accident — instead of deferring to the OS.
      localStorage.setItem(STORAGE_KEY, 'dark');
      document.documentElement.classList.add('dark');
      buildFixture();
      await loadModule();

      choose('system');

      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });

    it('resolves system against a dark OS', async () => {
      stubMatchMedia(true);
      localStorage.setItem(STORAGE_KEY, 'light');
      buildFixture();
      await loadModule();

      choose('system');

      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });
  });

  describe('reflecting the stored preference on load', () => {
    it.each([
      ['dark', 'dark'],
      ['light', 'light'],
      [null, 'system'],
    ])('stored %s checks the %s option', async (stored, expected) => {
      if (stored) localStorage.setItem(STORAGE_KEY, stored);
      buildFixture();

      await loadModule();

      expect(checkedValue()).toBe(expected);
    });

    it('falls back to system for a value it does not recognise', async () => {
      // Junk left by another tool, or a value from a future build. It must not
      // leave the group showing a preference nothing will honour: resolveDark
      // treats anything that is not 'light' or 'dark' as "follow the OS", and
      // the checked radio has to say the same thing.
      localStorage.setItem(STORAGE_KEY, 'sepia');
      buildFixture();

      await loadModule();

      expect(checkedValue()).toBe('system');
    });
  });

  describe('following the OS', () => {
    it('repaints when the OS flips and the preference is system', async () => {
      // No stored key — the default state, and the only one where the OS
      // still has a say. macOS switching at sunset with the page open is the
      // ordinary way to hit this.
      buildFixture();
      await loadModule();
      expect(document.documentElement.classList.contains('dark')).toBe(false);

      flipOsTo(true);

      expect(document.documentElement.classList.contains('dark')).toBe(true);
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('ignores an OS flip while a theme is pinned', async () => {
      buildFixture();
      await loadModule();
      choose('light');

      flipOsTo(true);

      expect(document.documentElement.classList.contains('dark')).toBe(false);
      expect(localStorage.getItem(STORAGE_KEY)).toBe('light');
    });
  });

  describe('degradation', () => {
    it('still applies the theme when storage refuses the write', async () => {
      // Safari in private browsing throws on setItem. Failing to persist is
      // survivable; failing to apply is not.
      buildFixture();
      await loadModule();
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new DOMException('QuotaExceededError');
      });

      expect(() => choose('dark')).not.toThrow();
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    });

    it('does nothing on a page without the control', async () => {
      document.body.innerHTML = '<main></main>';

      await expect(loadModule()).resolves.not.toThrow();
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    });
  });
});
