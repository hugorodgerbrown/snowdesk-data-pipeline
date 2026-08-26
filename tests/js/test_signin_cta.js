/*
 * tests/js/test_signin_cta.js — the shared sign-in gate (SNOW-672).
 *
 * Three map surfaces gate on being signed in, and each used to build its own
 * copy of the same prompt-plus-pill. The copies were identical when they were
 * written; nothing kept them that way. These tests assert the two properties
 * that made consolidating them worth doing:
 *
 *   1. The DOM builder and the class strings report the same treatment, so
 *      report.js's markup string cannot drift from the nodes favourites.js
 *      and routes.js build.
 *   2. A missing sign-in URL still renders the prompt. That branch is real —
 *      the surfaces render the gate whenever the visitor is ineligible, and
 *      a template that has nowhere to send them must not produce a link to
 *      nowhere.
 *
 * The module is imported by tests/js/setup.js, matching the page, where it
 * loads ahead of the three surfaces that call it.
 */

import { describe, expect, it } from 'vitest';

const PROMPT = 'Sign in to save favourites.';
const CTA = 'Sign in';
const URL = '/accounts/sign-in/';

describe('window.snowdeskSigninCta', () => {
  it('builds a prompt and a pill link to the sign-in URL', () => {
    const el = window.snowdeskSigninCta.build(URL, PROMPT, CTA);

    const p = el.querySelector('p');
    expect(p.textContent).toBe(PROMPT);

    const a = el.querySelector('a');
    expect(a.getAttribute('href')).toBe(URL);
    expect(a.textContent).toBe(CTA);
  });

  it('renders the prompt alone when there is no sign-in URL', () => {
    for (const missing of [null, '']) {
      const el = window.snowdeskSigninCta.build(missing, PROMPT, CTA);
      expect(el.querySelector('p').textContent).toBe(PROMPT);
      expect(el.querySelector('a')).toBeNull();
    }
  });

  it('gives the built nodes the same classes it publishes to markup callers', () => {
    // report.js assigns its gate with innerHTML, so it interpolates these two
    // strings instead of using build(). If the constants and the nodes ever
    // disagree, that surface silently renders a different pill.
    const el = window.snowdeskSigninCta.build(URL, PROMPT, CTA);
    expect(el.querySelector('p').className).toBe(window.snowdeskSigninCta.PROMPT_CLASS);
    expect(el.querySelector('a').className).toBe(window.snowdeskSigninCta.LINK_CLASS);
  });

  it('escapes nothing itself — the text is set as text, not markup', () => {
    // The DOM-not-markup discipline the calling modules rely on: a translated
    // string containing a tag must land as characters, not as an element.
    const el = window.snowdeskSigninCta.build(URL, '<b>bold</b>', CTA);
    expect(el.querySelector('p').querySelector('b')).toBeNull();
    expect(el.querySelector('p').textContent).toBe('<b>bold</b>');
  });
});
