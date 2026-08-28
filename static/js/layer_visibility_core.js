/*
 * static/js/layer_visibility_core.js — the Bulletins layer's visibility
 * state machine (SNOW-656).
 *
 * The map's `l4` overlay key used to drive four layers at once —
 * `regions-fill`, `regions-line`, `regions-label` and
 * `bulletin-groupings-line` — so the micro-region GEOGRAPHY and the BULLETIN
 * DATA painted onto it could only be shown or hidden together. SNOW-656
 * split that into two rows, **Micro regions** (the boundary and its label)
 * and **Bulletins** (the coloured infill and the dissolved bulletin
 * boundary), because the downloaded-areas overlay and the danger choropleth
 * paint the same polygons and were unreadable together — the geography can
 * stay up throughout, since borders interfere with nothing, while only the
 * infill yields. The two layers coexist now (SNOW-663, below), but the split
 * outlived its occasion: the boundary is fixed and the infill is date-bound,
 * and one control for both was always a control for two different things.
 *
 * Two pieces of state, and conflating them is the bug this module exists to
 * prevent:
 *
 *   - the user's PREFERENCE for Bulletins, which is persisted; and
 *   - its EFFECTIVE visibility, which is the preference AND-ed with any
 *     active suppression.
 *
 * A suppression is a reason some other mode is temporarily overriding the
 * preference — today only staff resort-edit mode
 * (`SUPPRESSION.EDIT_RESORTS`). It has to restore CLEANLY: a user who had
 * already switched Bulletins off must not find it switched back on for them
 * when the suppression lifts. Keeping the preference as the only persisted
 * value gets that for free — there is no "remembered" second copy to drift,
 * and a reload during a suppression comes back with what the user actually
 * chose rather than with the suppressed value.
 *
 * SNOW-656 had a second reason, `SUPPRESSION.DOWNLOADS`: the downloaded-areas
 * overlay painted a second translucent tint over the polygons the choropleth
 * fills, so the two could not both be on. SNOW-663 made the squares a hatch
 * the danger colour reads through, and the exclusivity went with it. The
 * reason and the `choose()` transition that cleared it are gone — a
 * transition whose only difference from `setPreference` was clearing
 * something nothing sets is two names for one operation. The `suppress` /
 * `unsuppress` machinery stays: resort-edit mode still uses it, and it is
 * what a future exclusivity would register through.
 *
 * Lives here rather than in `map.js` for the usual reason: `map.js` is one
 * file of IIFEs that cannot be imported under jsdom, and the four-quadrant
 * truth table below is exactly the kind of thing that needs a test.
 *
 * Every function is pure — states are frozen and transitions return a new
 * state rather than mutating the one passed in.
 *
 * Exports (frozen `self.pwaLayerVisibilityCore`):
 *
 *   SUPPRESSION
 *   create(preference)
 *   setPreference(state, next)   — the only writer of the preference
 *   suppress(state, reason) / unsuppress(state, reason)
 *   isEffective(state)
 *   regionsFillLayout(microRegionsOn, state)
 *   seedFromLegacy(rawNew, rawLegacy, dflt)
 */

