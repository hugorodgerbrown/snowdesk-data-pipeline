/**
 * static/js/theme_preference.js — theme preference control behaviour.
 *
 * The write half of the theme preference, bound to the radio group in
 * `includes/_theme_preference.html` on the settings page.
 * `templates/includes/theme_head.html` has read the `theme` localStorage key
 * since the dark-mode plumbing landed, but the setter that shipped with it
 * (`window.__setTheme`) was removed as dead code in SNOW-615 because the
 * toggle it was built for never existed. A value stored from a console call
 * therefore survived forever with nothing able to change it.
 *
 * Three preferences, two of them stored:
 *
 *   'dark'   → key set to 'dark'    → always dark
 *   'light'  → key set to 'light'   → always light
 *   'system' → key REMOVED          → follows prefers-color-scheme
 *
 * "System" is the absence of the key rather than a third stored literal, so
 * this module and theme_head.html cannot disagree about what it means: the
 * resolver's existing fall-through IS the system branch.
 *
 * ## The one duplicated line
 *
 * `resolveDark()` mirrors the expression in theme_head.html. That is a real
 * duplication and worth naming rather than hiding: theme_head.html has to be
 * an inline, parser-blocking script in <head> (an external file would either
 * add a render-blocking request or run too late to prevent a flash of the
 * wrong theme), so it cannot import anything, and this module cannot call
 * into it. Publishing a function on `window` from a template was the
 * alternative and is worse — bin/js-globals-lint only counts assignments in
 * static/js, so a global assigned in a template reads here as an unknown
 * global and the fix would be to add it to KNOWN_EXTERNALS, which defeats
 * that guard for a name that genuinely is ours.
 *
 * Keep the two in step. The rule is one boolean expression and it is quoted
 * in theme_head.html's own comment.
 *
 * ## Live OS switching
 *
 * While the preference is 'system', a change to the OS setting repaints the
 * page immediately. macOS and iOS switch appearance on a schedule, so the
 * common way to see this is to have the site open at sunset — without the
 * listener "System" would only take effect on the next navigation, which is
 * not what the option says it does.
 *
 * Every localStorage call is wrapped: Safari in private browsing and any
 * browser with site data blocked throw on access rather than returning null.
 * A theme that fails to persist still applies for this page view, which is
 * strictly better than a script that dies before applying it.
 */

(function themePreferenceInit() {
  'use strict';

  const fieldset = document.querySelector('[data-theme-preference]');
  if (!fieldset) return;

  const STORAGE_KEY = 'theme';
  const SYSTEM = 'system';
  const DARK = 'dark';
  const LIGHT = 'light';

  const root = document.documentElement;
  const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

  /**
   * Resolve a stored preference to a boolean "is dark".
   *
   * Mirrors templates/includes/theme_head.html — see the note above. Any
   * value that is not exactly 'dark' or 'light' (including null, and
   * including junk left by another tool) follows the OS, which is what
   * makes a corrupted key harmless rather than sticky.
   *
   * @param {?string} stored The raw localStorage value, or null.
   * @returns {boolean}
   */
  const resolveDark = (stored) =>
    stored === DARK || (stored !== LIGHT && darkQuery.matches);

  /**
   * Read the stored preference, treating unreadable storage as unset.
   *
   * @returns {?string}
   */
  const readStored = () => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch (_e) {
      return null;
    }
  };

  /**
   * Persist a preference, or clear the key for 'system'.
   *
   * Silent when storage refuses the write — see the module note.
   *
   * @param {string} choice One of 'system', 'light', 'dark'.
   */
  const store = (choice) => {
    try {
      if (choice === SYSTEM) {
        localStorage.removeItem(STORAGE_KEY);
      } else {
        localStorage.setItem(STORAGE_KEY, choice);
      }
    } catch (_e) {
      // Storage unavailable. The theme still applies to this page view.
    }
  };

  /**
   * Apply a preference to <html>, repainting the page.
   *
   * The dark theme is only a `.dark {}` block of custom-property overrides
   * in main.css — there are no `dark:` utilities anywhere in this project —
   * so toggling the class restyles everything in one recalculation, with no
   * reload needed.
   *
   * @param {string} choice One of 'system', 'light', 'dark'.
   */
  const apply = (choice) => {
    root.classList.toggle(DARK, resolveDark(choice === SYSTEM ? null : choice));
  };

  /**
   * Check the radio matching the stored preference.
   *
   * The server renders "System" checked because it cannot see localStorage.
   * An unrecognised stored value leaves that default in place, matching what
   * resolveDark does with it.
   */
  const syncChecked = () => {
    const stored = readStored();
    const choice = stored === DARK || stored === LIGHT ? stored : SYSTEM;
    const radio = fieldset.querySelector(`input[value="${choice}"]`);
    if (radio) radio.checked = true;
  };

  fieldset.addEventListener('change', (event) => {
    const choice = event.target.value;
    store(choice);
    apply(choice);
  });

  // Only meaningful while the preference is 'system' — a pinned theme is
  // deliberately immune to the OS, which is the whole point of pinning it.
  darkQuery.addEventListener('change', () => {
    if (readStored() === null) apply(SYSTEM);
  });

  syncChecked();
})();
