/*
 * tests/js/test_favourites.js — Vitest unit tests for static/js/favourites.js's
 * pin-detail HTMX request bookkeeping (finding M5 in
 * docs/code-reviews/2026-08-03-js-review.md).
 *
 * favourites.js listens for `htmx:beforeRequest` / `htmx:afterRequest` on
 * `document`, so every HTMX request anywhere on the page passes through both
 * handlers — including report.js's form-load `htmx.ajax()`, which shares the
 * map homepage with the favourites surface. The two tests below reproduce the
 * two interleavings that state showed to be indistinguishable when the
 * in-flight marker lived on the module rather than on the request.
 *
 * The handlers read three fields off the event detail — `elt`, `xhr` and
 * `successful` — so the tests drive them with hand-built CustomEvents and no
 * htmx in the loop. `xhr` is a bare object: nothing here calls into it, it
 * only has to be the same identity across a request's two events, which is
 * what htmx guarantees.
 *
 * The module is an IIFE that returns early unless #favourite-add-btn,
 * #favourite-sheet and #favourite-create-template are all present, and it
 * captures those three elements once at load, so the fixture is built before
 * the dynamic import below and no test replaces it. The
 * [data-favourite-detail] container is re-queried on every afterRequest, so
 * each test owns its contents.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

document.body.innerHTML = `
  <button id="favourite-add-btn"
          data-favourites-eligible="true"
          data-signin-url="/sign-in/"
          data-favourite-create-url="/favourites/create/"
          data-favourite-rename-url-template="/favourites/__UUID__/rename/"
          data-favourite-delete-url-template="/favourites/__UUID__/delete/"></button>
  <div id="favourite-sheet" hidden></div>
  <template id="favourite-create-template"></template>
  <div data-favourite-detail></div>
`;

await import('../../static/js/favourites.js');

const detailContainer = document.querySelector('[data-favourite-detail]');
const teardowns = [];

afterEach(() => {
  while (teardowns.length) teardowns.pop()();
  delete window.pwaTelemetry;
  detailContainer.innerHTML = '';
});

/**
 * Record every dispatch of a document-level custom event, and register the
 * listener's removal for the enclosing test's teardown.
 *
 * @param {string} name
 * @returns {Array<CustomEvent>} live array, appended to as the event fires
 */
function recordEvent(name) {
  const seen = [];
  const handler = (evt) => seen.push(evt);
  document.addEventListener(name, handler);
  teardowns.push(() => document.removeEventListener(name, handler));
  return seen;
}

/**
 * Dispatch one htmx lifecycle event on `document` with the given detail.
 *
 * @param {string} name
 * @param {object} detail
 */
function fireHtmx(name, detail) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

/** Put a rename form inside the detail popup and return it. */
function openDetailPopup() {
  detailContainer.innerHTML = '<form id="favourite-rename-form"></form>';
  return detailContainer.querySelector('#favourite-rename-form');
}

describe('pin-detail htmx request correlation', () => {
  it('still reports a rename when another surface starts a request mid-flight', () => {
    const renameForm = openDetailPopup();
    const renameXhr = {};
    const reportXhr = {};
    const changed = recordEvent('snowdesk:favourites-changed');

    fireHtmx('htmx:beforeRequest', { elt: renameForm, xhr: renameXhr });
    // report.js's loadForm() calls htmx.ajax() with no source element, so
    // htmx reports document.body as the request's elt.
    fireHtmx('htmx:beforeRequest', { elt: document.body, xhr: reportXhr });

    // The rename lands: the server's _favourite.html (carrying
    // [data-favourite-uuid]) swaps back into the container.
    detailContainer.innerHTML = '<div data-favourite-uuid="a1b2"></div>';
    fireHtmx('htmx:afterRequest', {
      elt: renameForm,
      xhr: renameXhr,
      successful: true,
    });

    expect(changed).toHaveLength(1);
  });

  it('does not report a delete when a request from another surface completes', () => {
    const renameForm = openDetailPopup();
    const renameXhr = {};
    const reportXhr = {};
    const emit = vi.fn();
    window.pwaTelemetry = { emit };
    const changed = recordEvent('snowdesk:favourites-changed');
    const closed = recordEvent('snowdesk:favourite-detail-close');

    fireHtmx('htmx:beforeRequest', { elt: renameForm, xhr: renameXhr });
    // The user closes the popup while the rename is still in flight, then
    // report.js's form load — started earlier — completes.
    detailContainer.innerHTML = '';
    fireHtmx('htmx:afterRequest', {
      elt: document.body,
      xhr: reportXhr,
      successful: true,
    });

    expect(emit).not.toHaveBeenCalled();
    expect(changed).toHaveLength(0);
    expect(closed).toHaveLength(0);
  });
});
