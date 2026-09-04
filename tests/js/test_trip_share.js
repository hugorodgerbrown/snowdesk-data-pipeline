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
 */

import { describe, expect, it } from 'vitest';

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
