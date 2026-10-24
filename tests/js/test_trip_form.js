/*
 * tests/js/test_trip_form.js — the Plan form's day tiles and read-back
 * footer (SNOW-1215, static/js/trip_form.js).
 *
 * The form posts with no script; what the script adds, and what fails
 * silently if it regresses: a day picked from the Other tile's date picker
 * lands on its own tile when it has one and on Other when it has not; the
 * caption under the tiles follows the checked day; and the footer reads
 * the plan back — the day and time, then the address once found (the
 * place sentence until then) and the day's level when there is one.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import '../../static/js/trip_form.js';

const FORM = `
  <form data-trip-form>
    <fieldset>
      <input type="radio" name="date" value="2026-10-24" id="d0">
      <input type="radio" name="date" value="2026-10-25" id="d1" checked>
      <input type="radio" name="date" value="" id="other" data-day-other>
      <span data-day-other-label><svg></svg></span>
      <template data-day-other-blank><svg data-blank></svg></template>
      <input type="date" data-day-other-input>
      <p data-day-caption="2026-10-24" data-short="Sat 24 Oct" data-rating="Moderate (2)" hidden>Saturday</p>
      <p data-day-caption="2026-10-25" data-short="Sun 25 Oct" data-rating="">Sunday 25 October</p>
      <p data-day-caption="other" hidden></p>
    </fieldset>
    <input type="time" name="start_time" value="08:00">
    <p data-meeting-address data-state="finding">Finding the address…</p>
    <p data-meeting-place>At the route's start</p>
    <p data-trip-readback-when></p>
    <p data-trip-readback-where></p>
  </form>`;

/** @returns {HTMLFormElement} A freshly bound form. */
function mount() {
  document.body.innerHTML = FORM;
  const form = document.querySelector('form');
  window.pwaTripForm.bind(form);
  return form;
}

/** Pick a day from the Other tile's date picker. */
function pickOther(form, iso) {
  const input = form.querySelector('[data-day-other-input]');
  input.value = iso;
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

const text = (form, selector) => form.querySelector(selector).textContent;

describe('formatDay', () => {
  it('words a day three ways in the given language', () => {
    expect(window.pwaTripForm.formatDay('2026-11-12', 'short', 'en-GB')).toBe('Thu 12 Nov');
    expect(window.pwaTripForm.formatDay('2026-11-12', 'long', 'en-GB')).toBe('Thursday 12 November');
    expect(window.pwaTripForm.formatDay('2026-11-12', 'tile', 'en-GB')).toBe('12 Nov');
  });

  it('gives nothing for a value that is not a day', () => {
    expect(window.pwaTripForm.formatDay('', 'short', 'en-GB')).toBe('');
    expect(window.pwaTripForm.formatDay('12/11/2026', 'short', 'en-GB')).toBe('');
  });
});

describe('readbackLines', () => {
  it('joins what is known and drops what is not', () => {
    expect(window.pwaTripForm.readbackLines({
      day: 'Sun 25 Oct', time: '08:00', where: '///a.b.c', rating: 'Considerable (3)',
    })).toEqual({ when: 'Sun 25 Oct · 08:00', where: '///a.b.c · Considerable (3)' });
    expect(window.pwaTripForm.readbackLines({ day: 'Sun 25 Oct', time: '', where: '', rating: '' }))
      .toEqual({ when: 'Sun 25 Oct', where: '' });
  });
});

describe('a bound form', () => {
  beforeEach(() => {
    document.documentElement.lang = 'en-GB';
  });

  it('reads the plan back on load, with the place until the address is found', () => {
    const form = mount();
    expect(text(form, '[data-trip-readback-when]')).toBe('Sun 25 Oct · 08:00');
    expect(text(form, '[data-trip-readback-where]')).toBe("At the route's start");
  });

  it('follows the checked day, its caption and its level', () => {
    const form = mount();
    const today = form.querySelector('#d0');
    today.checked = true;
    today.dispatchEvent(new Event('change', { bubbles: true }));

    expect(form.querySelector('[data-day-caption="2026-10-24"]').hidden).toBe(false);
    expect(form.querySelector('[data-day-caption="2026-10-25"]').hidden).toBe(true);
    expect(text(form, '[data-trip-readback-when]')).toBe('Sat 24 Oct · 08:00');
    expect(text(form, '[data-trip-readback-where]')).toBe("At the route's start · Moderate (2)");
  });

  it('follows the time', () => {
    const form = mount();
    const time = form.querySelector('[name="start_time"]');
    time.value = '07:15';
    time.dispatchEvent(new Event('input', { bubbles: true }));
    expect(text(form, '[data-trip-readback-when]')).toBe('Sun 25 Oct · 07:15');
  });

  it('takes a later day onto Other, which prints it', () => {
    const form = mount();
    pickOther(form, '2026-11-12');

    const other = form.querySelector('[data-day-other]');
    expect(other.checked).toBe(true);
    expect(other.value).toBe('2026-11-12');
    expect(text(form, '[data-day-other-label]')).toBe('12 Nov');
    expect(form.querySelector('[data-day-caption="other"]').hidden).toBe(false);
    expect(text(form, '[data-day-caption="other"]')).toBe('Thursday 12 November');
    expect(text(form, '[data-trip-readback-when]')).toBe('Thu 12 Nov · 08:00');
  });

  it('puts a day the week has back on its own tile, and clears Other', () => {
    const form = mount();
    pickOther(form, '2026-11-12');
    pickOther(form, '2026-10-24');

    expect(form.querySelector('#d0').checked).toBe(true);
    expect(form.querySelector('[data-day-other]').value).toBe('');
    expect(form.querySelector('[data-day-other-label] [data-blank]')).not.toBeNull();
  });

  it('reads the address back once the picker finds it', async () => {
    const form = mount();
    const address = form.querySelector('[data-meeting-address]');
    address.setAttribute('data-state', 'found');
    address.textContent = '///filled.count.soap';
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(text(form, '[data-trip-readback-where]')).toBe('///filled.count.soap');
  });

  it('binds a form once', () => {
    const form = mount();
    window.pwaTripForm.bind(form);
    pickOther(form, '2026-11-12');
    expect(text(form, '[data-trip-readback-when]')).toBe('Thu 12 Nov · 08:00');
  });
});