(function () {
  'use strict';

  /**
   * The reasons something other than the user's own preference can hide the
   * Bulletins layers.
   *
   * `EDIT_RESORTS` is the staff resort-edit mode, which hid `regions-fill`
   * directly before this module existed — routing it through the same path
   * keeps the number of writers of that layer's visibility at one. It is the
   * only reason left: `DOWNLOADS` went with the exclusivity SNOW-663 removed
   * (see the module header).
   *
   * Still an object rather than a bare string constant. `suppress` /
   * `unsuppress` take a reason and hold a LIST of them precisely so a second
   * one can be added without either function changing, and a one-member enum
   * is what that shape looks like while there is one mode.
   */
  const SUPPRESSION = Object.freeze({
    EDIT_RESORTS: 'edit-resorts',
  });

  /**
   * The five steps the Bulletins control offers, weakest first.
   *
   * SNOW-656: this replaced an on/off toggle. The choropleth's right strength
   * is not a binary — it depends on the basemap under it, on whether the user
   * is reading danger or reading terrain, and on the display. A toggle forced
   * that judgement into "all or nothing" and the answer was usually neither.
   *
   * Five fixed steps rather than a continuous slider: they are all reachable
   * with one tap on a 44px target, they are the same on every device, and
   * they persist as one of five known values rather than an arbitrary float
   * nobody chose deliberately.
   *
   * `0` is the off position — the toggle this replaced, kept as one end of
   * the range rather than a separate control.
   */
  const STEPS = Object.freeze([0, 0.25, 0.5, 0.75, 1]);

  /** The step a device with no stored preference starts at. */
  const DEFAULT_STEP = 0.5;

  /**
   * Snap an arbitrary number to the nearest step.
   *
   * Storage and the URL are both places a value can arrive from outside this
   * module, so nothing downstream should have to trust that it is one of the
   * five.
   *
   * @param {number} value
   * @returns {number} A member of `STEPS`.
   */
  function nearestStep(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_STEP;
    return STEPS.reduce((best, s) => (Math.abs(s - n) < Math.abs(best - n) ? s : best), STEPS[0]);
  }

  /**
   * A new state holding a preference and no suppressions.
   *
   * @param {number} preference The user's persisted Bulletins step, 0–1.
   * @returns {{preference: number, suppressedBy: string[]}} Frozen state.
   */
  function create(preference) {
    return Object.freeze({
      preference: nearestStep(preference),
      suppressedBy: Object.freeze([]),
    });
  }

  /**
   * The opacity the choropleth should actually be painted at: the user's
   * step, or 0 while anything is suppressing it.
   *
   * @param {{preference: number, suppressedBy: string[]}} state
   * @returns {number} 0–1.
   */
  function effectiveOpacity(state) {
    return state.suppressedBy.length === 0 ? state.preference : 0;
  }

  /**
   * Whether the Bulletins layers are drawn at all — the step is above zero
   * and nothing is suppressing it.
   *
   * Kept as a named predicate because several callers ask the yes/no question
   * (should the bulletin-boundary layer be visible, should a scrubbed date
   * refetch it) and none of them care by how much.
   *
   * @param {{preference: number, suppressedBy: string[]}} state
   * @returns {boolean}
   */
  function isEffective(state) {
    return effectiveOpacity(state) > 0;
  }

  /**
   * Write the preference and nothing else.
   *
   * The ONLY way the preference moves — the boot seed, the re-seed after a
   * basemap swap, and the user's own click on the step control all come
   * through here. It must not disturb any suppression: a basemap swap during
   * resort-edit mode has to leave that mode's suppression in force, and a
   * user picking a step is choosing what to see when the mode ENDS, not
   * asking to leave it.
   *
   * A second entry point, `choose()`, existed while the downloads overlay
   * was mutually exclusive with this one: it cleared that suppression on the
   * way through, on the grounds that the user had just said which of the two
   * they wanted. SNOW-663 ended the exclusivity, which left `choose` doing
   * nothing this does not, so it is gone.
   *
   * @param {{preference: boolean, suppressedBy: string[]}} state
   * @param {boolean} next
   * @returns {{preference: boolean, suppressedBy: string[]}} A new state.
   */
  function setPreference(state, next) {
    return Object.freeze({ preference: nearestStep(next), suppressedBy: state.suppressedBy });
  }

  /**
   * Add a suppression reason. Idempotent — suppressing twice for the same
   * reason is the same as suppressing once, so a caller need not track
   * whether it has already done so.
   *
   * @param {{preference: boolean, suppressedBy: string[]}} state
   * @param {string} reason A `SUPPRESSION` value.
   * @returns {{preference: boolean, suppressedBy: string[]}} A new state.
   */
  function suppress(state, reason) {
    if (state.suppressedBy.indexOf(reason) !== -1) return state;
    return Object.freeze({
      preference: state.preference,
      suppressedBy: Object.freeze(state.suppressedBy.concat([reason])),
    });
  }

  /**
   * Remove a suppression reason, restoring the preference if it was the
   * last one. Idempotent, for the same reason `suppress` is.
   *
   * @param {{preference: boolean, suppressedBy: string[]}} state
   * @param {string} reason A `SUPPRESSION` value.
   * @returns {{preference: boolean, suppressedBy: string[]}} A new state.
   */
  function unsuppress(state, reason) {
    if (state.suppressedBy.indexOf(reason) === -1) return state;
    return Object.freeze({
      preference: state.preference,
      suppressedBy: Object.freeze(
        state.suppressedBy.filter((existing) => existing !== reason),
      ),
    });
  }

  /**
   * How `regions-fill` must be painted, given both rows' state.
   *
   * The fill is the map's hit-test target: region selection resolves through
   * `queryRenderedFeatures(e.point, {layers: ['regions-fill', 'resorts-pin']})`
   * and the hover cursor is bound to it. A layer at `visibility: none`
   * returns nothing from that query, so hiding the choropleth by visibility
   * would leave visible borders that cannot be tapped — no popup, no pointer
   * cursor. "Bulletins off" therefore means INVISIBLE, not ABSENT:
   * `fill-opacity: 0`, which `queryRenderedFeatures` still sees through.
   *
   * `visibility` is not simply pinned to `visible`, though. With both rows
   * off there is nothing of the region tier on screen at all, and a fully
   * transparent fill would still answer taps over blank basemap with a popup
   * for a region the user cannot see. So the layer is installed whenever
   * EITHER row is on, and transparent whenever Bulletins is not effective:
   *
   *   micro on,  bulletins on   → visible, opaque   (the default map)
   *   micro on,  bulletins off  → visible, transparent (borders, still tappable)
   *   micro off, bulletins on   → visible, opaque   (infill with no borders)
   *   micro off, bulletins off  → none              (nothing drawn, nothing tappable)
   *
   * The opacity is 1 rather than a translucent value because the choropleth's
   * colours are pre-blended against a fixed backdrop — see
   * `docs/decisions/choropleth-blended-not-translucent.md`. That decision
   * fixes WHICH colour the fill is; this one gates WHETHER it paints.
   *
   * @param {boolean} microRegionsOn Whether the Micro regions row is on.
   * @param {{preference: boolean, suppressedBy: string[]}} state
   * @returns {{visibility: string, opacity: number}} MapLibre layout/paint values.
   */
  function regionsFillLayout(microRegionsOn, state) {
    const opacity = effectiveOpacity(state);
    return {
      visibility: microRegionsOn || opacity > 0 ? 'visible' : 'none',
      opacity: opacity,
    };
  }

  /**
   * Resolve a split overlay key's stored preference, seeding it once from
   * the key it was split out of.
   *
   * `snowdesk.map.overlay.l4` used to mean "micro-region boundary AND the
   * bulletin data painted on it". After the split it means the boundary
   * alone, and Bulletins has its own key. A device carrying `l4=false`
   * today has to come back with BOTH rows off rather than silently
   * acquiring a switched-on choropleth it never asked for — so the legacy
   * value is read as the seed until the new key exists, and ignored from
   * then on.
   *
   * Reads raw `localStorage` strings rather than booleans so "absent" and
   * "explicitly false" stay distinguishable, which is the whole point.
   *
   * @param {?string} rawNew The new key's raw stored value, or null.
   * @param {?string} rawLegacy The legacy key's raw stored value, or null.
   * @param {boolean} dflt The default for a device carrying neither.
   * @returns {boolean}
   */
  function seedFromLegacy(rawNew, rawLegacy, dflt) {
    const fallback = nearestStep(dflt === undefined ? DEFAULT_STEP : dflt);
    // The new key, which holds one of the five steps as a number. It also had
    // a short life holding 'true'/'false', while the control was still a
    // toggle — a device that saw that build has to come back on a step, not
    // on NaN.
    if (rawNew !== null && rawNew !== undefined) {
      if (rawNew === 'true') return fallback;
      if (rawNew === 'false') return 0;
      return nearestStep(rawNew);
    }
    // The pre-split key, which meant "micro-region boundary AND the bulletin
    // data painted on it". Off there has to mean off here — a device that had
    // the tier switched off must not come back with a choropleth it never
    // asked for.
    if (rawLegacy !== null && rawLegacy !== undefined) {
      return rawLegacy === 'false' ? 0 : fallback;
    }
    return fallback;
  }

  self.pwaLayerVisibilityCore = Object.freeze({
    SUPPRESSION: SUPPRESSION,
    STEPS: STEPS,
    DEFAULT_STEP: DEFAULT_STEP,
    nearestStep: nearestStep,
    create: create,
    setPreference: setPreference,
    suppress: suppress,
    unsuppress: unsuppress,
    isEffective: isEffective,
    effectiveOpacity: effectiveOpacity,
    regionsFillLayout: regionsFillLayout,
    seedFromLegacy: seedFromLegacy,
  });
})();
