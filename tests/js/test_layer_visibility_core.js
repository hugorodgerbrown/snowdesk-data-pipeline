/*
 * tests/js/test_layer_visibility_core.js — the Bulletins layer's
 * preference-vs-suppression state machine (SNOW-656).
 *
 * The four bullets in the ticket are a state machine, not a pair of
 * booleans, and the one that is easy to get wrong is the restore: turning
 * "Show areas on the map" OFF must return Bulletins to whatever it was
 * BEFORE, so a user who had already switched it off does not get it
 * switched back on for them. That falls out of never overwriting the
 * preference — which is exactly the property a test has to pin, because the
 * obvious implementation (flip the preference, remember the old one) passes
 * a casual read and fails this.
 *
 * `regionsFillLayout`'s four quadrants are here too. The fill is the map's
 * hit-test target, so "Bulletins off" has to mean transparent rather than
 * hidden — and a regression to `visibility: none` would leave visible
 * borders that cannot be tapped, with no error anywhere.
 */

import { beforeAll, describe, expect, it } from 'vitest';

/** @type {any} */
let core;

beforeAll(async () => {
  await import('../../static/js/layer_visibility_core.js');
  core = self.pwaLayerVisibilityCore;
});

describe('preference and suppression', () => {
  it('is effective when the preference is on and nothing suppresses it', () => {
    expect(core.isEffective(core.create(true))).toBe(true);
    expect(core.isEffective(core.create(false))).toBe(false);
  });

  it('is not effective while suppressed, whatever the preference', () => {
    const state = core.suppress(core.create(true), core.SUPPRESSION.DOWNLOADS);
    expect(core.isEffective(state)).toBe(false);
  });

  it('keeps the preference intact through a suppress/unsuppress round trip', () => {
    const on = core.create(true);
    const restored = core.unsuppress(
      core.suppress(on, core.SUPPRESSION.DOWNLOADS),
      core.SUPPRESSION.DOWNLOADS,
    );
    expect(core.isEffective(restored)).toBe(true);
  });

  it('does NOT switch Bulletins on for a user who had already switched it off', () => {
    // The restore case the ticket calls out: Show areas goes on and then off
    // again, over a preference that was already false.
    const off = core.create(false);
    const restored = core.unsuppress(
      core.suppress(off, core.SUPPRESSION.DOWNLOADS),
      core.SUPPRESSION.DOWNLOADS,
    );
    expect(core.isEffective(restored)).toBe(false);
    expect(restored.preference).toBe(false);
  });

  it('treats suppress and unsuppress as idempotent', () => {
    let state = core.create(true);
    state = core.suppress(state, core.SUPPRESSION.DOWNLOADS);
    state = core.suppress(state, core.SUPPRESSION.DOWNLOADS);
    expect(state.suppressedBy).toEqual([core.SUPPRESSION.DOWNLOADS]);
    state = core.unsuppress(state, core.SUPPRESSION.DOWNLOADS);
    state = core.unsuppress(state, core.SUPPRESSION.DOWNLOADS);
    expect(state.suppressedBy).toEqual([]);
  });

  it('stays suppressed while a second, independent reason is still in force', () => {
    let state = core.suppress(core.create(true), core.SUPPRESSION.DOWNLOADS);
    state = core.suppress(state, core.SUPPRESSION.EDIT_RESORTS);
    state = core.unsuppress(state, core.SUPPRESSION.DOWNLOADS);
    expect(core.isEffective(state)).toBe(false);
  });

  it('never mutates the state it is given', () => {
    const original = core.create(true);
    core.suppress(original, core.SUPPRESSION.DOWNLOADS);
    core.choose(original, false);
    expect(original.preference).toBe(true);
    expect(original.suppressedBy).toEqual([]);
  });
});

describe('setPreference is the mechanical write', () => {
  it('leaves every suppression in place, even when turning the preference on', () => {
    // The boot seed and the basemap-swap re-seed both re-read the stored
    // preference. Neither is a user action, so neither may clear the
    // downloads suppression — doing so would reveal the choropleth under
    // the download squares with nobody having asked for it.
    const suppressed = core.suppress(core.create(false), core.SUPPRESSION.DOWNLOADS);
    const next = core.setPreference(suppressed, true);
    expect(next.preference).toBe(true);
    expect(next.suppressedBy).toEqual([core.SUPPRESSION.DOWNLOADS]);
    expect(core.isEffective(next)).toBe(false);
  });
});

