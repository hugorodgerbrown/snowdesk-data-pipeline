/*
 * tests/js/test_trip_share.js — the trip page's share URL builders
 * (SNOW-821).
 *
 * `trip_share.js` does two things worth asserting without a browser: it
 * substitutes a uuid into a server-supplied URL template, and it refuses to
 * build a URL from a missing half. Everything else it does is
 * `window.pwaShare`'s, which has its own tests.
 *
 * The templates come from the PAGE (`data-trip-share-url`), never from a
 * string in this module — a JS module must not know how the project spells
 * its URLs — so these builders take the template as an argument and that is
 * the contract under test.
 *
 * SNOW-834 adds the third: WHERE an outcome is announced. The trips list
 * renders a toast per kind and the trip page renders none, so the same
 * click has to say the same thing in two different surfaces — and must
 * never paint a failure in the success palette.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/trip_share.js';

const core = self.pwaTripShareCore;

const UUID = '11111111-2222-3333-4444-555555555555';
const SHARE_TEMPLATE = '/trips/__UUID__/share/';
const REVOKE_TEMPLATE = '/trips/__UUID__/share/revoke/';

describe('shareUrlFor', () => {
  it('substitutes the uuid into the server-supplied template', () => {
    expect(core.shareUrlFor(SHARE_TEMPLATE, UUID)).toBe(
      `/trips/${UUID}/share/`
    );
  });

  it('returns an empty string when either half is missing', () => {
    // A missing template means the page did not render the attribute, and
    // a missing uuid means the button did not carry one. Building
    // `/trips/undefined/share/` and POSTing it would be worse than doing
    // nothing.
    expect(core.shareUrlFor('', UUID)).toBe('');
    expect(core.shareUrlFor(SHARE_TEMPLATE, '')).toBe('');
    expect(core.shareUrlFor(null, null)).toBe('');
  });
});

describe('revokeUrlFor', () => {
  it('substitutes into its own template, not the share one', () => {
    // Separate templates rather than one plus a suffix: the two paths are
    // reversed independently server-side, so appending "revoke/" here
    // would be this module inventing a URL shape.
    expect(core.revokeUrlFor(REVOKE_TEMPLATE, UUID)).toBe(
      `/trips/${UUID}/share/revoke/`
    );
  });

  it('returns an empty string when either half is missing', () => {
    expect(core.revokeUrlFor('', UUID)).toBe('');
    expect(core.revokeUrlFor(REVOKE_TEMPLATE, '')).toBe('');
  });
});

describe('announcing an outcome', () => {
  /** Markup for a page carrying the trips list's toast group. */
  const TOASTS = `
    <div data-toast-group="trip" data-kind="success" id="ok" class="hidden">
      <span data-toast-body></span>
    </div>
    <div data-toast-group="trip" data-kind="error" id="bad" class="hidden">
      <span data-toast-body></span>
    </div>`;

  const BUTTON = `
    <div data-trip-share-url="${SHARE_TEMPLATE}">
      <input name="csrfmiddlewaretoken" value="tok">
      <button data-trip-share="${UUID}">Share</button>
    </div>`;

  /**
   * Press Share and let the click's promise chain settle.
   * @returns {Promise<void>}
   */
  async function pressShare() {
    document.querySelector('[data-trip-share]').dispatchEvent(
      new MouseEvent('click', { bubbles: true })
    );
    // Two turns: createShare resolves, then shareOrCopy does.
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  }

  /**
   * Stub the shared share helper with a fixed outcome.
   * @param {string} outcome 'copied', 'shared' or 'failed'.
   */
  function stubShare(outcome) {
    window.pwaShare = {
      createShare: vi.fn(() => Promise.resolve('https://x/trips/s/abc/')),
      shareOrCopy: vi.fn(() => Promise.resolve(outcome)),
    };
  }

  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('writes a clipboard outcome into the success toast', async () => {
    document.body.innerHTML = BUTTON + TOASTS;
    stubShare('copied');

    await pressShare();

    const toast = document.getElementById('ok');
    expect(toast.classList.contains('hidden')).toBe(false);
    expect(toast.querySelector('[data-toast-body]').textContent).toBe(
      'Link copied.'
    );
  });

  it('writes a failure into the error toast, not the success one', async () => {
    // The reason the toasts are a group and not one element: a failure
    // painted in the success palette reads as a success.
    document.body.innerHTML = BUTTON + TOASTS;
    stubShare('failed');

    await pressShare();

    expect(document.getElementById('bad').classList.contains('hidden')).toBe(
      false
    );
    expect(document.getElementById('ok').classList.contains('hidden')).toBe(
      true
    );
  });

  it('hides the rest of the group when it shows one', async () => {
    // The trips list opens with the success toast already shown, having
    // just confirmed a create. A share that then fails must replace it
    // rather than stack on top of it — both are fixed to the same spot.
    document.body.innerHTML = BUTTON + TOASTS;
    document.getElementById('ok').classList.remove('hidden');
    stubShare('failed');

    await pressShare();

    expect(document.getElementById('ok').classList.contains('hidden')).toBe(
      true
    );
  });

  it('says nothing at all when the sheet was taken', async () => {
    // A user who shared has seen the sheet; a user who CANCELLED has
    // declined, and telling either that a link was copied contradicts them.
    document.body.innerHTML = BUTTON + TOASTS;
    stubShare('shared');

    await pressShare();

    expect(document.getElementById('ok').classList.contains('hidden')).toBe(
      true
    );
    expect(document.getElementById('bad').classList.contains('hidden')).toBe(
      true
    );
  });

  it('falls back to the trip page\'s state paragraph', async () => {
    // The trip page renders no toast: the paragraph under the buttons is
    // already what describes the link's state.
    document.body.innerHTML =
      BUTTON + '<p data-testid="trip-share-state">old</p>';
    stubShare('copied');

    await pressShare();

    expect(
      document.querySelector('[data-testid="trip-share-state"]').textContent
    ).toBe('Link copied.');
  });
});
