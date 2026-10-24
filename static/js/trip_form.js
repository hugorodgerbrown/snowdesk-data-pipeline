/*
 * static/js/trip_form.js — the Plan form's day tiles and its read-back
 * footer (SNOW-1215).
 *
 * The form (apps/trips/templates/trips/partials/_trip_form.html) posts with
 * no script at all: every day tile is a `name="date"` radio. This module
 * adds the two things a radio group cannot do on its own:
 *
 *   - OTHER. The "Other" tile carries a native date input laid over it, so
 *     a tap opens the system's own picker (and `showPicker()` does the same
 *     on a desktop, where a click on the field's text would only focus
 *     it). A picked day is copied into the "Other" radio's value and
 *     checked — or, if it falls inside the week, its own tile is checked
 *     instead, so one day never has two tiles.
 *   - THE READ-BACK. The footer says the plan back as it is chosen: the
 *     day and the time on the first line, and on the second the meeting
 *     point's three word address once it is found (the place sentence
 *     until then) and the day's danger level when a bulletin rated it.
 *     The line under the tiles names the checked day.
 *
 * The meeting picker owns the address and the place sentence; this module
 * reads them back through a MutationObserver rather than a channel of its
 * own, so the picker does not know the footer exists.
 *
 * The form is swapped whole by htmx on a validation error, so it is bound
 * again on `htmx:load` — once per form element, never twice.
 *
 * Publishes (frozen `window.pwaTripForm`), for tests:
 *
 *   formatDay(iso, style, locale) → "Sun 25 Oct" (style "short"),
 *                                   "Sunday 25 October" ("long") or
 *                                   "25 Oct" ("tile")
 *   readbackLines({day, time, where, rating}) → {when, where}
 *   bind(form)                    — wire one form
 */

