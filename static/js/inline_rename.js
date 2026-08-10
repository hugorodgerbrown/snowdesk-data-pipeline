/*
 * static/js/inline_rename.js — renaming a UGC panel row in place (SNOW-658).
 *
 * The DOM half of includes/_ugc_panel_row.html's `renameable` state. Hugo's
 * "Map panels — common format" design: "Where a row can be renamed, that
 * happens in place on the label." The pencil, or a click on the label
 * itself, swaps the label for the editor already sitting hidden beside it;
 * Enter or blur commits, Escape cancels, and a trimmed-empty input leaves
 * the title unchanged.
 *
 * WHAT THIS REPLACES. Both renameable panels used ``window.prompt`` —
 * downloads since SNOW-635, favourites for one day after SNOW-658's first
 * pass copied it. A prompt is a modal browser chrome dialogue that covers
 * the thing being renamed, cannot be styled, and reads out of place on a
 * touch device; the design puts the edit where the name is.
 *
 * WHY IT IS ITS OWN MODULE. Two panels rename — a favourite (POST to
 * favourites:rename) and a downloaded custom area (a local IndexedDB
 * write through window.pwaBasemapDownloads.rename). The INTERACTION is
 * identical and the COMMIT is not, so the interaction lives here once and
 * each module passes its own commit. The alternative — the same
 * focus/keyboard/blur state machine written twice — is exactly the
 * duplication that had the two panels' rows drifting apart to begin with.
 *
 * WHY THE EDITOR IS SERVER-RENDERED. The input is in the row's markup,
 * hidden, rather than created here: its aria-label is user-facing copy,
 * and makemessages cannot see a string built in JS (see docs/i18n.md).
 * This module only ever reveals what the template already rendered.
 *
 * NO TRAILING STATE. Every listener is bound at the start of an edit and
 * removed when it finishes, because the input is a persistent element that
 * gets edited many times — a listener added per edit and never removed
 * would fire once per previous edit on the next commit.
 */

(function inlineRenameInit() {
  'use strict';

  var ROW_SELECTOR = '[data-row-renameable]';
  var LABEL_SELECTOR = '[data-row-label]';
  var INPUT_SELECTOR = '[data-row-rename-input]';
  var TRIGGER_SELECTOR = '[data-row-rename]';

  /**
   * The renameable row a click belongs to, if the click should start an
   * edit.
   *
   * Two things open an edit, and both are the design's: the pencil (a real
   * button, so the interaction is reachable from the keyboard and named for
   * a screen reader) and the label itself (the obvious target, and the one
   * a pointer user reaches for first). A click anywhere else in the row —
   * the trash, the meta line — is not a rename.
   *
   * @param {EventTarget|null} target The click's target.
   * @returns {HTMLElement|null} The row, or null when this was not a
   *   rename click.
   */
  function rowFor(target) {
    var el = /** @type {HTMLElement|null} */ (target);
    if (!el || !el.closest) return null;
    var row = el.closest(ROW_SELECTOR);
    if (!row) return null;
    if (!el.closest(TRIGGER_SELECTOR) && !el.closest(LABEL_SELECTOR)) return null;
    return /** @type {HTMLElement} */ (row);
  }

  /**
   * Put a row's label into an inline edit.
   *
   * Safe to call on a row already being edited (the second call just
   * refocuses) and on a row with no editor (returns false, so a caller can
   * fall back rather than swallow the click).
   *
   * @param {HTMLElement} row A `[data-row-renameable]` row.
   * @param {function(string): void} onCommit Called with the new name —
   *   trimmed, non-empty, and different from the current one. Never called
   *   for a cancel, an unchanged name, or an emptied field: the design's
   *   own rule is that clearing the field leaves the title alone.
   * @returns {boolean} Whether an edit was opened.
   */
  function begin(row, onCommit) {
    var label = /** @type {HTMLElement|null} */ (row.querySelector(LABEL_SELECTOR));
    var input = /** @type {HTMLInputElement|null} */ (row.querySelector(INPUT_SELECTOR));
    if (!label || !input) return false;

    if (!input.hidden) {
      input.focus();
      return true;
    }

    // The label's own text is the truth, not the input's stale `value`:
    // the row may have been re-rendered, or a previous edit cancelled,
    // since the input was last written to.
    var current = label.textContent.trim();
    input.value = current;
    label.hidden = true;
    input.hidden = false;
    input.focus();
    input.select();

    /**
     * Restore the reading state and unbind, optionally committing.
     *
     * The row is restored BEFORE the commit fires, not after: a commit
     * re-reads or re-renders the list, and a row left mid-edit while that
     * is in flight is a second source of truth about what the name is.
     *
     * @param {boolean} commit Whether to offer the value to the caller.
     * @returns {void}
     */
    function finish(commit) {
      input.removeEventListener('keydown', onKeyDown);
      input.removeEventListener('blur', onBlur);
      var next = input.value.trim();
      input.hidden = true;
      label.hidden = false;
      if (!commit) return;
      // An emptied field is not an erasure — the design's own commit()
      // leaves the title unchanged — and an unchanged name is not an edit,
      // so neither is worth a write.
      if (!next || next === current) return;
      onCommit(next);
    }

    /**
     * @param {KeyboardEvent} event
     * @returns {void}
     */
    function onKeyDown(event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        finish(true);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        // Cancel first, so the blur that follows hiding a focused input
        // finds nothing bound and cannot commit what Escape just refused.
        finish(false);
      }
    }

    /**
     * @returns {void}
     */
    function onBlur() {
      finish(true);
    }

    input.addEventListener('keydown', onKeyDown);
    input.addEventListener('blur', onBlur);
    return true;
  }

  window.pwaInlineRename = Object.freeze({
    begin: begin,
    rowFor: rowFor,
  });
})();
