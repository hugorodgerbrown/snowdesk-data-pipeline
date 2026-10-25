/*
 * tests/js/test_day_strips.js — moving the day strips' selected block
 * (static/js/day_strips.js, SNOW-1219).
 *
 * A pressed block button and a tap or drag along the timeline both move one
 * selection: `--block` on the section, `data-selected` on every
 * block-indexed element and the two boundary labels, and `aria-pressed`
 * on the buttons. Delegated, so strips injected after load work too.
 */

import { beforeAll, beforeEach, describe, expect, it } from 'vitest';

function strips() {
  const buttons = Array.from({ length: 8 }, (_, i) =>
    `<button type="button" data-block="${i}" aria-pressed="${i === 3}"></button>`).join('');
  const figures = Array.from({ length: 8 }, (_, i) =>
    `<span class="fig" data-block="${i}" ${i === 3 ? 'data-selected' : ''}></span>`).join('');
  const bounds = Array.from({ length: 9 }, (_, i) =>
    `<span data-boundary="${i}" ${i === 3 || i === 4 ? 'data-selected' : ''}></span>`).join('');
  return `<section data-day-strips style="--block: 3">${bounds}${buttons}
    <div data-day-strips-track></div><div class="plot"></div>${figures}</section>`;
}

/** @param {Element} section */
function selected(section) {
  return {
    block: section.style.getPropertyValue('--block'),
    figures: Array.from(section.querySelectorAll('.fig[data-selected]'))
      .map((el) => el.getAttribute('data-block')),
    bounds: Array.from(section.querySelectorAll('[data-boundary][data-selected]'))
      .map((el) => el.getAttribute('data-boundary')),
    pressed: Array.from(section.querySelectorAll('button[aria-pressed="true"]'))
      .map((el) => el.getAttribute('data-block')),
  };
}

beforeAll(async () => {
  await import('../../static/js/day_strips_core.js');
  await import('../../static/js/day_strips.js');
});

beforeEach(() => {
  document.body.innerHTML = strips();
});

describe('day strips selection', () => {
  it('moves everything to a pressed block', () => {
    const section = document.querySelector('[data-day-strips]');
    section.querySelector('button[data-block="6"]').click();

    expect(selected(section)).toEqual({
      block: '6', figures: ['6'], bounds: ['6', '7'], pressed: ['6'],
    });
  });

  it('selects the block under a tap on the timeline, and follows a drag', () => {
    const section = document.querySelector('[data-day-strips]');
    const track = section.querySelector('[data-day-strips-track]');
    track.getBoundingClientRect = () => ({ left: 0, width: 320, top: 0, height: 28 });

    track.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 10 }));
    expect(selected(section).block).toBe('0');

    document.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 300 }));
    expect(selected(section).figures).toEqual(['7']);

    document.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
    document.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 100 }));
    expect(selected(section).block).toBe('7');
  });

  it('leaves the selection alone when a chart is touched', () => {
    const section = document.querySelector('[data-day-strips]');
    const plot = section.querySelector('.plot');
    plot.getBoundingClientRect = () => ({ left: 0, width: 320, top: 0, height: 90 });

    plot.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 10 }));
    document.dispatchEvent(new MouseEvent('pointermove', { bubbles: true, clientX: 300 }));
    document.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));

    expect(selected(section).block).toBe('3');
  });

  it('works on strips injected after load', () => {
    document.body.innerHTML = '<div id="sheet"></div>';
    document.getElementById('sheet').innerHTML = strips();
    const section = document.querySelector('[data-day-strips]');
    section.querySelector('button[data-block="1"]').click();

    expect(selected(section).pressed).toEqual(['1']);
  });

  it('leaves another day\'s strips alone', () => {
    document.body.innerHTML = strips() + strips();
    const [first, second] = document.querySelectorAll('[data-day-strips]');
    first.querySelector('button[data-block="0"]').click();

    expect(selected(first).block).toBe('0');
    expect(selected(second).block).toBe('3');
  });
});
