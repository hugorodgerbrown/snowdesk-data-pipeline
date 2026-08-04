/*
 * static/js/i18n_strings.js — read server-translated strings back into JS
 * (SNOW-620).
 *
 * ``makemessages`` scans templates and Python. It does not scan
 * JavaScript, so a string written as a JS literal is invisible to the
 * message catalogue and ships as English to every locale no matter how
 * complete that locale is. Roughly forty user-facing strings were in that
 * position across eight modules — the whole layers-menu sync dashboard,
 * every mutation and favourite failure toast, the update banner, and all
 * the timelapse controls.
 *
 * The fix already existed in the tree, used by exactly one module:
 * ``_map_downloads_sheet.html`` renders a hidden ``<template>`` of
 * ``{% trans %}`` spans and ``map_downloads_manager.js`` reads them back.
 * That read loop was thirty lines with two non-obvious details in it, and
 * eight more copies of it would be eight chances to get one of them
 * wrong — so it lives here now and every surface calls it.
 *
 * ## Usage
 *
 *   var STRINGS = self.pwaStrings.read('my-strings-template', {
 *     'save-failed': 'That could not be saved. Try again.',
 *   });
 *   el.textContent = STRINGS['save-failed'];
 *
 * The second argument is the English fallback, and it is not optional in
 * spirit: a module whose template is missing (a partial not included on
 * this page, a surface built before the template rendered) must still put
 * words on the screen rather than ``undefined``. The fallback is also the
 * only copy of the string a reader of the JS can see, so it doubles as
 * documentation of what the key means.
 *
 * ## Two details worth keeping
 *
 * **Whitespace is collapsed, not trimmed.** ``djangofmt`` reflows a long
 * ``{% blocktrans %}`` across lines to satisfy the line length it
 * enforces, which puts a newline and a run of source indentation into the
 * middle of the string. That is invisible in the template and rendered
 * verbatim into a ``window.confirm`` dialog. Collapsing on read makes
 * these strings immune to how the formatter chooses to wrap them, rather
 * than leaving templates that must never be reformatted.
 *
 * **Interpolation is by name, never positional.** Django's ``blocktrans``
 * emits ``%(name)s`` placeholders and a locale is free to reorder them —
 * a German translation may well need the size before the region name. A
 * positional substitution silently swaps them.
 *
 * Dependency-free and DOM-light by design: everything except ``read``
 * takes plain values, so it is unit-tested directly
 * (tests/js/test_i18n_strings.js).
 */

(function () {
  'use strict';

  /**
   * Collapse every run of whitespace to a single space and trim.
   *
   * @param {string} value
   * @returns {string}
   */
  function collapse(value) {
    return String(value == null ? '' : value)
      .replace(/\s+/g, ' ')
      .trim();
  }

  /**
   * Read a strings ``<template>`` back into a plain object.
   *
   * Every ``[data-string]`` element inside the template contributes one
   * entry, keyed by its ``data-string`` value. Keys absent from the
   * template — or every key, when the template itself is absent — fall
   * back to `fallbacks`.
   *
   * An empty or whitespace-only element does NOT override its fallback.
   * A translator leaving a msgstr blank is how gettext spells "not
   * translated yet", and Django renders that as the empty string; taking
   * it literally would blank the control rather than show English.
   *
   * @param {string} templateId
   * @param {Object<string, string>} fallbacks
   * @param {Document} [doc] Injectable for tests.
   * @returns {Object<string, string>}
   */
  function read(templateId, fallbacks, doc) {
    var out = {};
    var key;
    var source = fallbacks || {};
    for (key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) {
        out[key] = source[key];
      }
    }

    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) return out;
    var tpl = d.getElementById(templateId);
    // `content` is absent on a <template> polyfilled away, and on any
    // element that is not a template at all — an id collision would
    // otherwise throw here and take the whole module down.
    if (!tpl || !tpl.content) return out;

    tpl.content.querySelectorAll('[data-string]').forEach(function (el) {
      var text = collapse(el.textContent);
      if (text) out[el.dataset.string] = text;
    });
    return out;
  }

  /**
   * Substitute ``%(name)s``-style placeholders into a translated string.
   *
   * An unknown placeholder is left as-is rather than replaced with
   * ``undefined``: a locale that invents a placeholder the caller does not
   * supply should show a visibly wrong string, not a confidently wrong one.
   *
   * @param {string} template
   * @param {Object<string, *>} values
   * @returns {string}
   */
  function interpolate(template, values) {
    if (!template) return '';
    var source = values || {};
    return String(template).replace(/%\((\w+)\)s/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(source, key)
        ? String(source[key])
        : match;
    });
  }

  var HTML_ESCAPES = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
  };

  /**
   * Escape a translated string for interpolation into an HTML string.
   *
   * Some surfaces assemble their markup by concatenation and assign it to
   * ``innerHTML`` (``report.js``'s ineligible-user states). A translated
   * string dropped into that unescaped is a markup injection waiting for
   * the first locale whose copy contains a quote — ``aria-label="…"``
   * closes early and the rest of the sentence becomes attributes. This is
   * not about untrusted input, since translations are ours; it is that
   * ordinary punctuation in ordinary prose would silently corrupt the DOM.
   *
   * Prefer ``textContent`` where the surface allows it — then no escaping
   * is needed at all.
   *
   * @param {string} value
   * @returns {string}
   */
  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
      return HTML_ESCAPES[ch];
    });
  }

  self.pwaStrings = Object.freeze({
    read: read,
    interpolate: interpolate,
    collapse: collapse,
    escapeHtml: escapeHtml,
  });
})();
