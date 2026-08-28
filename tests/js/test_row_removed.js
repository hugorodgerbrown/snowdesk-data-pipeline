/*
 * tests/js/test_row_removed.js — Vitest DOM tests for static/js/row_removed.js.
 *
 * Scenario: none — this is the recognition half of a row deletion, observed
 * through synthetic htmx lifecycle events. No browser is needed to prove it,
 * and a manual test script could not see it at all.
 *
 * The module answers one question for six callers: has a row just been
 * removed from MY list? Everything it does is in service of getting that
 * answer right in the two ways the naive version gets it wrong, so those two
 * are what this suite is mostly about:
 *
 *   1. an `outerHTML` swap DETACHES the delete form before
 *      `htmx:afterRequest` fires, so the test has to be made on
 *      `htmx:beforeRequest` and carried on the request's own xhr;
 *   2. a row is not only a delete. /account/favourites/'s chevron hx-gets a
 *      card from inside the same list, and treating that as a removal would
 *      re-read the list and close the card the user just opened.
 *
 * Plus the plain scoping the map homepage needs, where three of these lists
 * are on screen at once.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/row_removed.js';

/** Build two sibling lists, each with a delete form and a chevron. */
function buildLists() {
  document.body.innerHTML = `
    <div data-list-a>
      <ul>
        <li>
          <button data-row-disclosure hx-get="/card/"></button>
          <form data-row-remove id="remove-a"></form>
        </li>
      </ul>
    </div>
    <div data-list-b>
      <ul><li><form data-row-remove id="remove-b"></form></li></ul>
    </div>
    <form data-row-remove id="remove-loose"></form>`;
}

/**
 * Drive one htmx request lifecycle.
 *
 * @param {Element|null} elt The element htmx would name as the source.
 * @param {boolean} [successful] Whether the response was a 2xx.
 * @returns {void}
 */
function request(elt, successful = true) {
  const xhr = {};
  document.dispatchEvent(
    new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt } }),
  );
  document.dispatchEvent(
    new CustomEvent('htmx:afterRequest', { detail: { xhr, successful } }),
  );
}

describe('window.pwaRowRemoved', () => {
  it('is a frozen bridge, like every other shared row module', () => {
    expect(Object.isFrozen(window.pwaRowRemoved)).toBe(true);
  });

  describe('watch()', () => {
    let seen;

    beforeEach(() => {
      buildLists();
      seen = vi.fn();
      window.pwaRowRemoved.watch('[data-list-a]', seen);
    });

    it('fires for a delete form inside the watched list', () => {
      request(document.getElementById('remove-a'));

      expect(seen).toHaveBeenCalledTimes(1);
    });

    it('ignores the chevron hx-get from inside the very same row', () => {
      // The account favourites page's disclosure. Keyed on the list alone
      // this would read as a removal, re-read the list, and close the card
      // the user had just opened.
      request(document.querySelector('[data-row-disclosure]'));

      expect(seen).not.toHaveBeenCalled();
    });

    it('ignores a delete in a SIBLING list', () => {
      // Three of these lists share the map homepage. A pin deleted in the
      // favourites panel must not make the routes panel refetch.
      request(document.getElementById('remove-b'));

      expect(seen).not.toHaveBeenCalled();
    });

    it('ignores a delete form outside every list', () => {
      request(document.getElementById('remove-loose'));

      expect(seen).not.toHaveBeenCalled();
    });

    it('ignores a request htmx sourced from the body', () => {
      // How `htmx.ajax(verb, url, {target})` presents itself — the call
      // every caller's own re-read makes. Marked, this would loop.
      request(document.body);

      expect(seen).not.toHaveBeenCalled();
    });

    it('stays quiet when the delete failed', () => {
      // Nothing was removed, so the row is still there and the list still
      // says what it said. Re-reading would claim otherwise.
      request(document.getElementById('remove-a'), false);

      expect(seen).not.toHaveBeenCalled();
    });

    it('answers for a form the swap has already detached', () => {
      // The real sequence: htmx swaps `outerHTML` — which takes the form
      // out of the document — and only then fires afterRequest. This is
      // the whole reason the test is made on beforeRequest and the answer
      // rides the xhr; asking `closest()` at the end would hit an orphan.
      const form = document.getElementById('remove-a');
      const xhr = {};
      document.dispatchEvent(
        new CustomEvent('htmx:beforeRequest', { detail: { xhr, elt: form } }),
      );
      form.closest('li').remove();
      document.dispatchEvent(
        new CustomEvent('htmx:afterRequest', { detail: { xhr, successful: true } }),
      );

      expect(seen).toHaveBeenCalledTimes(1);
    });

    it('gives each watch its own mark, so two lists never cross', () => {
      const other = vi.fn();
      window.pwaRowRemoved.watch('[data-list-b]', other);

      request(document.getElementById('remove-b'));

      expect(other).toHaveBeenCalledTimes(1);
      expect(seen).not.toHaveBeenCalled();
    });

    it('survives being handed nonsense rather than throwing', () => {
      expect(() => window.pwaRowRemoved.watch(null, seen)).not.toThrow();
      expect(() => window.pwaRowRemoved.watch('[data-list-a]', null)).not.toThrow();
    });

    it('ignores an event carrying no xhr at all', () => {
      document.dispatchEvent(
        new CustomEvent('htmx:afterRequest', { detail: { successful: true } }),
      );

      expect(seen).not.toHaveBeenCalled();
    });
  });
});
