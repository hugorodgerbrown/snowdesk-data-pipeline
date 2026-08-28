/*
 * tests/js/test_account_observations.js — the account page's reports list
 * (SNOW-752).
 *
 * Scenario: none — a document event announcing a row removal, observed
 * through synthetic htmx lifecycle events. No browser is needed for it.
 *
 * /account/observations/ carried no module at all until this ticket: its
 * rows have one control, a trash, and the trash is a plain HTMX form whose
 * empty 200 the row's own `hx-swap` turns into a removal with no JS. What
 * that missed is what this suite asserts — the swap empties one `<li>` and
 * nothing else, so a page which has just lost its last report keeps
 * rendering as a list of none. observations:list's empty state is a
 * server-side `{% if not observations %}` clause, and only a fresh response
 * can carry it.
 *
 * The refetch itself is the list wrapper's own `hx-get` (my_observations.html
 * declares it, triggered by this event), so what is asserted here is only
 * the announcement: `static/js/account_observations.js` is a selector and an
 * event name, deliberately. Recognising the removal belongs to
 * `window.pwaRowRemoved` and is covered in tests/js/test_row_removed.js.
 *
 * The sibling of test_account_routes.js, and shorter by everything that
 * suite spends on rename: an observation has no name to change.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/row_removed.js';

const UUID = '11111111-2222-3333-4444-555555555555';

await import('../../static/js/account_observations.js');

/** Paint the page as my_observations.html renders it.
 *
 * The wrapper's own `hx-get` is included: it is what re-reads the list, and
 * it is also the request that must NOT be mistaken for a removal.
 *
 * @returns {void}
 */
function renderPage() {
  document.body.innerHTML = `
    <div data-observation-list
         hx-get="/partials/report/list/"
         hx-trigger="snowdesk:reports-changed from:document"
         hx-target="this"
         hx-swap="innerHTML">
      <div data-testid="observation-list">
        <ul>
          <li id="observation-${UUID}">
            <span data-row-label>Avalanche</span>
            <form data-row-remove hx-post="/partials/report/${UUID}/delete/">
              <input type="hidden" name="csrfmiddlewaretoken" value="tok">
              <button type="submit" aria-label="Remove Avalanche">bin</button>
            </form>
          </li>
        </ul>
      </div>
    </div>`;
}

/**
 * Drive one htmx request lifecycle from `elt`.
 *
 * @param {Element} elt The element htmx names as the source.
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

describe('a row removed from /account/observations/', () => {
  let changed;

  beforeEach(() => {
    renderPage();
    changed = vi.fn();
    document.addEventListener('snowdesk:reports-changed', changed);
  });

  it('announces the change, so the page re-reads its own list', () => {
    request(document.querySelector('[data-row-remove]'));

    expect(changed).toHaveBeenCalledTimes(1);
    document.removeEventListener('snowdesk:reports-changed', changed);
  });

  it('uses the same event name the map listens for', () => {
    // "Your reports changed" is one fact. The map refetches the
    // community-reports layer on it (static/js/map.js) and this page
    // re-reads its rows; neither knows about the other, and a second name
    // for the same fact would be two things to keep in step.
    const detail = [];
    document.addEventListener('snowdesk:reports-changed', (e) => detail.push(e.type));

    request(document.querySelector('[data-row-remove]'));

    expect(detail).toEqual(['snowdesk:reports-changed']);
    document.removeEventListener('snowdesk:reports-changed', changed);
  });

  it('stays quiet when the delete failed', () => {
    // Nothing was removed, so the list still says what it said.
    request(document.querySelector('[data-row-remove]'), false);

    expect(changed).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:reports-changed', changed);
  });

  it('does not treat the wrapper’s own re-read as another removal', () => {
    // The wrapper carries the hx-get this event triggers. Counted as a
    // removal it would announce again, and the page would refetch its list
    // forever.
    request(document.querySelector('[data-observation-list]'));

    expect(changed).not.toHaveBeenCalled();
    document.removeEventListener('snowdesk:reports-changed', changed);
  });
});
