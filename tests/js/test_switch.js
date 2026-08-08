/*
 * tests/js/test_switch.js — Vitest DOM tests for
 * templates/includes/_switch.html (SNOW-645).
 *
 * There is no JS module behind this partial — the whole control is a real
 * `<input type="checkbox" role="switch">` wrapped in a `<label>`, driven
 * by CSS `peer`/`peer-checked` variants (see the partial's own docstring).
 * "Testing" it here means proving the MARKUP SHAPE genuinely activates on
 * a real click, not proving any JS logic — there is none.
 *
 * The bug this guards against (see the partial's own "SNOW-645 review"
 * comment): the wrapping element used to be a bare `<span>`, on the
 * reasoning that the input already sits inside it so no explicit
 * association was needed for KEYBOARD activation. True, but irrelevant to
 * POINTER activation — the track and thumb are both `pointer-events-none`
 * (a click passes through them to whatever sits beneath, in a REAL
 * browser), and the `sr-only` input is clipped to a 1×1 box, so a click
 * anywhere on the visible pill landed on the inert `<span>` and did
 * nothing. Silent, no console error, no way to notice from
 * `render_to_string` output — only a real click showed it.
 *
 * Every test below dispatches a genuine `.click()` on the TRACK element
 * (the visible child a real pointer click lands on), never a synthetic
 * `change`/`click` fired directly at the `<input>` — exactly the
 * distinction that let the bare-`<span>` regression ship past every
 * earlier check, all of which dispatched events instead of clicking.
 * `.click()`'s bubble-then-label-forwarding activation behaviour is a
 * real jsdom implementation of the HTML Standard's label activation
 * behaviour (verified directly: a bare `<span>` wrapper measurably does
 * NOT forward the click in jsdom either, so this suite can fail against
 * the old shape, not just pass against the new one) — jsdom does not lay
 * out or apply CSS at all (there is no `pointer-events-none` to bypass
 * here either way), so this cannot observe the CSS half of the original
 * bug, only the markup-association half. That is the one `render_to_string`
 * could never have caught, and is the whole point of this file.
 */

import { describe, expect, it } from 'vitest';

/**
 * The partial's own rendered markup (templates/includes/_switch.html),
 * hand-copied — Vitest cannot render a Django template (see the standing
 * convention other DOM-fixture test files in this directory follow).
 *
 * @param {{id?: string, checked?: boolean, disabled?: boolean}} [opts]
 * @returns {string}
 */
function switchMarkup({ id = 'sw', checked = false, disabled = false } = {}) {
  return `
    <label
        for="${id}"
        class="relative inline-flex w-11 min-h-11 shrink-0 cursor-pointer items-center has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-50"
    >
        <input
            id="${id}"
            type="checkbox"
            role="switch"
            class="peer sr-only"
            ${checked ? 'checked' : ''}
            ${disabled ? 'disabled' : ''}
        >
        <span
            aria-hidden="true"
            class="pointer-events-none absolute inset-0 m-auto h-5 w-9 rounded-full bg-border-strong transition-colors peer-checked:bg-accent"
        ></span>
        <span
            aria-hidden="true"
            class="pointer-events-none absolute inset-y-0 left-1.5 my-auto h-4 w-4 rounded-full bg-accent-text shadow transition-transform peer-checked:translate-x-4"
        ></span>
    </label>`;
}

function build(opts) {
  document.body.innerHTML = switchMarkup(opts);
  return {
    input: document.getElementById(opts?.id || 'sw'),
    track: document.querySelector('[aria-hidden="true"]'),
    label: document.querySelector('label'),
  };
}

describe('a real click on the track activates the input (the dead-track regression)', () => {
  it('toggles checked from false to true on a track click', () => {
    const { input, track } = build();
    expect(input.checked).toBe(false);

    track.click();

    expect(input.checked).toBe(true);
  });

  it('toggles checked from true to false on a track click', () => {
    const { input, track } = build({ checked: true });
    expect(input.checked).toBe(true);

    track.click();

    expect(input.checked).toBe(false);
  });

  it('fires a real change event, not just a property flip', () => {
    const { input, track } = build();
    let changeFired = false;
    input.addEventListener('change', () => { changeFired = true; });

    track.click();

    expect(changeFired).toBe(true);
  });

  it('a click on the label itself (outside both spans) also activates it', () => {
    const { input, label } = build();

    label.click();

    expect(input.checked).toBe(true);
  });

  it('does not toggle when the input is disabled', () => {
    const { input, track } = build({ disabled: true });

    track.click();

    expect(input.checked).toBe(false);
  });

  it('regression proof: a bare <span> wrapper (the old shape) does not forward the click', () => {
    // Not a test of production code — a control proving this suite can
    // actually fail, by rebuilding the exact markup shape the SNOW-645
    // review replaced (a <span> instead of a <label for="…">) and
    // confirming a track click reaches nothing.
    document.body.innerHTML = `
      <span class="relative inline-flex w-11 min-h-11">
        <input id="sw-old" type="checkbox" role="switch" class="peer sr-only">
        <span aria-hidden="true" class="pointer-events-none absolute inset-0 m-auto h-5 w-9"></span>
      </span>`;
    const input = document.getElementById('sw-old');
    const track = document.querySelector('[aria-hidden="true"]');

    track.click();

    expect(input.checked).toBe(false);
  });
});