describe('mutual exclusion with the downloads overlay', () => {
  it('turning Bulletins ON clears the downloads suppression', () => {
    const suppressed = core.suppress(core.create(false), core.SUPPRESSION.DOWNLOADS);
    const next = core.choose(suppressed, true);
    expect(next.suppressedBy).toEqual([]);
    expect(core.isEffective(next)).toBe(true);
  });

  it('turning Bulletins OFF leaves the downloads suppression alone', () => {
    const suppressed = core.suppress(core.create(true), core.SUPPRESSION.DOWNLOADS);
    const next = core.choose(suppressed, false);
    expect(next.suppressedBy).toEqual([core.SUPPRESSION.DOWNLOADS]);
  });

  it('leaves resort-edit mode in force when Bulletins is turned on', () => {
    // Edit mode is a mode the user is still in, not a competing view
    // control — only the downloads exclusivity is a radio-ish pair.
    const suppressed = core.suppress(core.create(false), core.SUPPRESSION.EDIT_RESORTS);
    const next = core.choose(suppressed, true);
    expect(next.suppressedBy).toEqual([core.SUPPRESSION.EDIT_RESORTS]);
    expect(core.isEffective(next)).toBe(false);
  });

  it('allows both Bulletins and the downloads overlay to be off at once', () => {
    // Not a radio pair: basemap plus borders, nothing else, is reachable.
    const state = core.choose(core.create(true), false);
    expect(core.isEffective(state)).toBe(false);
    expect(state.suppressedBy).toEqual([]);
  });
});

describe('regionsFillLayout', () => {
  const on = () => core.create(true);
  const off = () => core.create(false);

  it('paints an opaque fill with both rows on', () => {
    expect(core.regionsFillLayout(true, on())).toEqual({ visibility: 'visible', opacity: 1 });
  });

  it('keeps the fill installed but transparent when Bulletins is off', () => {
    // The whole point: queryRenderedFeatures still returns features from a
    // fully transparent layer, so borders stay tappable. `visibility: none`
    // here would be the regression.
    expect(core.regionsFillLayout(true, off())).toEqual({ visibility: 'visible', opacity: 0 });
  });

  it('keeps the fill installed but transparent while suppressed', () => {
    const suppressed = core.suppress(on(), core.SUPPRESSION.DOWNLOADS);
    expect(core.regionsFillLayout(true, suppressed)).toEqual({
      visibility: 'visible',
      opacity: 0,
    });
  });

  it('paints the infill with no borders when only Bulletins is on', () => {
    expect(core.regionsFillLayout(false, on())).toEqual({ visibility: 'visible', opacity: 1 });
  });

  it('removes the fill entirely when neither row is on', () => {
    // Nothing of the region tier is drawn, so nothing should answer a tap
    // either — a popup over blank basemap for an invisible region is worse
    // than no popup.
    expect(core.regionsFillLayout(false, off())).toEqual({ visibility: 'none', opacity: 0 });
  });
});

describe('seedFromLegacy', () => {
  it('brings a device carrying the legacy key back with both rows off', () => {
    // snowdesk.map.overlay.l4 === 'false' meant "boundary AND choropleth
    // off". After the split the choropleth must not switch itself on.
    expect(core.seedFromLegacy(null, 'false', true)).toBe(false);
  });

  it('honours a legacy key that was explicitly on', () => {
    expect(core.seedFromLegacy(null, 'true', true)).toBe(true);
  });

  it('falls back to the default when neither key is stored', () => {
    expect(core.seedFromLegacy(null, null, true)).toBe(true);
    expect(core.seedFromLegacy(null, null, false)).toBe(false);
  });

  it('stops consulting the legacy key once the new one exists', () => {
    expect(core.seedFromLegacy('true', 'false', false)).toBe(true);
    expect(core.seedFromLegacy('false', 'true', true)).toBe(false);
  });

  it('treats undefined the same as absent', () => {
    // localStorage.getItem returns null, but a stub or a Map-backed shim can
    // hand back undefined — both mean "nothing stored".
    expect(core.seedFromLegacy(undefined, undefined, true)).toBe(true);
    expect(core.seedFromLegacy(undefined, 'false', true)).toBe(false);
  });
});
