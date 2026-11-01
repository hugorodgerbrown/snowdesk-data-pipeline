// @ts-check
/**
 * static/js/help_filter.js — the filter box at the top of /help/ (SNOW-743).
 *
 * /help/ holds twenty collapsed topics under four eyebrow headings, and a
 * reader who arrives knowing the word for what they want ("gpx",
 * "offline", "slope") should not have to open them one at a time. Typing
 * here hides every topic whose text does not match, opens the ones that
 * do, marks the matched words, and hides a heading left with no topic.
 * Clearing the box puts the page back exactly as it was before the first
 * keystroke, including a topic a #help-topic-… link had opened.
 *
 * What it matches:
 *   - each topic's own words, its title and body, folded to lower case
 *     with accents stripped (so TELEPHERIQUE finds téléphérique);
 *   - never a topic's illustration ([data-testid$="-illustration"]): those
 *     are live mock components whose sample copy would make false hits;
 *   - a short synonym map, so a reader's word finds the topic that uses
 *     ours: when the query's own word matches nothing, a query naming a
 *     synonym key searches for its values instead.
 *
 * This is the deliberate alternative to a semantic search: the corpus is
 * one page, nothing here fetches, and the page keeps working offline.
 *
 * The box is rendered ``hidden`` and only this script reveals it, so with
 * JavaScript off there is no control that does nothing. Highlighting
 * splits text nodes and wraps runs in <mark>; it never writes innerHTML.
 * Every string comes from #help-filter-strings-template via
 * window.pwaStrings, with the English literal as the fallback.
 */