(function tripFormInit() {
  'use strict';

  /** The separator between the read-back's parts. Punctuation, not copy. */
  var JOIN = ' · ';

  /**
   * Word an ISO day in the page's language.
   *
   * Formatted at noon UTC and read in UTC, so the day cannot slip either
   * side of midnight in the reader's own timezone.
   *
   * @param {string} iso A "YYYY-MM-DD" day.
   * @param {'short'|'long'|'tile'} style "Sun 25 Oct", "Sunday 25 October",
   *   or "25 Oct" for the Other tile.
   * @param {string} [locale] A BCP 47 tag; the page's language by default.
   * @returns {string} '' for a value that is not a day.
   */
  function formatDay(iso, style, locale) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(iso || '')) return '';
    var date = new Date(iso + 'T12:00:00Z');
    if (Number.isNaN(date.getTime())) return '';
    var options = {
      long: { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' },
      short: { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' },
      tile: { day: 'numeric', month: 'short', timeZone: 'UTC' },
    }[style] || {};
    try {
      return new Intl.DateTimeFormat(locale || document.documentElement.lang || undefined, /** @type {Intl.DateTimeFormatOptions} */ (options))
        .format(date)
        .replace(/,/g, '');
    } catch (_err) {
      return iso;
    }
  }

  /**
   * The footer's two lines.
   *
   * @param {{day: string, time: string, where: string, rating: string}} plan
   *   The day as words, the time, the meeting point in words and the
   *   day's level in words — each '' when not yet known.
   * @returns {{when: string, where: string}}
   */
  function readbackLines(plan) {
    var keep = function (part) { return !!part; };
    return {
      when: [plan.day, plan.time].filter(keep).join(JOIN),
      where: [plan.where, plan.rating].filter(keep).join(JOIN),
    };
  }

  /**
   * Wire one Plan form: the Other tile, the captions and the read-back.
   *
   * @param {HTMLFormElement} form The `[data-trip-form]` element.
   */
  function bind(form) {
    if (!form || form.hasAttribute('data-trip-form-bound')) return;
    form.setAttribute('data-trip-form-bound', '');

    var otherRadio = /** @type {HTMLInputElement|null} */ (form.querySelector('[data-day-other]'));
    var otherInput = /** @type {HTMLInputElement|null} */ (form.querySelector('[data-day-other-input]'));
    var otherLabel = form.querySelector('[data-day-other-label]');
    // The tile with no day picked — the calendar glyph — to put back when
    // a picked day turns out to have a tile of its own.
    var otherBlank = /** @type {HTMLTemplateElement|null} */ (form.querySelector('[data-day-other-blank]'));
    var otherCaption = /** @type {HTMLElement|null} */ (form.querySelector('[data-day-caption="other"]'));
    var timeInput = /** @type {HTMLInputElement|null} */ (form.querySelector('input[name="start_time"]'));
    var whenLine = form.querySelector('[data-trip-readback-when]');
    var whereLine = form.querySelector('[data-trip-readback-where]');

    /** @returns {?HTMLInputElement} The checked day radio. */
    function checkedDay() {
      return /** @type {HTMLInputElement|null} */ (form.querySelector('input[name="date"]:checked'));
    }

    /** @returns {?HTMLElement} The caption for the checked day. */
    function checkedCaption() {
      var radio = checkedDay();
      if (!radio) return null;
      if (radio === otherRadio) return otherCaption;
      return /** @type {HTMLElement|null} */ (
        form.querySelector('[data-day-caption="' + radio.value + '"]')
      );
    }

    /** Show the checked day's caption and hide the rest. */
    function paintCaption() {
      var shown = checkedCaption();
      form.querySelectorAll('[data-day-caption]').forEach(function (caption) {
        /** @type {HTMLElement} */ (caption).hidden = caption !== shown;
      });
    }

    /** Write the footer from what the form holds now. */
    function paintReadback() {
      var radio = checkedDay();
      var caption = checkedCaption();
      var day = '';
      if (radio && radio === otherRadio) day = formatDay(radio.value, 'short');
      else if (caption) day = caption.getAttribute('data-short') || '';
      var address = form.querySelector('[data-meeting-address]');
      var place = form.querySelector('[data-meeting-place]');
      var where = address && address.getAttribute('data-state') === 'found'
        ? (address.textContent || '').trim()
        : ((place && place.textContent) || '').trim();
      var lines = readbackLines({
        day: day,
        time: timeInput ? timeInput.value : '',
        where: where,
        rating: (caption && caption.getAttribute('data-rating')) || '',
      });
      if (whenLine) whenLine.textContent = lines.when;
      if (whereLine) whereLine.textContent = lines.where;
    }

    /**
     * Take a day from the date picker: its own tile when it has one,
     * otherwise "Other", which then prints it.
     */
    function takeOther() {
      if (!otherInput || !otherRadio) return;
      var iso = otherInput.value;
      if (!iso) return;
      var tile = /** @type {HTMLInputElement|null} */ (
        form.querySelector('input[name="date"][value="' + iso + '"]:not([data-day-other])')
      );
      if (tile) {
        tile.checked = true;
        otherRadio.value = '';
        if (otherLabel && otherBlank) otherLabel.replaceChildren(otherBlank.content.cloneNode(true));
        if (otherCaption) otherCaption.textContent = '';
      } else {
        otherRadio.value = iso;
        otherRadio.checked = true;
        if (otherLabel) {
          var text = document.createElement('span');
          text.className = 'text-sm font-semibold text-text-1';
          text.textContent = formatDay(iso, 'tile');
          otherLabel.replaceChildren(text);
        }
        if (otherCaption) otherCaption.textContent = formatDay(iso, 'long');
      }
      paintCaption();
      paintReadback();
    }

    form.addEventListener('change', function (event) {
      var target = /** @type {HTMLInputElement} */ (event.target);
      if (target === otherInput) takeOther();
      else if (target.name === 'date' || target === timeInput) {
        paintCaption();
        paintReadback();
      }
    });
    if (timeInput) timeInput.addEventListener('input', paintReadback);
    if (otherInput) {
      otherInput.addEventListener('click', function () {
        if (typeof otherInput.showPicker !== 'function') return;
        try {
          otherInput.showPicker();
        } catch (_err) {
          // Not from a user gesture, or already open: the tap still
          // reaches the field itself.
        }
      });
    }

    // The picker writes the address and the place sentence; follow them.
    if (typeof MutationObserver === 'function') {
      var observer = new MutationObserver(paintReadback);
      form.querySelectorAll('[data-meeting-address], [data-meeting-place]').forEach(function (line) {
        observer.observe(line, { childList: true, characterData: true, subtree: true, attributes: true });
      });
    }

    paintCaption();
    paintReadback();
  }

  /**
   * Bind every Plan form under a root.
   *
   * @param {ParentNode} root
   */
  function bindAll(root) {
    root.querySelectorAll('[data-trip-form]').forEach(function (form) {
      bind(/** @type {HTMLFormElement} */ (form));
    });
  }

  self.pwaTripForm = Object.freeze({
    formatDay: formatDay,
    readbackLines: readbackLines,
    bind: bind,
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bindAll(document); });
  } else {
    bindAll(document);
  }
  // An invalid submission swaps the form in whole; htmx announces the new
  // content, and the fresh form is bound once.
  document.addEventListener('htmx:load', function (event) {
    var root = /** @type {CustomEvent} */ (event).detail && /** @type {CustomEvent} */ (event).detail.elt;
    if (root && root.querySelectorAll) {
      if (root.matches && root.matches('[data-trip-form]')) bind(root);
      bindAll(root);
    }
  });
})();
