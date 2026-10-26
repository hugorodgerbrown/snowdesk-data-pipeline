/*
 * tests/js/test_sheet_playback_core.js — the pieces an object sheet uses to
 * pause while the timeline plays (static/js/sheet_playback_core.js,
 * SNOW-1239).
 *
 * The guard keeps one request in flight and aborts the one it supersedes;
 * `shouldReload` refuses only a timelapse frame during playback; and
 * `markPaused` puts one band on the sheet, marks it busy, and makes the
 * band the peek only when the sheet has no other peek row.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

document.body.innerHTML = `
  <div id="sheet"><button type="button" data-sheet-handle></button></div>
  <template id="sheet-playback-band-template"><div role="status" class="px-2 py-2"><p>Playback in progress</p></div></template>
`;
await import('../../static/js/i18n_strings.js');
await import('../../static/js/sheet_playback_core.js');

const core = window.pwaSheetPlayback;
const sheet = /** @type {HTMLElement} */ (document.getElementById('sheet'));
const bands = () => sheet.querySelectorAll('[data-sheet-playback-band]');

beforeEach(() => {
  core.markPaused(sheet, false);
  sheet.querySelectorAll(':scope > :not([data-sheet-handle])').forEach((el) => el.remove());
});

afterEach(() => {
  delete window.snowdeskMapState;
});

describe('createGuard', () => {
  it('aborts the previous request when the next begins', () => {
    const guard = core.createGuard();
    const first = guard.begin();
    const second = guard.begin();
    expect(first.signal.aborted).toBe(true);
    expect(first.isCurrent()).toBe(false);
    expect(second.signal.aborted).toBe(false);
    expect(second.isCurrent()).toBe(true);
  });

  it('invalidate aborts the request in flight and makes it stale', () => {
    const guard = core.createGuard();
    const ask = guard.begin();
    guard.invalidate();
    expect(ask.signal.aborted).toBe(true);
    expect(ask.isCurrent()).toBe(false);
  });

  it('still tells a stale answer apart with no AbortController', () => {
    const original = globalThis.AbortController;
    // @ts-ignore — a runtime without it.
    delete globalThis.AbortController;
    try {
      const guard = core.createGuard();
      const ask = guard.begin();
      expect(ask.signal).toBeUndefined();
      guard.invalidate();
      expect(ask.isCurrent()).toBe(false);
    } finally {
      globalThis.AbortController = original;
    }
  });
});

describe('shouldReload', () => {
  it.each([
    ['timelapse', true, false],
    ['timelapse', false, true],
    ['scrubber', true, true],
    ['scrubber', false, true],
    [undefined, true, true],
    [undefined, false, true],
  ])('source %s, playing %s → %s', (source, playing, expected) => {
    expect(core.shouldReload(source, playing)).toBe(expected);
  });
});

describe('isPlaying and isAbort', () => {
  it('reads the map state, false when it is absent', () => {
    expect(core.isPlaying()).toBe(false);
    window.snowdeskMapState = { isPlaying: true };
    expect(core.isPlaying()).toBe(true);
  });

  it('recognises an abort and nothing else', () => {
    expect(core.isAbort(new DOMException('aborted', 'AbortError'))).toBe(true);
    expect(core.isAbort(new Error('offline'))).toBe(false);
    expect(core.isAbort(null)).toBe(false);
  });
});

describe('markPaused', () => {
  it('inserts one band after the handle and marks the sheet busy', () => {
    sheet.insertAdjacentHTML('beforeend', '<div data-sheet-peek>Verbier</div>');
    core.markPaused(sheet, true);
    core.markPaused(sheet, true);
    expect(bands()).toHaveLength(1);
    expect(sheet.children[1].hasAttribute('data-sheet-playback-band')).toBe(true);
    expect(bands()[0].textContent).toContain('Playback in progress');
    expect(sheet.hasAttribute('data-sheet-paused')).toBe(true);
    expect(sheet.getAttribute('aria-busy')).toBe('true');
  });

  it('removes the band and the marks', () => {
    core.markPaused(sheet, true);
    core.markPaused(sheet, false);
    expect(bands()).toHaveLength(0);
    expect(sheet.hasAttribute('data-sheet-paused')).toBe(false);
    expect(sheet.hasAttribute('aria-busy')).toBe(false);
  });

  it('makes the band the peek only when the sheet has no other', () => {
    core.markPaused(sheet, true);
    expect(bands()[0].hasAttribute('data-sheet-peek')).toBe(true);
    sheet.insertAdjacentHTML('beforeend', '<div data-sheet-peek>Verbier</div>');
    core.markPaused(sheet, true);
    expect(bands()[0].hasAttribute('data-sheet-peek')).toBe(false);
  });

  it('falls back to a plain line when the page has no template', () => {
    const template = document.getElementById('sheet-playback-band-template');
    template.remove();
    try {
      core.markPaused(sheet, true);
      expect(bands()[0].textContent).toBe('Playback in progress');
    } finally {
      document.body.appendChild(template);
    }
  });
});