(function helpFilterInit() {
  'use strict';

  /**
   * A reader's word → the words the topics use for it. A key matches a
   * query that equals it or that it starts with (three letters or more),
   * so "favor" reaches "favorite" and so the topic that says "favourite".
   *
   * @type {Readonly<Record<string, readonly string[]>>}
   */
  const SYNONYMS = Object.freeze({
    gpx: ['route'],
    track: ['route'],
    tour: ['route'],
    cache: ['offline', 'download'],
    storage: ['offline', 'download'],
    basemap: ['layer'],
    swisstopo: ['layer'],
    steep: ['slope'],
    gradient: ['slope'],
    incline: ['slope'],
    favorite: ['favourite'],
    pin: ['favourite'],
    star: ['favourite'],
    report: ['observation'],
    login: ['account', 'sign in'],
    password: ['account', 'sign in'],
    email: ['account'],
    app: ['install'],
    pwa: ['install'],
    danger: ['bulletin'],
    risk: ['bulletin'],
    forecast: ['weather'],
    temperature: ['weather'],
    wind: ['weather'],
    season: ['calendar'],
    history: ['calendar', 'timeline'],
    date: ['timeline'],
  });

  const MARK_CLASS = 'rounded-sm bg-status-warning-bg text-text-1';
  const ILLUSTRATION = '[data-testid$="-illustration"]';

  /**
   * Fold a string for matching: lower case, accents stripped. Folded one
   * character at a time so ``index[i]`` names the source character the
   * i-th folded one came from, which is how a match is mapped back onto
   * the text node to mark it.
   *
   * @param {string} text
   * @returns {{ folded: string, index: number[] }}
   */
  function fold(text) {
    let folded = '';
    /** @type {number[]} */
    const index = [];
    for (let i = 0; i < text.length; i += 1) {
      const piece = text[i].normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
      for (let j = 0; j < piece.length; j += 1) {
        folded += piece[j];
        index.push(i);
      }
    }
    return { folded, index };
  }

  /**
   * The folded query, and the terms it widens to: itself plus the
   * synonyms of every key it names. The widened set is only searched when
   * the query's own word finds nothing, so "gpx" lands on the one topic
   * that says GPX rather than on every topic that mentions a route.
   *
   * @param {string} query
   * @returns {{ literal: string[], widened: string[] }}
   */
  function termsFor(query) {
    const q = fold(query).folded.replace(/\s+/g, ' ').trim();
    if (!q) return { literal: [], widened: [] };
    const terms = new Set([q]);
    Object.keys(SYNONYMS).forEach((key) => {
      if (key === q || (q.length >= 3 && key.startsWith(q))) {
        SYNONYMS[key].forEach((word) => terms.add(word));
      }
    });
    return { literal: [q], widened: Array.from(terms) };
  }

  /**
   * The text nodes of a topic that count: everything but its illustration.
   *
   * @param {Element} panel
   * @returns {Text[]}
   */
  function textNodes(panel) {
    /** @type {Text[]} */
    const nodes = [];
    const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || parent.closest(`${ILLUSTRATION}, script, template, style`)) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      nodes.push(/** @type {Text} */ (node));
    }
    return nodes;
  }

  /**
   * The source ranges in a string that any term matches, merged.
   *
   * @param {string} text
   * @param {string[]} terms
   * @returns {Array<[number, number]>}
   */
  function matchRanges(text, terms) {
    const { folded, index } = fold(text);
    /** @type {Array<[number, number]>} */
    const ranges = [];
    terms.forEach((term) => {
      let at = folded.indexOf(term);
      while (at !== -1) {
        ranges.push([index[at], index[at + term.length - 1] + 1]);
        at = folded.indexOf(term, at + term.length);
      }
    });
    ranges.sort((a, b) => a[0] - b[0]);
    /** @type {Array<[number, number]>} */
    const merged = [];
    ranges.forEach((range) => {
      const last = merged[merged.length - 1];
      if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
      else merged.push([range[0], range[1]]);
    });
    return merged;
  }

  /**
   * Wrap each matched run of a text node in a <mark>, by splitting it.
   *
   * @param {Text} node
   * @param {Array<[number, number]>} ranges Source ranges, ascending.
   */
  function markNode(node, ranges) {
    let current = node;
    let offset = 0;
    ranges.forEach(([start, end]) => {
      const run = current.splitText(start - offset);
      const rest = run.splitText(end - start);
      const mark = document.createElement('mark');
      mark.className = MARK_CLASS;
      mark.setAttribute('data-help-filter-mark', '');
      run.replaceWith(mark);
      mark.appendChild(run);
      current = rest;
      offset = end;
    });
  }

  /**
   * Remove every <mark> this module added, merging the text back.
   *
   * @param {ParentNode} root
   */
  function unmark(root) {
    root.querySelectorAll('mark[data-help-filter-mark]').forEach((mark) => {
      const parent = mark.parentNode;
      mark.replaceWith(...Array.from(mark.childNodes));
      if (parent) parent.normalize();
    });
  }

  /** Bind the box on /help/, if this page has one. */
  function init() {
    const box = /** @type {HTMLElement | null} */ (document.querySelector('[data-help-filter]'));
    const input = box && box.querySelector('[data-help-filter-input]');
    const main = box && box.closest('main');
    if (!box || !(input instanceof HTMLInputElement) || !main) return;
    const status = box.querySelector('[data-help-filter-status]');

    /** @type {Record<string, string>} */
    const FALLBACKS = {
      'no-match': 'No topic matches that. Try a different word.',
      'one-match': '1 topic matches.',
      'some-match': '%(count)s topics match.',
    };
    const strings = window.pwaStrings;
    /** @type {Record<string, string>} */
    const STRINGS = strings ? strings.read('help-filter-strings-template', FALLBACKS) : FALLBACKS;
    /**
     * @param {string} template
     * @param {number} count
     * @returns {string}
     */
    const withCount = (template, count) => (strings
      ? strings.interpolate(template, { count })
      : template.replace('%(count)s', String(count)));

    /** Topics and headings in document order; a heading owns what follows it. */
    const items = Array.from(
      main.querySelectorAll('[data-hook="help-section"], details.snowdesk-panel[id^="help-topic-"]'),
    );
    /** @type {Map<Element, HTMLDetailsElement[]>} */
    const sections = new Map();
    /** @type {HTMLDetailsElement[]} */
    const panels = [];
    /** @type {Element | null} */
    let heading = null;
    items.forEach((item) => {
      if (item instanceof HTMLDetailsElement) {
        panels.push(item);
        if (heading) sections.get(heading)?.push(item);
      } else {
        heading = item;
        sections.set(item, []);
      }
    });

    /** Which topics were open before the first keystroke; null when unfiltered. */
    /** @type {Map<HTMLDetailsElement, boolean> | null} */
    let before = null;

    /** @param {string} text */
    const say = (text) => {
      if (status) status.textContent = text;
    };

    const apply = () => {
      unmark(main);
      const { literal, widened } = termsFor(input.value);
      if (!literal.length) {
        if (before) {
          before.forEach((open, panel) => { panel.open = open; });
          before = null;
        }
        panels.forEach((panel) => { panel.hidden = false; });
        sections.forEach((_, item) => { /** @type {HTMLElement} */ (item).hidden = false; });
        say('');
        return;
      }
      if (!before) before = new Map(panels.map((panel) => [panel, panel.open]));

      /**
       * Each topic's text nodes that the terms match, with their ranges.
       *
       * @param {string[]} terms
       * @returns {Array<Array<[Text, Array<[number, number]>]>>}
       */
      const hitsFor = (terms) => panels.map((panel) => textNodes(panel)
        .map((node) => /** @type {[Text, Array<[number, number]>]} */ (
          [node, matchRanges(node.data, terms)]
        ))
        .filter(([, ranges]) => ranges.length));

      let hits = hitsFor(literal);
      if (!hits.some((found) => found.length) && widened.length > literal.length) {
        hits = hitsFor(widened);
      }

      let shown = 0;
      panels.forEach((panel, i) => {
        const found = hits[i];
        panel.hidden = !found.length;
        if (!found.length) return;
        shown += 1;
        panel.open = true;
        found.forEach(([node, ranges]) => markNode(node, ranges));
      });
      sections.forEach((owned, item) => {
        /** @type {HTMLElement} */ (item).hidden = !owned.some((panel) => !panel.hidden);
      });

      if (!shown) say(STRINGS['no-match']);
      else if (shown === 1) say(STRINGS['one-match']);
      else say(withCount(STRINGS['some-match'], shown));
    };

    input.addEventListener('input', apply);
    box.hidden = false;
  }

  init();
}());
