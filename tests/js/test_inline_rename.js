/*
 * tests/js/test_inline_rename.js — Vitest DOM tests for
 * static/js/inline_rename.js (SNOW-658).
 *
 * The interaction two UGC panels share: a favourite and a downloaded
 * custom area both rename in place on the row's own label, and only the
 * COMMIT differs (a POST vs an IndexedDB write). This file covers the part
 * that is shared, so neither caller's own suite has to re-prove Escape.
 *
 * The rules under test are the design's, verbatim from its own commit():
 * Enter or blur commits, Escape cancels, a trimmed-empty input leaves the
 * title unchanged. Two more come from the DOM rather than the design, and
 * are the ones a re-implementation would get wrong:
 *
 *   - Escape must unbind BEFORE the input hides, because hiding a focused
 *     element fires blur, and blur commits. A cancel that unbinds too late
 *     saves the very edit the user just refused;
 *   - the input is a persistent element edited many times, so a listener
 *     added per edit and never removed fires once per previous edit on the
 *     next commit. The count assertions below are what catch that.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/inline_rename.js';

/** Build one renameable row, mirroring includes/_ugc_panel_row.html.
 *
 * @returns {HTMLElement} The row.
 */
function buildRow() {
  document.body.innerHTML = `
    <ul><li data-row-renameable>
      <span data-row-label>Old name</span>
      <input type="text" data-row-rename-input hidden aria-label="Name">
      <button type="button" data-row-rename aria-label="Rename">✎</button>
      <button type="button" data-row-remove aria-label="Remove">🗑</button>
    </li></ul>`;
  return document.querySelector('li');
}

/** @param {HTMLElement} row @param {string} key @returns {void} */
function press(row, key) {
  row
    .querySelector('[data-row-rename-input]')
    .dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
}

/** @param {HTMLElement} row @returns {void} */
function blur(row) {
  row.querySelector('[data-row-rename-input]').dispatchEvent(new FocusEvent('blur'));
}

/** @param {HTMLElement} row @param {string} value @returns {void} */
function type(row, value) {
  row.querySelector('[data-row-rename-input]').value = value;
}

let row;
let commit;

beforeEach(() => {
  row = buildRow();
  commit = vi.fn();
});

describe('opening an edit', () => {
  it('reveals the editor, hides the label, and seeds from the label', () => {
    row.querySelector('[data-row-label]').textContent = '  Spaced name  ';

    window.pwaInlineRename.begin(row, commit);

    const input = row.querySelector('[data-row-rename-input]');
    expect(input.hidden).toBe(false);
    expect(input.value).toBe('Spaced name');
    expect(row.querySelector('[data-row-label]').hidden).toBe(true);
  });

  it('seeds from the LABEL, not the input’s stale value', () => {
    // The row may have been re-rendered, or a previous edit cancelled,
    // since the input was last written to. The label is the truth.
    row.querySelector('[data-row-rename-input]').value = 'Stale';

    window.pwaInlineRename.begin(row, commit);

    expect(row.querySelector('[data-row-rename-input]').value).toBe('Old name');
  });

  it('reports false, and changes nothing, for a row with no editor', () => {
    row.querySelector('[data-row-rename-input]').remove();

    expect(window.pwaInlineRename.begin(row, commit)).toBe(false);
    expect(row.querySelector('[data-row-label]').hidden).toBe(false);
  });
});

describe('committing', () => {
  it('commits on Enter and restores the reading state', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, 'New name');

    press(row, 'Enter');

    expect(commit).toHaveBeenCalledWith('New name');
    expect(row.querySelector('[data-row-rename-input]').hidden).toBe(true);
    expect(row.querySelector('[data-row-label]').hidden).toBe(false);
  });

  it('commits on blur — leaving the field is not a cancel', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, 'New name');

    blur(row);

    expect(commit).toHaveBeenCalledWith('New name');
  });

  it('trims what it commits', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, '   New name   ');

    press(row, 'Enter');

    expect(commit).toHaveBeenCalledWith('New name');
  });

  it('restores the row BEFORE the commit fires', () => {
    // A commit re-reads or re-renders the list; a row left mid-edit while
    // that is in flight is a second source of truth about the name.
    let hiddenWhenCalled = null;
    window.pwaInlineRename.begin(row, () => {
      hiddenWhenCalled = row.querySelector('[data-row-rename-input]').hidden;
    });
    type(row, 'New name');

    press(row, 'Enter');

    expect(hiddenWhenCalled).toBe(true);
  });
});

describe('not committing', () => {
  it('writes nothing on Escape', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, 'New name');

    press(row, 'Escape');

    expect(commit).not.toHaveBeenCalled();
    expect(row.querySelector('[data-row-label]').hidden).toBe(false);
  });

  it('does not commit on the blur that Escape itself causes', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, 'New name');

    press(row, 'Escape');
    blur(row);

    expect(commit).not.toHaveBeenCalled();
  });

  it('writes nothing when the name is unchanged', () => {
    window.pwaInlineRename.begin(row, commit);

    press(row, 'Enter');

    expect(commit).not.toHaveBeenCalled();
  });

  it('writes nothing when the field is emptied', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, '   ');

    press(row, 'Enter');

    expect(commit).not.toHaveBeenCalled();
    // And the title is left alone — the design's own rule.
    expect(row.querySelector('[data-row-label]').textContent).toBe('Old name');
  });
});

describe('binding and unbinding', () => {
  it('commits once per edit, however many edits came before', () => {
    for (const name of ['First', 'Second', 'Third']) {
      window.pwaInlineRename.begin(row, commit);
      type(row, name);
      press(row, 'Enter');
      row.querySelector('[data-row-label]').textContent = name;
    }

    expect(commit).toHaveBeenCalledTimes(3);
    expect(commit.mock.calls.map((c) => c[0])).toEqual(['First', 'Second', 'Third']);
  });

  it('a second begin() on an open editor refocuses rather than reseeding', () => {
    window.pwaInlineRename.begin(row, commit);
    type(row, 'Half typed');

    expect(window.pwaInlineRename.begin(row, commit)).toBe(true);
    expect(row.querySelector('[data-row-rename-input]').value).toBe('Half typed');
  });
});

describe('rowFor — which clicks open an edit', () => {
  it('recognises the pencil', () => {
    expect(window.pwaInlineRename.rowFor(row.querySelector('[data-row-rename]'))).toBe(
      row,
    );
  });

  it('ignores the label — the pencil is the only trigger (SNOW-658)', () => {
    // Hugo: "We have inline editing & the pencil - choose one." The label
    // is a <span>, so its click was mouse-only; the pencil is a real
    // button, in the tab order and named for a screen reader.
    expect(window.pwaInlineRename.rowFor(row.querySelector('[data-row-label]'))).toBe(
      null,
    );
  });

  it('ignores every other control in the row — the trash above all', () => {
    expect(window.pwaInlineRename.rowFor(row.querySelector('[data-row-remove]'))).toBe(
      null,
    );
  });

  it('ignores a row that is not renameable', () => {
    row.removeAttribute('data-row-renameable');

    expect(window.pwaInlineRename.rowFor(row.querySelector('[data-row-rename]'))).toBe(
      null,
    );
  });
});
