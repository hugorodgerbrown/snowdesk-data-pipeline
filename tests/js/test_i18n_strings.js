/*
 * tests/js/test_i18n_strings.js — the shared translated-string reader
 * (SNOW-620).
 *
 * Eight modules now read their user-facing copy through this one helper,
 * so its failure modes are shared too. The cases below are the ones that
 * would ship wrong text to a real user rather than merely fail loudly:
 * a reflowed `{% blocktrans %}` arriving with source indentation inside
 * it, an untranslated msgstr arriving empty, and a locale reordering the
 * placeholders in a sentence.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';

const strings = self.pwaStrings;

/**
 * Render a strings template into the document.
 *
 * @param {string} id
 * @param {Object<string, string>} entries
 * @returns {HTMLTemplateElement}
 */
function mountTemplate(id, entries) {
  const tpl = document.createElement('template');
  tpl.id = id;
  tpl.innerHTML = Object.entries(entries)
    .map(([key, value]) => `<span data-string="${key}">${value}</span>`)
    .join('');
  document.body.appendChild(tpl);
  return tpl;
}

beforeEach(() => {
  document.body.innerHTML = '';
});

describe('read', () => {
  it('prefers the server-rendered string over the fallback', () => {
    mountTemplate('t', { save: 'Enregistré' });

    expect(strings.read('t', { save: 'Saved' })).toEqual({ save: 'Enregistré' });
  });

  it('falls back to English when the template is absent', () => {
    // The real case: a partial that is not included on this page. The
    // module must still render words.
    expect(strings.read('missing', { save: 'Saved' })).toEqual({ save: 'Saved' });
  });

  it('falls back per key, not all-or-nothing', () => {
    mountTemplate('t', { save: 'Enregistré' });

    expect(strings.read('t', { save: 'Saved', undo: 'Undo' })).toEqual({
      save: 'Enregistré',
      undo: 'Undo',
    });
  });

  it('collapses the indentation djangofmt reflows into a long blocktrans', () => {
    // djangofmt wraps a long {% blocktrans %} to satisfy its line length,
    // putting a newline plus source indentation mid-sentence. Rendered
    // verbatim into a confirm() dialog that reads as broken text.
    mountTemplate('t', {
      confirm: 'Remove the offline map?\n            This frees space.',
    });

    expect(strings.read('t', {}).confirm).toBe(
      'Remove the offline map? This frees space.',
    );
  });

  it('keeps the fallback when a msgstr is untranslated and renders empty', () => {
    // gettext spells "not translated yet" as an empty msgstr, and Django
    // renders that as "". Taking it literally would blank the control.
    mountTemplate('t', { save: '', undo: '   ' });

    expect(strings.read('t', { save: 'Saved', undo: 'Undo' })).toEqual({
      save: 'Saved',
      undo: 'Undo',
    });
  });

  it('survives an id collision with a non-template element', () => {
    const div = document.createElement('div');
    div.id = 't';
    document.body.appendChild(div);

    expect(() => strings.read('t', { save: 'Saved' })).not.toThrow();
    expect(strings.read('t', { save: 'Saved' })).toEqual({ save: 'Saved' });
  });

  it('returns the fallbacks unchanged rather than a reference to them', () => {
    const fallbacks = { save: 'Saved' };
    mountTemplate('t', { save: 'Enregistré' });

    strings.read('t', fallbacks);

    expect(fallbacks.save).toBe('Saved');
  });

  it('tolerates no fallbacks at all', () => {
    mountTemplate('t', { save: 'Saved' });

    expect(strings.read('t')).toEqual({ save: 'Saved' });
  });
});

describe('interpolate', () => {
  it('substitutes named placeholders', () => {
    expect(
      strings.interpolate('%(used)s of %(budget)s used', {
        used: '12 MB',
        budget: '50 MB',
      }),
    ).toBe('12 MB of 50 MB used');
  });

  it('substitutes by name when the locale reorders the placeholders', () => {
    // The reason substitution cannot be positional. A locale is free to
    // put the size first; a positional pass would swap the two values.
    expect(
      strings.interpolate('%(size)s frei durch %(name)s', {
        name: 'Wallis',
        size: '8 MB',
      }),
    ).toBe('8 MB frei durch Wallis');
  });

  it('repeats a placeholder used twice', () => {
    expect(strings.interpolate('%(n)s of %(n)s', { n: '3' })).toBe('3 of 3');
  });

  it('leaves an unsupplied placeholder visibly unsubstituted', () => {
    expect(strings.interpolate('%(a)s and %(b)s', { a: 'x' })).toBe(
      'x and %(b)s',
    );
  });

  it('returns empty for a missing string rather than "undefined"', () => {
    expect(strings.interpolate(undefined, { a: 'x' })).toBe('');
  });

  it('tolerates no values', () => {
    expect(strings.interpolate('plain text')).toBe('plain text');
  });
});
